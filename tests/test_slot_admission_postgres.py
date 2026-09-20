from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.capital_reservation import InsufficientCapital
from bybit_workbench.entry_admission import (
    EntryAdmissionRequest,
    PositionModeMismatch,
    PositionModeStateUnavailable,
    PostgresEntryAdmissionPort,
    SlotOwnershipConflict,
)

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="set CRIPTA_TRADE_LIFECYCLE_TEST_DSN to an explicitly disposable PostgreSQL DB",
)

NOW = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
SYMBOL = "UNIUSDT"


def _seed_attempts(prefix: str, *, count: int = 2) -> dict[str, str]:
    assert DSN is not None
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
                "slot test",
                "atomic slot admission integration test",
                "{}",
                NOW,
                "test",
            ),
        )
        connection.execute(
            """INSERT INTO strategy_entry.strategy_activations(
                   activation_id,strategy_id,strategy_version,
                   strategy_config_fingerprint,enabled,enabled_at,disabled_at,
                   scope,operator,source,change_reason
               ) VALUES(%s,%s,%s,%s,true,%s,NULL,'{}'::jsonb,'test','test','')""",
            (activation_id, strategy_id, strategy_version, strategy_fp, NOW),
        )
        connection.execute(
            """INSERT INTO strategy_entry.entry_plans(
                   entry_plan_fingerprint,strategy_id,strategy_version,
                   strategy_config_fingerprint,entry_plan_version,plan_json
               ) VALUES(%s,%s,%s,%s,'1',%s::jsonb)""",
            (entry_fp, strategy_id, strategy_version, strategy_fp, "{}"),
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
                   ) VALUES(%s,%s,%s,%s,%s,%s,%s,'LONG',%s,%s,'[]'::jsonb,'{}'::jsonb)""",
                (
                    signal_id,
                    strategy_id,
                    strategy_version,
                    strategy_fp,
                    entry_fp,
                    activation_id,
                    SYMBOL,
                    NOW,
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
                    NOW,
                ),
            )
    return {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "strategy_fp": strategy_fp,
        "entry_fp": entry_fp,
        "account_ref": f"{prefix}-account",
    }


def _seed_mode(
    prefix: str,
    identity: dict[str, str],
    *,
    mode: str = "ONE_WAY",
    position_idx: int | None = 0,
    observed_at: datetime = NOW,
    fresh_until: datetime | None = None,
) -> str:
    assert DSN is not None
    state_ref = f"{prefix}-pmode"
    with psycopg.connect(DSN) as connection:
        connection.execute(
            """INSERT INTO runtime.position_mode_states(
                   position_mode_state_ref,exchange,account_ref,product_category,
                   instrument,position_mode,position_idx,observed_at,received_at,
                   fresh_until,provenance
               ) VALUES(%s,'BYBIT',%s,'LINEAR',%s,%s,%s,%s,%s,%s,'{}'::jsonb)""",
            (
                state_ref,
                identity["account_ref"],
                SYMBOL,
                mode,
                position_idx,
                observed_at,
                observed_at,
                fresh_until or (observed_at + timedelta(minutes=5)),
            ),
        )
    return state_ref


def _request(
    prefix: str,
    index: int,
    identity: dict[str, str],
    *,
    capacity_available: Decimal = Decimal("100"),
) -> EntryAdmissionRequest:
    return EntryAdmissionRequest(
        account_ref=identity["account_ref"],
        exchange_position_key=(
            f"BYBIT:{identity['account_ref']}:LINEAR:USDT:{SYMBOL}:0"
        ),
        symbol=SYMBOL,
        direction="LONG",
        strategy_id=identity["strategy_id"],
        strategy_version=identity["strategy_version"],
        strategy_config_fingerprint=identity["strategy_fp"],
        entry_plan_fingerprint=identity["entry_fp"],
        signal_id=f"{prefix}-signal-{index}",
        strategy_attempt_id=f"{prefix}-attempt-{index}",
        requested_amount=Decimal("10"),
        amount_currency="USDT",
        capacity_snapshot_id=f"{prefix}-capacity",
        capacity_observed_at=NOW,
        capacity_available=capacity_available,
        requested_at=NOW,
        pre_dispatch_expires_at=NOW + timedelta(seconds=30),
    )


def test_atomic_success_persists_claim_and_reservation_together() -> None:
    assert DSN is not None
    prefix = "slot-success-" + uuid4().hex
    identity = _seed_attempts(prefix, count=1)
    _seed_mode(prefix, identity)
    with psycopg.connect(DSN) as connection:
        receipt = PostgresEntryAdmissionPort(connection).admit(
            _request(prefix, 0, identity)
        )
        row = connection.execute(
            """SELECT c.claim_state,c.capital_reservation_id,r.state,
                      c.position_mode_state_ref
                 FROM runtime.exchange_position_slot_claims c
                 JOIN runtime.capital_reservations r
                   ON r.reservation_id=c.capital_reservation_id
                WHERE c.exchange_position_slot_claim_id=%s""",
            (receipt.exchange_position_slot_claim_id,),
        ).fetchone()
    assert row is not None
    assert row[0] == "CLAIMED"
    assert row[1] == receipt.capital_reservation.reservation_id
    assert row[2] == "RESERVED"
    assert row[3] == receipt.position_mode_state_ref


def test_insufficient_capital_rolls_back_slot_claim_in_same_admission() -> None:
    assert DSN is not None
    prefix = "slot-insufficient-" + uuid4().hex
    identity = _seed_attempts(prefix, count=1)
    _seed_mode(prefix, identity)
    with psycopg.connect(DSN) as connection:
        with pytest.raises(InsufficientCapital):
            PostgresEntryAdmissionPort(connection).admit(
                _request(prefix, 0, identity, capacity_available=Decimal("5"))
            )
        claims = connection.execute(
            """SELECT count(*) FROM runtime.exchange_position_slot_claims
                WHERE strategy_attempt_id=%s""",
            (f"{prefix}-attempt-0",),
        ).fetchone()[0]
        reservations = connection.execute(
            """SELECT count(*) FROM runtime.capital_reservations
                WHERE strategy_attempt_id=%s""",
            (f"{prefix}-attempt-0",),
        ).fetchone()[0]
    assert claims == 0
    assert reservations == 0


def test_two_concurrent_attempts_cannot_own_one_physical_slot() -> None:
    assert DSN is not None
    prefix = "slot-race-" + uuid4().hex
    identity = _seed_attempts(prefix, count=2)
    _seed_mode(prefix, identity)
    barrier = Barrier(2)

    def admit(index: int) -> str:
        with psycopg.connect(DSN) as connection:
            barrier.wait(timeout=10)
            try:
                PostgresEntryAdmissionPort(connection).admit(
                    _request(prefix, index, identity)
                )
            except SlotOwnershipConflict:
                return "CONFLICT"
            return "CLAIMED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(admit, (0, 1)))

    assert outcomes == ["CLAIMED", "CONFLICT"]
    with psycopg.connect(DSN) as connection:
        claims = connection.execute(
            """SELECT count(*) FROM runtime.exchange_position_slot_claims
                WHERE account_ref=%s
                  AND claim_state IN ('CLAIMED','BOUND','RECONCILIATION_REQUIRED')""",
            (identity["account_ref"],),
        ).fetchone()[0]
        reservations = connection.execute(
            """SELECT count(*) FROM runtime.capital_reservations
                WHERE account_ref=%s AND state='RESERVED'""",
            (identity["account_ref"],),
        ).fetchone()[0]
    assert claims == 1
    assert reservations == 1


def test_stale_position_mode_creates_no_claim_or_reservation() -> None:
    assert DSN is not None
    prefix = "slot-stale-" + uuid4().hex
    identity = _seed_attempts(prefix, count=1)
    _seed_mode(
        prefix,
        identity,
        observed_at=NOW - timedelta(minutes=10),
        fresh_until=NOW - timedelta(minutes=1),
    )
    with psycopg.connect(DSN) as connection:
        with pytest.raises(PositionModeStateUnavailable):
            PostgresEntryAdmissionPort(connection).admit(_request(prefix, 0, identity))
        claim_count = connection.execute(
            "SELECT count(*) FROM runtime.exchange_position_slot_claims WHERE account_ref=%s",
            (identity["account_ref"],),
        ).fetchone()[0]
        reservation_count = connection.execute(
            "SELECT count(*) FROM runtime.capital_reservations WHERE account_ref=%s",
            (identity["account_ref"],),
        ).fetchone()[0]
    assert claim_count == 0
    assert reservation_count == 0


def test_incompatible_position_mode_creates_no_claim_or_reservation() -> None:
    assert DSN is not None
    prefix = "slot-mode-" + uuid4().hex
    identity = _seed_attempts(prefix, count=1)
    _seed_mode(prefix, identity, mode="HEDGE", position_idx=1)
    with psycopg.connect(DSN) as connection:
        with pytest.raises(PositionModeMismatch):
            PostgresEntryAdmissionPort(connection).admit(_request(prefix, 0, identity))
        claim_count = connection.execute(
            "SELECT count(*) FROM runtime.exchange_position_slot_claims WHERE account_ref=%s",
            (identity["account_ref"],),
        ).fetchone()[0]
        reservation_count = connection.execute(
            "SELECT count(*) FROM runtime.capital_reservations WHERE account_ref=%s",
            (identity["account_ref"],),
        ).fetchone()[0]
    assert claim_count == 0
    assert reservation_count == 0


def test_same_attempt_is_idempotent_with_same_claim_and_reservation() -> None:
    assert DSN is not None
    prefix = "slot-idem-" + uuid4().hex
    identity = _seed_attempts(prefix, count=1)
    _seed_mode(prefix, identity)
    request = _request(prefix, 0, identity)
    with psycopg.connect(DSN) as connection:
        port = PostgresEntryAdmissionPort(connection)
        first = port.admit(request)
        second = port.admit(request)
    assert first.exchange_position_slot_claim_id == second.exchange_position_slot_claim_id
    assert first.capital_reservation.reservation_id == second.capital_reservation.reservation_id


def test_runtime_role_has_minimal_new_schema_privileges() -> None:
    assert DSN is not None
    expected = {
        "runtime.position_mode_states": (True, True, False, False),
        "runtime.exchange_position_slot_claims": (True, True, True, False),
        "runtime.lifecycle_fault_deliveries": (True, True, True, False),
        "strategy_entry.execution_request_state_events": (True, True, False, False),
        "control.live_arm_evidence": (True, True, False, False),
        "control.live_arm_sessions": (True, True, True, False),
    }
    with psycopg.connect(DSN) as connection:
        rows = connection.execute(
            """SELECT table_schema||'.'||table_name,
                      has_table_privilege(
                          'cripta',
                          quote_ident(table_schema)||'.'||quote_ident(table_name),
                          'SELECT'
                      ),
                      has_table_privilege(
                          'cripta',
                          quote_ident(table_schema)||'.'||quote_ident(table_name),
                          'INSERT'
                      ),
                      has_table_privilege(
                          'cripta',
                          quote_ident(table_schema)||'.'||quote_ident(table_name),
                          'UPDATE'
                      ),
                      has_table_privilege(
                          'cripta',
                          quote_ident(table_schema)||'.'||quote_ident(table_name),
                          'DELETE'
                      )
                 FROM information_schema.tables
                WHERE (table_schema,table_name) IN (
                    ('runtime','position_mode_states'),
                    ('runtime','exchange_position_slot_claims'),
                    ('runtime','lifecycle_fault_deliveries'),
                    ('strategy_entry','execution_request_state_events'),
                    ('control','live_arm_evidence'),
                    ('control','live_arm_sessions')
                )"""
        ).fetchall()
    actual = {
        str(row[0]): (bool(row[1]), bool(row[2]), bool(row[3]), bool(row[4]))
        for row in rows
    }
    assert actual == expected
