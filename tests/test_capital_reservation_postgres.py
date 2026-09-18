from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.capital_reservation import (
    CapitalReservationRequest,
    CapitalReservationState,
    InsufficientCapital,
    PostgresCapitalReservationPort,
)

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="set CRIPTA_TRADE_LIFECYCLE_TEST_DSN to an explicitly disposable PostgreSQL DB",
)


def _seed_attempts(prefix: str, *, count: int = 3) -> dict[str, str]:
    assert DSN is not None
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    strategy_id = f"{prefix}-strategy"
    strategy_version = "1"
    strategy_fp = f"{prefix}-strategy-fp"
    activation_id = f"{prefix}-activation"
    entry_fp = f"{prefix}-entry-fp"
    with psycopg.connect(DSN) as connection:
        connection.execute(
            """INSERT INTO strategy_entry.strategy_cards(
                   strategy_id,strategy_version,strategy_config_fingerprint,
                   name,description,card_json,approved_at,approved_source
               ) VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",
            (
                strategy_id,
                strategy_version,
                strategy_fp,
                "test",
                "capital reservation integration test",
                (
                    '{"strategy_id":"'
                    + strategy_id
                    + '","strategy_version":"'
                    + strategy_version
                    + '","strategy_config_fingerprint":"'
                    + strategy_fp
                    + '"}'
                ),
                now,
                "test",
            ),
        )
        connection.execute(
            """INSERT INTO strategy_entry.strategy_activations(
                   activation_id,strategy_id,strategy_version,
                   strategy_config_fingerprint,enabled,enabled_at,disabled_at,
                   scope,operator,source,change_reason
               ) VALUES(%s,%s,%s,%s,true,%s,NULL,'{}'::jsonb,'test','test','')""",
            (activation_id, strategy_id, strategy_version, strategy_fp, now),
        )
        connection.execute(
            """INSERT INTO strategy_entry.entry_plans(
                   entry_plan_fingerprint,strategy_id,strategy_version,
                   strategy_config_fingerprint,entry_plan_version,plan_json
               ) VALUES(%s,%s,%s,%s,'1',%s::jsonb)""",
            (
                entry_fp,
                strategy_id,
                strategy_version,
                strategy_fp,
                '{"entry_plan_fingerprint":"' + entry_fp + '"}',
            ),
        )
        for index in range(count):
            signal_id = f"{prefix}-signal-{index}"
            attempt_id = f"{prefix}-attempt-{index}"
            connection.execute(
                """INSERT INTO strategy_entry.strategy_signals(
                       signal_id,strategy_id,strategy_version,
                       strategy_config_fingerprint,entry_plan_fingerprint,
                       strategy_activation_id,symbol,direction,detected_at,
                       fact_id,source_refs,payload
                   ) VALUES(%s,%s,%s,%s,%s,%s,'UNIUSDT','LONG',%s,%s,'[]'::jsonb,'{}'::jsonb)""",
                (
                    signal_id,
                    strategy_id,
                    strategy_version,
                    strategy_fp,
                    entry_fp,
                    activation_id,
                    now,
                    f"{prefix}-fact-{index}",
                ),
            )
            connection.execute(
                """INSERT INTO strategy_entry.strategy_attempts(
                       strategy_attempt_id,signal_id,strategy_id,strategy_version,
                       strategy_config_fingerprint,entry_plan_fingerprint,
                       strategy_activation_id,created_at,payload
                   ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'{}'::jsonb)""",
                (
                    attempt_id,
                    signal_id,
                    strategy_id,
                    strategy_version,
                    strategy_fp,
                    entry_fp,
                    activation_id,
                    now,
                ),
            )
    return {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "strategy_fp": strategy_fp,
        "entry_fp": entry_fp,
    }


def _request(prefix: str, index: int, identity: dict[str, str]) -> CapitalReservationRequest:
    observed_at = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    return CapitalReservationRequest(
        account_ref=f"{prefix}-account",
        strategy_id=identity["strategy_id"],
        strategy_version=identity["strategy_version"],
        strategy_config_fingerprint=identity["strategy_fp"],
        entry_plan_fingerprint=identity["entry_fp"],
        signal_id=f"{prefix}-signal-{index}",
        strategy_attempt_id=f"{prefix}-attempt-{index}",
        requested_amount=Decimal("10"),
        amount_currency="USDT",
        capacity_snapshot_id=f"{prefix}-capacity",
        capacity_observed_at=observed_at,
        capacity_available=Decimal("10"),
        requested_at=observed_at,
    )


def test_two_connections_cannot_overallocate_one_capacity_snapshot() -> None:
    assert DSN is not None
    prefix = "race-" + uuid4().hex
    identity = _seed_attempts(prefix, count=2)
    barrier = Barrier(2)

    def reserve(index: int) -> str:
        with psycopg.connect(DSN) as connection:
            port = PostgresCapitalReservationPort(connection)
            barrier.wait(timeout=10)
            try:
                reservation = port.reserve(_request(prefix, index, identity))
            except InsufficientCapital:
                return "INSUFFICIENT"
            return reservation.state.value

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(reserve, (0, 1)))

    assert outcomes == ["INSUFFICIENT", "RESERVED"]
    with psycopg.connect(DSN) as connection:
        rows = connection.execute(
            """SELECT state,count(*)
                 FROM runtime.capital_reservations
                WHERE account_ref=%s
                GROUP BY state
                ORDER BY state""",
            (f"{prefix}-account",),
        ).fetchall()
    assert rows == [("RESERVED", 1)]


def test_same_attempt_is_idempotent() -> None:
    assert DSN is not None
    prefix = "idem-" + uuid4().hex
    identity = _seed_attempts(prefix, count=1)
    request = _request(prefix, 0, identity)
    with psycopg.connect(DSN) as connection:
        port = PostgresCapitalReservationPort(connection)
        first = port.reserve(request)
        second = port.reserve(request)
    assert first.reservation_id == second.reservation_id
    assert second.state is CapitalReservationState.RESERVED


def test_reconciliation_required_keeps_capital_unavailable() -> None:
    assert DSN is not None
    prefix = "reconcile-" + uuid4().hex
    identity = _seed_attempts(prefix, count=2)
    with psycopg.connect(DSN) as connection:
        port = PostgresCapitalReservationPort(connection)
        first = port.reserve(_request(prefix, 0, identity))
        moved = port.transition(
            first.reservation_id,
            state=CapitalReservationState.RECONCILIATION_REQUIRED,
        )
        assert moved.state is CapitalReservationState.RECONCILIATION_REQUIRED
        with pytest.raises(InsufficientCapital):
            port.reserve(_request(prefix, 1, identity))


def test_released_reservation_no_longer_blocks_capacity() -> None:
    assert DSN is not None
    prefix = "release-" + uuid4().hex
    identity = _seed_attempts(prefix, count=2)
    with psycopg.connect(DSN) as connection:
        port = PostgresCapitalReservationPort(connection)
        first = port.reserve(_request(prefix, 0, identity))
        released = port.transition(
            first.reservation_id,
            state=CapitalReservationState.RELEASED,
        )
        assert released.state is CapitalReservationState.RELEASED
        second = port.reserve(_request(prefix, 1, identity))
        assert second.state is CapitalReservationState.RESERVED
