from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
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


def _seed_attempts(
    prefix: str, *, count: int = 3, unified_r1: bool = False,
    strategy_id_override: str | None = None,
    strategy_version_override: str | None = None,
) -> dict[str, str]:
    assert DSN is not None
    now = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
    strategy_id = (
        strategy_id_override
        if strategy_id_override is not None
        else ("r1_five_symbols" if unified_r1 else f"{prefix}-strategy")
    )
    strategy_version = (
        strategy_version_override
        if strategy_version_override is not None
        else ("1.0-five-symbols-20261021" if unified_r1 else "1")
    )
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
        connection.execute(
            """INSERT INTO identity.venues(venue_id,venue_kind,display_name,status)
               VALUES('venue-bybit','CEX','Bybit','ACTIVE')
               ON CONFLICT (venue_id) DO NOTHING"""
        )
        connection.execute(
            """INSERT INTO identity.trading_accounts(
                   trading_account_id,venue_id,account_kind,environment,status)
               VALUES(%s,'venue-bybit','UNIFIED','MAINNET','ACTIVE')""",
            (f"{prefix}-ta",),
        )
        connection.execute(
            """INSERT INTO strategy_entry.strategy_execution_bindings(
                   strategy_execution_binding_id,strategy_activation_id,
                   execution_leg,execution_role,trading_account_id,
                   allocation_mode,binding_state,effective_from,
                   operator,source,reason)
               VALUES(%s,%s,'PRIMARY','PRIMARY',%s,
                      'DEDICATED_STRATEGY','ACTIVE',%s,
                      'test','test','integration fixture')""",
            (f"{prefix}-binding",activation_id,f"{prefix}-ta",now),
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
        pre_dispatch_expires_at=observed_at.replace(second=30),
        strategy_execution_binding_id=f"{prefix}-binding",
        execution_leg="PRIMARY",
        trading_account_id=f"{prefix}-ta",
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


def test_unified_r1_parallel_cap_consumed_and_confirmed_release() -> None:
    """Five requests fit 50 USDT; the sixth races but must fail."""
    assert DSN is not None
    prefix = "r1-cap-" + uuid4().hex
    identity = _seed_attempts(prefix, count=7, unified_r1=True)
    barrier = Barrier(6)

    def attempt(index: int) -> tuple[int, str]:
        request = replace(
            _request(prefix, index, identity),
            capacity_available=Decimal("100"),
        )
        with psycopg.connect(DSN) as connection:
            port = PostgresCapitalReservationPort(connection)
            barrier.wait(timeout=15)
            try:
                row = port.reserve(request)
            except InsufficientCapital:
                return index, "BLOCKED"
            return index, row.state.value

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(attempt, range(6)))
    accepted = [i for i, state in results if state == "RESERVED"]
    blocked = [i for i, state in results if state == "BLOCKED"]
    assert len(accepted) == 5
    assert len(blocked) == 1

    with psycopg.connect(DSN) as connection:
        current = connection.execute(
            """SELECT coalesce(sum(requested_amount),0), count(*)
                 FROM runtime.capital_reservations
                WHERE strategy_id=%s AND state<>'RELEASED'""",
            (identity["strategy_id"],),
        ).fetchone()
        assert current == (Decimal("50"), 5)
        port = PostgresCapitalReservationPort(connection)
        first = replace(
            _request(prefix, accepted[0], identity),
            capacity_available=Decimal("100"),
        )
        assert port.reserve(first).state is CapitalReservationState.RESERVED

        port.transition(
            first.reservation_id,
            state=CapitalReservationState.DISPATCHED,
        )
        port.transition(
            first.reservation_id,
            state=CapitalReservationState.PENDING_EXCHANGE_REFLECTION,
            exchange_commitment_at=first.requested_at,
        )
        port.transition(
            first.reservation_id,
            state=CapitalReservationState.CONSUMED,
            exchange_commitment_at=first.requested_at,
        )
        seventh = replace(
            _request(prefix, 6, identity),
            capacity_available=Decimal("100"),
        )
        with pytest.raises(InsufficientCapital):
            port.reserve(seventh)

        second = replace(
            _request(prefix, accepted[1], identity),
            capacity_available=Decimal("100"),
        )
        port.transition(
            second.reservation_id,
            state=CapitalReservationState.RELEASED,
        )
        assert port.reserve(seventh).state is CapitalReservationState.RESERVED

        rejected = replace(
            _request(prefix, blocked[0], identity),
            requested_amount=Decimal("11"),
            capacity_available=Decimal("100"),
        )
        with pytest.raises(ValueError, match="R1_UNIFIED_CAPITAL_POLICY_MISMATCH"):
            port.reserve(rejected)


