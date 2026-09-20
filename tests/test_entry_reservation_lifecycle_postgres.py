from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from bybit_workbench.entry_reservation_lifecycle import (
    finalize_failed_entry_command_reservation,
    mark_entry_order_acknowledged,
    resolve_cancelled_entry_reservation_after_reconcile,
)
from bybit_workbench.live_arm_readiness import (
    REQUIRED_LIVE_ARM_CHECKS,
    LiveArmContext,
    scope_for_check,
)
from operations.connectivity.universal_entry_consumer import run_once

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="set CRIPTA_TRADE_LIFECYCLE_TEST_DSN to an explicitly disposable PostgreSQL DB",
)
NOW = datetime(2026, 9, 18, 14, 0, tzinfo=UTC)


def _seed(
    connection: psycopg.Connection,
    prefix: str,
    *,
    symbol: str,
    reservation_state: str,
    expires_at: datetime,
    dispatched: bool = False,
    permission: bool = False,
    command: bool = False,
) -> dict[str, str]:
    strategy_id = f"{prefix}-strategy"
    version = "1"
    strategy_fp = f"{prefix}-strategy-fp"
    activation_id = f"{prefix}-activation"
    entry_fp = f"{prefix}-entry-fp"
    exit_fp = f"{prefix}-exit-fp"
    signal_id = f"{prefix}-signal"
    attempt_id = f"{prefix}-attempt"
    decision_id = f"{prefix}-decision"
    reservation_id = f"{prefix}-reservation"
    slot_claim_id = f"{prefix}-slot"
    position_mode_state_ref = f"{prefix}-pmode"
    exchange_key = f"BYBIT:UNIFIED:LINEAR:USDT:{symbol}:0"
    request_id = f"{prefix}-request"
    command_id = f"ue-{prefix}"[:36]

    connection.execute(
        """INSERT INTO strategy_entry.strategy_cards(
               strategy_id,strategy_version,strategy_config_fingerprint,
               name,description,card_json,approved_at,approved_source
           ) VALUES(%s,%s,%s,'test','p4.1',%s::jsonb,%s,'test')""",
        (
            strategy_id,
            version,
            strategy_fp,
            json.dumps(
                {
                    "strategy_id": strategy_id,
                    "strategy_version": version,
                    "strategy_config_fingerprint": strategy_fp,
                }
            ),
            NOW,
        ),
    )
    connection.execute(
        """INSERT INTO strategy_entry.strategy_activations(
               activation_id,strategy_id,strategy_version,
               strategy_config_fingerprint,enabled,enabled_at,disabled_at,
               scope,operator,source,change_reason
           ) VALUES(%s,%s,%s,%s,true,%s,NULL,'{}'::jsonb,'test','test','')""",
        (activation_id, strategy_id, version, strategy_fp, NOW),
    )
    connection.execute(
        """INSERT INTO strategy_entry.entry_plans(
               entry_plan_fingerprint,strategy_id,strategy_version,
               strategy_config_fingerprint,entry_plan_version,plan_json
           ) VALUES(%s,%s,%s,%s,'1',%s::jsonb)""",
        (
            entry_fp,
            strategy_id,
            version,
            strategy_fp,
            json.dumps({"entry_plan_fingerprint": entry_fp}),
        ),
    )
    connection.execute(
        """INSERT INTO strategy_entry.exit_plans(
               exit_plan_fingerprint,strategy_id,strategy_version,
               strategy_config_fingerprint,exit_plan_version,plan_json
           ) VALUES(%s,%s,%s,%s,'1',%s::jsonb)""",
        (
            exit_fp,
            strategy_id,
            version,
            strategy_fp,
            json.dumps({"exit_plan_fingerprint": exit_fp}),
        ),
    )
    connection.execute(
        """INSERT INTO strategy_entry.strategy_signals(
               signal_id,strategy_id,strategy_version,strategy_config_fingerprint,
               entry_plan_fingerprint,strategy_activation_id,symbol,direction,
               detected_at,fact_id,source_refs,payload
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,'LONG',%s,%s,'[]'::jsonb,'{}'::jsonb)""",
        (
            signal_id,
            strategy_id,
            version,
            strategy_fp,
            entry_fp,
            activation_id,
            symbol,
            NOW,
            f"{prefix}-fact",
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
            version,
            strategy_fp,
            entry_fp,
            activation_id,
            NOW,
        ),
    )
    connection.execute(
        """INSERT INTO runtime.position_mode_states(
               position_mode_state_ref,exchange,account_ref,product_category,
               instrument,position_mode,position_idx,observed_at,received_at,
               fresh_until,provenance
           ) VALUES(%s,'BYBIT','BYBIT:UNIFIED','LINEAR',%s,'ONE_WAY',0,
                    %s,%s,%s,'{}'::jsonb)""",
        (
            position_mode_state_ref,
            symbol,
            NOW,
            NOW,
            NOW + timedelta(minutes=10),
        ),
    )
    connection.execute(
        """INSERT INTO runtime.capital_reservations(
               reservation_id,account_ref,strategy_id,strategy_version,
               strategy_config_fingerprint,entry_plan_fingerprint,signal_id,
               strategy_attempt_id,requested_amount,amount_currency,
               capacity_snapshot_id,capacity_observed_at,
               capacity_available_at_reservation,pre_dispatch_expires_at,
               state,state_reason,created_at,updated_at
           ) VALUES(%s,'BYBIT:UNIFIED',%s,%s,%s,%s,%s,%s,10,'USDT',
                    %s,%s,10,%s,%s,'TEST_SEED',%s,%s)""",
        (
            reservation_id,
            strategy_id,
            version,
            strategy_fp,
            entry_fp,
            signal_id,
            attempt_id,
            f"{prefix}-capacity",
            NOW,
            expires_at,
            reservation_state,
            NOW,
            NOW,
        ),
    )
    connection.execute(
        """INSERT INTO runtime.exchange_position_slot_claims(
               exchange_position_slot_claim_id,exchange_position_key,account_ref,
               symbol,position_idx,strategy_attempt_id,strategy_id,strategy_version,
               strategy_config_fingerprint,entry_plan_fingerprint,direction,
               position_mode_state_ref,capital_reservation_id,claim_state,
               claimed_at,updated_at
           ) VALUES(%s,%s,'BYBIT:UNIFIED',%s,0,%s,%s,%s,%s,%s,'LONG',
                    %s,%s,'CLAIMED',%s,%s)""",
        (
            slot_claim_id,
            exchange_key,
            symbol,
            attempt_id,
            strategy_id,
            version,
            strategy_fp,
            entry_fp,
            position_mode_state_ref,
            reservation_id,
            NOW,
            NOW,
        ),
    )
    connection.execute(
        """INSERT INTO strategy_entry.entry_decisions(
               entry_decision_id,strategy_attempt_id,signal_id,decision_code,
               reason,decided_at,capacity_snapshot_id,capital_reservation_id,
               exchange_position_slot_claim_id,position_mode_state_ref,payload
           ) VALUES(%s,%s,%s,'ACCEPTED','test',%s,%s,%s,%s,%s,'{}'::jsonb)""",
        (
            decision_id,
            attempt_id,
            signal_id,
            NOW,
            f"{prefix}-capacity",
            reservation_id,
            slot_claim_id,
            position_mode_state_ref,
        ),
    )
    connection.execute(
        """INSERT INTO strategy_entry.execution_requests(
               execution_request_id,strategy_attempt_id,entry_decision_id,signal_id,
               strategy_id,strategy_version,strategy_config_fingerprint,
               entry_plan_fingerprint,symbol,direction,requested_at,payload,
               exit_plan_fingerprint,capital_reservation_id,
               exchange_position_slot_claim_id,position_mode_state_ref
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'LONG',%s,'{}'::jsonb,%s,%s,%s,%s)""",
        (
            request_id,
            attempt_id,
            decision_id,
            signal_id,
            strategy_id,
            version,
            strategy_fp,
            entry_fp,
            symbol,
            NOW,
            exit_fp,
            reservation_id,
            slot_claim_id,
            position_mode_state_ref,
        ),
    )
    if permission:
        connection.execute(
            """INSERT INTO strategy_entry.execution_permissions(
                   execution_permission_id,strategy_id,strategy_version,
                   strategy_config_fingerprint,enabled,enabled_at,disabled_at,
                   operator,source,change_reason
               ) VALUES(%s,%s,%s,%s,true,%s,NULL,'test','test','')""",
            (f"{prefix}-permission", strategy_id, version, strategy_fp, NOW),
        )
    if dispatched:
        connection.execute(
            """INSERT INTO strategy_entry.execution_dispatches(
                   dispatch_id,execution_request_id,command_id,state,reason,
                   strategy_attempt_id,entry_decision_id,signal_id,strategy_id,
                   strategy_version,strategy_config_fingerprint,
                   entry_plan_fingerprint,exit_plan_fingerprint,payload,
                   capital_reservation_id,exchange_position_slot_claim_id,
                   position_mode_state_ref
               ) VALUES(%s,%s,%s,'DISPATCHED','test',%s,%s,%s,%s,%s,%s,%s,%s,
                        '{}'::jsonb,%s,%s,%s)""",
            (
                f"{prefix}-dispatch",
                request_id,
                command_id,
                attempt_id,
                decision_id,
                signal_id,
                strategy_id,
                version,
                strategy_fp,
                entry_fp,
                exit_fp,
                reservation_id,
                slot_claim_id,
                position_mode_state_ref,
            ),
        )
    if command:
        payload = {
            "source": "universal_entry",
            "execution_request_id": request_id,
            "strategy_attempt_id": attempt_id,
            "entry_decision_id": decision_id,
            "signal_id": signal_id,
            "strategy_id": strategy_id,
            "strategy_version": version,
            "strategy_config_fingerprint": strategy_fp,
            "entry_plan_fingerprint": entry_fp,
            "exit_plan_fingerprint": exit_fp,
            "capital_reservation_id": reservation_id,
            "exchange_position_slot_claim_id": slot_claim_id,
            "position_mode_state_ref": position_mode_state_ref,
            "strategy_activation_id": activation_id,
            "bot_instance_id": "universal-entry",
        }
        connection.execute(
            """INSERT INTO runtime.trade_commands(
                   command_id,command_type,symbol,payload_json,state,requested_at_epoch_ms
               ) VALUES(%s,'entry',%s,%s,'queued',%s)""",
            (command_id, symbol, json.dumps(payload), int(NOW.timestamp() * 1000)),
        )
    return {
        "strategy_id": strategy_id,
        "strategy_version": version,
        "strategy_config_fingerprint": strategy_fp,
        "strategy_activation_id": activation_id,
        "reservation_id": reservation_id,
        "slot_claim_id": slot_claim_id,
        "position_mode_state_ref": position_mode_state_ref,
        "request_id": request_id,
        "command_id": command_id,
        "symbol": symbol,
    }


