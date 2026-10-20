from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.capital_reservation import InsufficientCapital
from bybit_workbench.exchange_position_identity import exchange_position_key
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
            (f"{prefix}-binding",activation_id,f"{prefix}-ta",NOW),
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
                   fresh_until,provenance,trading_account_id,identity_status
               ) VALUES(%s,'BYBIT',%s,'LINEAR',%s,%s,%s,%s,%s,%s,'{}'::jsonb,
                        %s,'PROVEN')""",
            (
                state_ref,
                identity["account_ref"],
                SYMBOL,
                mode,
                position_idx,
                observed_at,
                observed_at,
                fresh_until or (observed_at + timedelta(minutes=5)),
                f"{prefix}-ta",
            ),
        )
        connection.execute(
            """INSERT INTO runtime.account_state_generations(
                   generation_id,account_ref,reason,state,started_at,completed_at,
                   account_type,total_equity,wallet_balance,available_balance,
                   positions_count,active_orders_count,position_mode_refs,wallet_payload,
                   error,trading_account_id,identity_status
               ) VALUES(%s,%s,'test','COMPLETE',%s,%s,'UNIFIED',100,100,100,
                        0,0,%s::jsonb,'{}'::jsonb,'',%s,'PROVEN')""",
            (
                f"{prefix}-generation",
                identity["account_ref"],
                observed_at,
                observed_at,
                json.dumps({SYMBOL: state_ref}),
                f"{prefix}-ta",
            ),
        )
    return state_ref


def _request(
    prefix: str,
    index: int,
    identity: dict[str, str],
    *,
    capacity_available: Decimal = Decimal("100"),
    requested_amount: Decimal = Decimal("10"),
) -> EntryAdmissionRequest:
    return EntryAdmissionRequest(
        account_ref=identity["account_ref"],
        exchange_position_key=exchange_position_key(f"{prefix}-ta", SYMBOL, 0),
        symbol=SYMBOL,
        direction="LONG",
        strategy_id=identity["strategy_id"],
        strategy_version=identity["strategy_version"],
        strategy_config_fingerprint=identity["strategy_fp"],
        entry_plan_fingerprint=identity["entry_fp"],
        signal_id=f"{prefix}-signal-{index}",
        strategy_attempt_id=f"{prefix}-attempt-{index}",
        requested_amount=requested_amount,
        amount_currency="USDT",
        capacity_snapshot_id=f"{prefix}-generation",
        capacity_observed_at=NOW,
        capacity_available=capacity_available,
        requested_at=NOW,
        pre_dispatch_expires_at=NOW + timedelta(seconds=30),
        account_state_generation_id=f"{prefix}-generation",
        strategy_activation_id=f"{prefix}-activation",
        strategy_execution_binding_id=f"{prefix}-binding",
        execution_leg="PRIMARY",
        trading_account_id=f"{prefix}-ta",
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
                _request(prefix, 0, identity, requested_amount=Decimal("150"))
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


def test_outer_transaction_can_persist_attempt_after_admission() -> None:
    """Regression: real engine admits before StrategyEntryStore writes the attempt."""
    assert DSN is not None
    prefix = "slot-deferred-attempt-" + uuid4().hex
    identity = _seed_attempts(prefix, count=1)
    _seed_mode(prefix, identity)
    attempt_id = f"{prefix}-attempt-1"
    signal_id = f"{prefix}-signal-1"

    request = _request(prefix, 1, identity)
    with psycopg.connect(DSN) as connection, connection.transaction():
        receipt = PostgresEntryAdmissionPort(connection).admit(request)
        assert receipt.exchange_position_slot_claim_id
        missing = connection.execute(
            "SELECT 1 FROM strategy_entry.strategy_attempts WHERE strategy_attempt_id=%s",
            (attempt_id,),
        ).fetchone()
        assert missing is None
        connection.execute(
            """INSERT INTO strategy_entry.strategy_signals(
                       signal_id,strategy_id,strategy_version,
                       strategy_config_fingerprint,entry_plan_fingerprint,
                       strategy_activation_id,symbol,direction,detected_at,
                       fact_id,source_refs,payload
                   ) VALUES(%s,%s,%s,%s,%s,%s,%s,'LONG',%s,%s,'[]'::jsonb,'{}'::jsonb)""",
            (
                signal_id,
                identity["strategy_id"],
                identity["strategy_version"],
                identity["strategy_fp"],
                identity["entry_fp"],
                f"{prefix}-activation",
                SYMBOL,
                NOW,
                f"{prefix}-fact-1",
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
                identity["strategy_id"],
                identity["strategy_version"],
                identity["strategy_fp"],
                identity["entry_fp"],
                f"{prefix}-activation",
                NOW,
            ),
        )

    with psycopg.connect(DSN) as connection:
        row = connection.execute(
            """SELECT c.claim_state,r.state
                 FROM runtime.exchange_position_slot_claims c
                 JOIN runtime.capital_reservations r
                   ON r.reservation_id=c.capital_reservation_id
                WHERE c.strategy_attempt_id=%s""",
            (attempt_id,),
        ).fetchone()
    assert row == ("CLAIMED", "RESERVED")


def test_runtime_role_has_minimal_new_schema_privileges() -> None:
    assert DSN is not None
    expected = {
        "runtime.position_mode_states": (True, True, False, False),
        "runtime.exchange_position_slot_claims": (True, True, True, False),
        "strategy_entry.strategy_execution_bindings": (True, False, False, False),
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
                    ('strategy_entry','strategy_execution_bindings'),
                    ('control','live_arm_evidence'),
                    ('control','live_arm_sessions')
                )"""
        ).fetchall()
    actual = {
        str(row[0]): (bool(row[1]), bool(row[2]), bool(row[3]), bool(row[4]))
        for row in rows
    }
    assert actual == expected
    with psycopg.connect(DSN) as connection:
        function_rights = connection.execute(
            """SELECT has_function_privilege(
                         'cripta',
                         'strategy_entry.lock_strategy_execution_binding(text)',
                         'EXECUTE'
                       ),
                      pg_has_role(
                         'cripta',
                         'cripta_entry_binding_locker',
                         'MEMBER'
                       )"""
        ).fetchone()
    assert function_rights == (True, False)