@pytest.mark.parametrize("strategy_id,parallel,expected_limit", (
    ("p90l90_legacy_main_ltc_inj", 3, 2),
    ("p90l90_legacy_sub_dot_arb_apt", 4, 3),
))
def test_p90_real_account_cap_atomic_pg16(
    strategy_id: str, parallel: int, expected_limit: int,
) -> None:
    """Exact Strategy pending+open ceiling is enforced under the account lock."""
    assert DSN is not None
    from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
        VERSION,
    )
    prefix = "p90-real-" + uuid4().hex
    identity = _seed_attempts(
        prefix, count=parallel+1,
        strategy_id_override=strategy_id,
        strategy_version_override=VERSION,
    )
    barrier = Barrier(parallel)

    def reserve(index: int) -> tuple[int, str]:
        request = replace(
            _request(prefix, index, identity),
            capacity_available=Decimal("100"),
        )
        with psycopg.connect(DSN) as connection:
            barrier.wait(timeout=15)
            try:
                row = PostgresCapitalReservationPort(connection).reserve(request)
            except InsufficientCapital:
                return index, "BLOCKED"
            return index, row.state.value

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        results = list(pool.map(reserve, range(parallel)))
    accepted = [index for index, state in results if state == "RESERVED"]
    blocked = [index for index, state in results if state == "BLOCKED"]
    assert len(accepted) == expected_limit
    assert len(blocked) == parallel - expected_limit

    with psycopg.connect(DSN) as connection:
        port = PostgresCapitalReservationPort(connection)
        assert connection.execute(
            """SELECT sum(requested_amount), count(*)
                 FROM runtime.capital_reservations
                WHERE strategy_id=%s AND state<>'RELEASED'""",
            (strategy_id,),
        ).fetchone() == (Decimal(str(expected_limit*10)), expected_limit)

        first = replace(
            _request(prefix, accepted[0], identity),
            capacity_available=Decimal("100"),
        )
        assert port.reserve(first).state is CapitalReservationState.RESERVED
        port.transition(first.reservation_id, state=CapitalReservationState.DISPATCHED)
        port.transition(
            first.reservation_id,
            state=CapitalReservationState.PENDING_EXCHANGE_REFLECTION,
            exchange_commitment_at=first.requested_at,
        )
        port.transition(
            first.reservation_id,
            state=CapitalReservationState.CONSUMED,
            exchange_commitment_at=first.requested_at,
        )
        pending = replace(
            _request(prefix, parallel, identity),
            capacity_available=Decimal("100"),
        )
        with pytest.raises(InsufficientCapital):
            port.reserve(pending)
        port.transition(
            first.reservation_id, state=CapitalReservationState.RELEASED,
        )
        assert port.reserve(pending).state is CapitalReservationState.RESERVED

        # Do not silently change the size to satisfy exchange min notional.
        invalid = replace(
            _request(prefix, blocked[0], identity),
            requested_amount=Decimal("11"),
            capacity_available=Decimal("100"),
        )
        with pytest.raises(ValueError, match="P90_REAL_CAPITAL_POLICY_MISMATCH"):
            port.reserve(invalid)