TEST_RELEASE_COMMIT = os.environ.get(
    "CRIPTA_RELEASE_COMMIT",
    "fc8f6b494b270aa855a9a03ee7932562c163db99",
).strip().lower()


def _seed_live_arm_ready(
    connection: psycopg.Connection,
    ids: dict[str, str],
) -> None:
    context = LiveArmContext(
        strategy_id=ids["strategy_id"],
        strategy_version=ids["strategy_version"],
        strategy_config_fingerprint=ids["strategy_config_fingerprint"],
        strategy_activation_id=ids["strategy_activation_id"],
        symbol=ids["symbol"],
        release_commit=TEST_RELEASE_COMMIT,
    )
    for index, code in enumerate(REQUIRED_LIVE_ARM_CHECKS):
        scope_type, scope_key = scope_for_check(code, context)
        connection.execute(
            """INSERT INTO control.live_arm_evidence(
                   evidence_id,check_code,scope_type,scope_key,status,checked_at,
                   valid_until,release_commit,source,evidence
               ) VALUES(%s,%s,%s,%s,'PASS',%s,%s,%s,'test','{}'::jsonb)""",
            (
                f"{ids['strategy_id']}-evidence-{index}",
                code,
                scope_type,
                scope_key,
                NOW,
                NOW + timedelta(minutes=10),
                TEST_RELEASE_COMMIT,
            ),
        )
    connection.execute(
        """INSERT INTO control.live_arm_sessions(
               live_arm_session_id,strategy_id,strategy_version,
               strategy_config_fingerprint,strategy_activation_id,symbol,
               release_commit,state,owner_approved_at,activated_at,
               deactivated_at,source
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s,%s,NULL,'test')""",
        (
            f"{ids['strategy_id']}-live-session",
            ids["strategy_id"],
            ids["strategy_version"],
            ids["strategy_config_fingerprint"],
            ids["strategy_activation_id"],
            ids["symbol"],
            TEST_RELEASE_COMMIT,
            NOW,
            NOW,
        ),
    )


