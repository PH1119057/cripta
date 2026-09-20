from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from bybit_workbench.strategy_position_binding import (
    exchange_position_key,
    load_universal_entry_lineage,
    persist_universal_strategy_position,
    release_position_capital_reservation,
)
from operations.connectivity.universal_entry_consumer import run_once

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="set CRIPTA_TRADE_LIFECYCLE_TEST_DSN to an explicitly disposable PostgreSQL DB",
)
NOW = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)


def _seed_request(
    connection: psycopg.Connection,
    prefix: str,
    *,
    reservation_state: str,
    dispatched: bool,
    permission: bool = False,
    symbol: str = "UNIUSDT",
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
    exchange_key = exchange_position_key(symbol, 0)
    request_id = f"{prefix}-request"
    command_id = f"{prefix}-command"

    connection.execute(
        """INSERT INTO strategy_entry.strategy_cards(
               strategy_id,strategy_version,strategy_config_fingerprint,
               name,description,card_json,approved_at,approved_source
           ) VALUES(%s,%s,%s,'test','p4',%s::jsonb,%s,'test')""",
        (
            strategy_id,
            version,
            strategy_fp,
            (
                '{"strategy_id":"'
                + strategy_id
                + '","strategy_version":"1","strategy_config_fingerprint":"'
                + strategy_fp
                + '"}'
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
            '{"entry_plan_fingerprint":"' + entry_fp + '"}',
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
            '{"exit_plan_fingerprint":"' + exit_fp + '"}',
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
            NOW + timedelta(seconds=30),
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
    if permission:
        connection.execute(
            """INSERT INTO strategy_entry.execution_permissions(
                   execution_permission_id,strategy_id,strategy_version,
                   strategy_config_fingerprint,enabled,enabled_at,disabled_at,
                   operator,source,change_reason
               ) VALUES(%s,%s,%s,%s,true,%s,NULL,'test','test','')""",
            (f"{prefix}-permission", strategy_id, version, strategy_fp, NOW),
        )
    return {
        "strategy_id": strategy_id,
        "strategy_fp": strategy_fp,
        "activation_id": activation_id,
        "entry_fp": entry_fp,
        "exit_fp": exit_fp,
        "signal_id": signal_id,
        "attempt_id": attempt_id,
        "decision_id": decision_id,
        "reservation_id": reservation_id,
        "slot_claim_id": slot_claim_id,
        "position_mode_state_ref": position_mode_state_ref,
        "exchange_key": exchange_key,
        "request_id": request_id,
        "command_id": command_id,
        "symbol": symbol,
    }


def _payload(ids: dict[str, str]) -> dict[str, object]:
    return {
        "source": "universal_entry",
        "execution_request_id": ids["request_id"],
        "strategy_attempt_id": ids["attempt_id"],
        "entry_decision_id": ids["decision_id"],
        "signal_id": ids["signal_id"],
        "strategy_id": ids["strategy_id"],
        "strategy_version": "1",
        "strategy_config_fingerprint": ids["strategy_fp"],
        "entry_plan_fingerprint": ids["entry_fp"],
        "exit_plan_fingerprint": ids["exit_fp"],
        "capital_reservation_id": ids["reservation_id"],
        "exchange_position_slot_claim_id": ids["slot_claim_id"],
        "position_mode_state_ref": ids["position_mode_state_ref"],
        "emergency_policy": {
            "enabled": True,
            "on_fault": "POSITION_WITHOUT_CONFIRMED_INITIAL_PROTECTION",
            "action": "FAIL_CLOSED_ONLY",
            "reconciliation_required": True,
        },
        "strategy_activation_id": ids["activation_id"],
        "bot_instance_id": "universal-entry",
    }


def test_confirmed_fill_binds_exact_strategy_position_and_consumes_reservation() -> None:
    assert DSN is not None
    prefix = "bind-" + uuid4().hex
    with psycopg.connect(DSN) as connection:
        ids = _seed_request(
            connection, prefix, reservation_state="DISPATCHED", dispatched=True, symbol="UNIUSDT"
        )
        lineage = load_universal_entry_lineage(
            connection,
            command_id=ids["command_id"],
            command_payload=_payload(ids),
        )
        assert lineage is not None
        position = persist_universal_strategy_position(
            connection,
            lineage=lineage,
            position_id=f"{prefix}-position",
            trade_id=f"{prefix}-trade",
            entry_command_id=ids["command_id"],
            symbol="UNIUSDT",
            side="Buy",
            actual_avg_fill=Decimal("8.50"),
            actual_qty=Decimal("2"),
            fill_at=NOW,
            exchange_order_ids=(f"{prefix}-order",),
            client_order_ids=(ids["command_id"],),
            execution_ids=(f"{prefix}-exec",),
            position_idx=0,
        )
        assert position.exit_plan_fingerprint == ids["exit_fp"]
        assert position.entry_execution_request_id == ids["request_id"]
        stored = connection.execute(
            """SELECT strategy_config_fingerprint,strategy_activation_id,
                      strategy_attempt_id,entry_decision_id,
                      entry_execution_request_id,entry_plan_fingerprint,
                      exit_plan_fingerprint,account_ref,exchange_position_key
                 FROM runtime.position_ownership
                WHERE position_id=%s""",
            (position.strategy_position_id,),
        ).fetchone()
        assert stored == (
            ids["strategy_fp"],
            ids["activation_id"],
            ids["attempt_id"],
            ids["decision_id"],
            ids["request_id"],
            ids["entry_fp"],
            ids["exit_fp"],
            "BYBIT:UNIFIED",
            exchange_position_key("UNIUSDT", 0),
        )
        reservation = connection.execute(
            """SELECT state,strategy_position_id,exchange_commitment_ref
                 FROM runtime.capital_reservations
                WHERE reservation_id=%s""",
            (ids["reservation_id"],),
        ).fetchone()
        assert reservation == (
            "CONSUMED",
            position.strategy_position_id,
            position.strategy_position_id,
        )

        repeated = persist_universal_strategy_position(
            connection,
            lineage=lineage,
            position_id=position.strategy_position_id,
            trade_id=f"{prefix}-trade",
            entry_command_id=ids["command_id"],
            symbol="UNIUSDT",
            side="Buy",
            actual_avg_fill=Decimal("8.50"),
            actual_qty=Decimal("2"),
            fill_at=NOW,
            exchange_order_ids=(f"{prefix}-order",),
            client_order_ids=(ids["command_id"],),
            execution_ids=(f"{prefix}-exec",),
            position_idx=0,
        )
        assert repeated.strategy_position_id == position.strategy_position_id


def test_command_payload_lineage_mismatch_is_fail_closed() -> None:
    assert DSN is not None
    prefix = "mismatch-" + uuid4().hex
    with psycopg.connect(DSN) as connection:
        ids = _seed_request(
            connection,
            prefix,
            reservation_state="DISPATCHED",
            dispatched=True,
            symbol="MISMATCHUSDT",
        )
        payload = _payload(ids)
        payload["exit_plan_fingerprint"] = "wrong-exit-plan"
        with pytest.raises(RuntimeError, match="exit_plan_fingerprint lineage mismatch"):
            load_universal_entry_lineage(
                connection,
                command_id=ids["command_id"],
                command_payload=payload,
            )


def test_second_active_slot_claim_same_exchange_position_is_rejected_before_fill() -> None:
    assert DSN is not None
    prefix_a = "slot-a-" + uuid4().hex
    prefix_b = "slot-b-" + uuid4().hex
    with psycopg.connect(DSN) as connection:
        ids_a = _seed_request(
            connection,
            prefix_a,
            reservation_state="DISPATCHED",
            dispatched=True,
            symbol="BTCUSDT",
        )
        connection.commit()
        with pytest.raises(
            psycopg.errors.UniqueViolation
        ), connection.transaction():
            _seed_request(
                connection,
                prefix_b,
                reservation_state="DISPATCHED",
                dispatched=True,
                symbol="BTCUSDT",
            )
        row = connection.execute(
            """SELECT exchange_position_slot_claim_id,claim_state
                 FROM runtime.exchange_position_slot_claims
                WHERE exchange_position_key=%s""",
            (ids_a["exchange_key"],),
        ).fetchall()
    assert row == [(ids_a["slot_claim_id"], "CLAIMED")]


def test_consumer_detects_post_admission_slot_invariant_and_keeps_resources_for_reconcile() -> None:
    assert DSN is not None
    prefix = "preblock-" + uuid4().hex
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        ids = _seed_request(
            connection,
            prefix,
            reservation_state="RESERVED",
            dispatched=False,
            permission=True,
            symbol="ETHUSDT",
        )
        connection.execute(
            """INSERT INTO control.execution_gates(mode,enabled,reason,updated_at_epoch_ms)
               VALUES('mainnet',1,'p4 disposable test',1)
               ON CONFLICT(mode) DO UPDATE SET enabled=1,reason=excluded.reason,
                                               updated_at_epoch_ms=excluded.updated_at_epoch_ms"""
        )
        connection.execute(
            """INSERT INTO runtime.position_ownership(
                   position_id,trade_id,bot_instance_id,strategy_id,strategy_version,
                   signal_id,entry_command_id,geometry_handoff_id,symbol,side,
                   actual_avg_fill,actual_qty,fill_at,exchange_order_ids,
                   client_order_ids,execution_ids,exchange_position_key,position_idx)
               VALUES(%s,%s,'legacy-test','legacy','1',%s,%s,NULL,'ETHUSDT','Buy',
                      8.0,1,%s,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,%s,0)""",
            (
                f"{prefix}-occupied-position",
                f"{prefix}-occupied-trade",
                f"{prefix}-old-signal",
                f"{prefix}-old-command",
                NOW,
                exchange_position_key("ETHUSDT", 0),
            ),
        )
        connection.commit()
        result = run_once(connection, now=NOW)
        assert result == "BLOCKED:EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN"
        dispatch = connection.execute(
            """SELECT state,command_id,reason
                 FROM strategy_entry.execution_dispatches
                WHERE execution_request_id=%s""",
            (ids["request_id"],),
        ).fetchone()
        assert dispatch is not None
        assert dispatch["state"] == "BLOCKED"
        assert dispatch["command_id"] is None
        assert str(dispatch["reason"]).startswith(
            "EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN:"
        )
        reservation = connection.execute(
            "SELECT state FROM runtime.capital_reservations WHERE reservation_id=%s",
            (ids["reservation_id"],),
        ).fetchone()
        assert reservation is not None
        assert reservation["state"] == "RECONCILIATION_REQUIRED"
        claim = connection.execute(
            """SELECT claim_state FROM runtime.exchange_position_slot_claims
                WHERE exchange_position_slot_claim_id=%s""",
            (ids["slot_claim_id"],),
        ).fetchone()
        assert claim is not None
        assert claim["claim_state"] == "RECONCILIATION_REQUIRED"
        command_count = connection.execute(
            "SELECT count(*) FROM runtime.trade_commands WHERE symbol='ETHUSDT'"
        ).fetchone()
        assert command_count is not None and command_count["count"] == 0


def test_confirmed_close_releases_bound_reservation() -> None:
    assert DSN is not None
    prefix = "release-position-" + uuid4().hex
    with psycopg.connect(DSN) as connection:
        ids = _seed_request(
            connection, prefix, reservation_state="DISPATCHED", dispatched=True, symbol="XRPUSDT"
        )
        lineage = load_universal_entry_lineage(
            connection, command_id=ids["command_id"], command_payload=_payload(ids)
        )
        assert lineage is not None
        position_id = f"{prefix}-position"
        persist_universal_strategy_position(
            connection,
            lineage=lineage,
            position_id=position_id,
            trade_id=f"{prefix}-trade",
            entry_command_id=ids["command_id"],
            symbol="XRPUSDT",
            side="Buy",
            actual_avg_fill=Decimal("8.50"),
            actual_qty=Decimal("1"),
            fill_at=NOW,
            exchange_order_ids=(f"{prefix}-order",),
            client_order_ids=(ids["command_id"],),
            execution_ids=(f"{prefix}-exec",),
            position_idx=0,
        )
        connection.execute(
            "UPDATE runtime.position_ownership SET state='CLOSED' WHERE position_id=%s",
            (position_id,),
        )
        release_position_capital_reservation(
            connection,
            position_id=position_id,
            entry_execution_request_id=ids["request_id"],
        )
        state = connection.execute(
            "SELECT state FROM runtime.capital_reservations WHERE reservation_id=%s",
            (ids["reservation_id"],),
        ).fetchone()
        assert state == ("RELEASED",)