def _gate(connection: psycopg.Connection, enabled: bool) -> None:
    connection.execute(
        """INSERT INTO control.execution_gates(mode,enabled,reason,updated_at_epoch_ms)
           VALUES('mainnet',%s,'p4.1 disposable test',1)
           ON CONFLICT(mode) DO UPDATE
             SET enabled=excluded.enabled,reason=excluded.reason,
                 updated_at_epoch_ms=excluded.updated_at_epoch_ms""",
        (1 if enabled else 0,),
    )


def test_expired_reserved_request_is_released_even_when_gate_is_disarmed() -> None:
    assert DSN is not None
    prefix = "expired-" + uuid4().hex[:12]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        ids = _seed(
            connection,
            prefix,
            symbol="ADAUSDT",
            reservation_state="RESERVED",
            expires_at=NOW - timedelta(seconds=1),
        )
        _gate(connection, False)
        connection.commit()

        assert run_once(connection, now=NOW) == "EXECUTION_GATE_DISARMED"
        reservation = connection.execute(
            """SELECT state,state_reason
                 FROM runtime.capital_reservations
                WHERE reservation_id=%s""",
            (ids["reservation_id"],),
        ).fetchone()
        assert reservation == {
            "state": "RELEASED",
            "state_reason": "REQUEST_EXPIRED_PRE_DISPATCH",
        }
        dispatch = connection.execute(
            """SELECT state,reason,command_id
                 FROM strategy_entry.execution_dispatches
                WHERE execution_request_id=%s""",
            (ids["request_id"],),
        ).fetchone()
        assert dispatch is not None
        assert dispatch["state"] == "BLOCKED"
        assert dispatch["reason"] == "REQUEST_EXPIRED_PRE_DISPATCH"
        assert dispatch["command_id"] is None


def test_missing_live_arm_evidence_blocks_before_exchange_and_releases_admission() -> None:
    assert DSN is not None
    prefix = "live-arm-block-" + uuid4().hex[:12]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        ids = _seed(
            connection,
            prefix,
            symbol="SOLUSDT",
            reservation_state="RESERVED",
            expires_at=NOW + timedelta(seconds=30),
            permission=True,
        )
        _gate(connection, True)
        connection.commit()

        result = run_once(connection, now=NOW)
        assert result == "BLOCKED:LIVE_ARM_NOT_READY"

        reservation = connection.execute(
            """SELECT state,state_reason
                 FROM runtime.capital_reservations
                WHERE reservation_id=%s""",
            (ids["reservation_id"],),
        ).fetchone()
        assert reservation is not None
        assert reservation["state"] == "RELEASED"
        assert reservation["state_reason"] == "PRE_EXCHANGE_BLOCK:LIVE_ARM_NOT_READY"

        claim = connection.execute(
            """SELECT claim_state,release_reason
                 FROM runtime.exchange_position_slot_claims
                WHERE exchange_position_slot_claim_id=%s""",
            (ids["slot_claim_id"],),
        ).fetchone()
        assert claim is not None
        assert claim["claim_state"] == "RELEASED"
        assert claim["release_reason"] == "PRE_EXCHANGE_BLOCK:LIVE_ARM_NOT_READY"

        command_count = connection.execute(
            "SELECT count(*) AS count FROM runtime.trade_commands WHERE symbol=%s",
            (ids["symbol"],),
        ).fetchone()
        assert command_count == {"count": 0}


def test_structural_pre_exchange_block_releases_reserved_capital() -> None:
    assert DSN is not None
    prefix = "structural-" + uuid4().hex[:12]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        ids = _seed(
            connection,
            prefix,
            symbol="DOGEUSDT",
            reservation_state="RESERVED",
            expires_at=NOW + timedelta(seconds=30),
            permission=True,
        )
        _seed_live_arm_ready(connection, ids)
        _gate(connection, True)
        connection.commit()

        result = run_once(connection, now=NOW)
        assert result in {
            "BLOCKED:STRUCTURAL_ERROR",
            "BLOCKED:IDENTITY_MISMATCH",
            "BLOCKED:POLICY_UNSUPPORTED",
        }
        reservation = connection.execute(
            """SELECT state,state_reason
                 FROM runtime.capital_reservations
                WHERE reservation_id=%s""",
            (ids["reservation_id"],),
        ).fetchone()
        assert reservation is not None
        assert reservation["state"] == "RELEASED"
        assert str(reservation["state_reason"]).startswith("PRE_EXCHANGE_BLOCK:")


def test_order_acknowledgement_changes_dispatch_to_pending_exchange_reflection() -> None:
    assert DSN is not None
    prefix = "ack-" + uuid4().hex[:12]
    with psycopg.connect(DSN) as connection:
        ids = _seed(
            connection,
            prefix,
            symbol="LINKUSDT",
            reservation_state="DISPATCHED",
            expires_at=NOW + timedelta(seconds=30),
            dispatched=True,
            command=True,
        )
        connection.commit()
        state = mark_entry_order_acknowledged(
            connection,
            command_id=ids["command_id"],
            exchange_order_id=f"{prefix}-order",
            acknowledged_at=NOW,
        )
        assert state == "PENDING_EXCHANGE_REFLECTION"
        row = connection.execute(
            """SELECT state,state_reason,exchange_commitment_ref
                 FROM runtime.capital_reservations
                WHERE reservation_id=%s""",
            (ids["reservation_id"],),
        ).fetchone()
        assert row == (
            "PENDING_EXCHANGE_REFLECTION",
            "ENTRY_ORDER_ACKNOWLEDGED",
            f"{prefix}-order",
        )


def test_deterministic_failure_before_order_ack_releases_dispatched_capital() -> None:
    assert DSN is not None
    prefix = "reject-" + uuid4().hex[:12]
    with psycopg.connect(DSN) as connection:
        ids = _seed(
            connection,
            prefix,
            symbol="XRPUSDT",
            reservation_state="DISPATCHED",
            expires_at=NOW + timedelta(seconds=30),
            dispatched=True,
            command=True,
        )
        state = finalize_failed_entry_command_reservation(
            connection,
            command_id=ids["command_id"],
            reason="BYBIT_EXPLICIT_REJECT",
            mutation_ambiguous=False,
        )
        assert state == "RELEASED"
        stored = connection.execute(
            "SELECT state FROM runtime.capital_reservations WHERE reservation_id=%s",
            (ids["reservation_id"],),
        ).fetchone()
        assert stored == ("RELEASED",)


def test_failure_after_order_ack_never_blindly_releases_capital() -> None:
    assert DSN is not None
    prefix = "ambiguous-" + uuid4().hex[:12]
    with psycopg.connect(DSN) as connection:
        ids = _seed(
            connection,
            prefix,
            symbol="SOLUSDT",
            reservation_state="DISPATCHED",
            expires_at=NOW + timedelta(seconds=30),
            dispatched=True,
            command=True,
        )
        mark_entry_order_acknowledged(
            connection,
            command_id=ids["command_id"],
            exchange_order_id=f"{prefix}-order",
            acknowledged_at=NOW,
        )
        state = finalize_failed_entry_command_reservation(
            connection,
            command_id=ids["command_id"],
            reason="POST_ACK_FAILURE",
            mutation_ambiguous=False,
        )
        assert state == "RECONCILIATION_REQUIRED"
        stored = connection.execute(
            "SELECT state FROM runtime.capital_reservations WHERE reservation_id=%s",
            (ids["reservation_id"],),
        ).fetchone()
        assert stored == ("RECONCILIATION_REQUIRED",)


def test_confirmed_zero_fill_ttl_cancel_releases_pending_capital() -> None:
    assert DSN is not None
    prefix = "zero-fill-" + uuid4().hex[:10]
    with psycopg.connect(DSN) as connection:
        ids = _seed(
            connection,
            prefix,
            symbol="AVAXUSDT",
            reservation_state="DISPATCHED",
            expires_at=NOW + timedelta(seconds=30),
            dispatched=True,
            command=True,
        )
        order_id = f"{prefix}-order"
        mark_entry_order_acknowledged(
            connection,
            command_id=ids["command_id"],
            exchange_order_id=order_id,
            acknowledged_at=NOW,
        )
        connection.execute(
            """INSERT INTO runtime.exchange_order_history(
                   order_id,order_link_id,symbol,side,order_status,
                   updated_at_epoch_ms,payload_json,refreshed_at_epoch_ms
               ) VALUES(%s,%s,%s,'Buy','Cancelled',1,%s::jsonb,1)""",
            (
                order_id,
                ids["command_id"][:36],
                ids["symbol"],
                json.dumps({"cumExecQty": "0"}),
            ),
        )
        state = resolve_cancelled_entry_reservation_after_reconcile(
            connection,
            command_id=ids["command_id"],
            exchange_order_id=order_id,
        )
        assert state == "RELEASED"


def test_partial_fill_cancel_requires_reconciliation_and_keeps_capital() -> None:
    assert DSN is not None
    prefix = "partial-" + uuid4().hex[:10]
    with psycopg.connect(DSN) as connection:
        ids = _seed(
            connection,
            prefix,
            symbol="BNBUSDT",
            reservation_state="DISPATCHED",
            expires_at=NOW + timedelta(seconds=30),
            dispatched=True,
            command=True,
        )
        order_id = f"{prefix}-order"
        mark_entry_order_acknowledged(
            connection,
            command_id=ids["command_id"],
            exchange_order_id=order_id,
            acknowledged_at=NOW,
        )
        connection.execute(
            """INSERT INTO runtime.exchange_order_history(
                   order_id,order_link_id,symbol,side,order_status,
                   updated_at_epoch_ms,payload_json,refreshed_at_epoch_ms
               ) VALUES(%s,%s,%s,'Buy','Cancelled',1,%s::jsonb,1)""",
            (
                order_id,
                ids["command_id"][:36],
                ids["symbol"],
                json.dumps({"cumExecQty": "0.5"}),
            ),
        )
        connection.execute(
            """INSERT INTO runtime.executions(
                   exec_id,order_id,order_link_id,symbol,side,exec_qty,exec_price,
                   exec_fee,exec_time_ms,received_at_epoch_ms,payload_json
               ) VALUES(%s,%s,%s,%s,'Buy','0.5','1','0',1,1,'{}')""",
            (
                f"{prefix}-exec",
                order_id,
                ids["command_id"][:36],
                ids["symbol"],
            ),
        )
        state = resolve_cancelled_entry_reservation_after_reconcile(
            connection,
            command_id=ids["command_id"],
            exchange_order_id=order_id,
        )
        assert state == "RECONCILIATION_REQUIRED"
        stored = connection.execute(
            "SELECT state FROM runtime.capital_reservations WHERE reservation_id=%s",
            (ids["reservation_id"],),
        ).fetchone()
        assert stored == ("RECONCILIATION_REQUIRED",)
