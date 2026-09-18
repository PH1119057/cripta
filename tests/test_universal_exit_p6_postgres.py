from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from bybit_workbench.universal_exit.execution_bridge import (
    prepare_runtime_exit_command,
)
from bybit_workbench.universal_exit.execution_store import (
    exact_open_position,
    load_exit_execution_request,
    mark_ambiguous_exit_command,
    publish_runtime_exit_command,
)
from operations.connectivity.universal_exit_consumer import (
    dispatch_once,
    materialize_once,
)

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="set CRIPTA_TRADE_LIFECYCLE_TEST_DSN to an explicitly disposable PostgreSQL DB",
)
NOW = datetime(2026, 9, 18, 21, 0, tzinfo=UTC)


def _gate(connection: psycopg.Connection, enabled: bool) -> None:
    connection.execute(
        """INSERT INTO control.execution_gates(mode,enabled,reason,updated_at_epoch_ms)
           VALUES('mainnet',%s,'p6 disposable test',1)
           ON CONFLICT(mode) DO UPDATE
             SET enabled=excluded.enabled,reason=excluded.reason,
                 updated_at_epoch_ms=excluded.updated_at_epoch_ms""",
        (1 if enabled else 0,),
    )


def _seed(
    connection: psycopg.Connection,
    prefix: str,
    *,
    decided_at: datetime = NOW,
    max_age: int = 30,
    action_kind: str = "CLOSE",
    mutation: dict[str, object] | None = None,
) -> dict[str, str]:
    strategy_id = f"{prefix}-strategy"
    version = "1"
    strategy_fp = f"{prefix}-strategy-fp"
    activation_id = f"{prefix}-activation"
    entry_fp = f"{prefix}-entry-fp"
    exit_fp = f"{prefix}-exit-fp"
    signal_id = f"{prefix}-signal"
    attempt_id = f"{prefix}-attempt"
    entry_decision_id = f"{prefix}-entry-decision"
    entry_request_id = f"{prefix}-entry-request"
    position_id = f"{prefix}-position"
    entry_command_id = f"{prefix}-entry-command"
    observation_id = f"{prefix}-exit-observation"
    exit_decision_id = f"{prefix}-exit-decision"
    symbol = "P6" + prefix.replace("-", "").upper()[-8:] + "USDT"
    mutation = mutation or {"quantity": "ALL", "order_type": "Market"}

    exit_plan_json = {
        "strategy_id": strategy_id,
        "strategy_version": version,
        "strategy_config_fingerprint": strategy_fp,
        "exit_plan_version": "1",
        "exit_plan_fingerprint": exit_fp,
        "exit_policy": {
            "execution_policy": {"max_request_age_seconds": max_age},
            "rules": [],
        },
        "protection_policy": {},
    }

    connection.execute(
        """INSERT INTO strategy_entry.strategy_cards(
               strategy_id,strategy_version,strategy_config_fingerprint,
               name,description,card_json,approved_at,approved_source
           ) VALUES(%s,%s,%s,'p6','p6',%s::jsonb,%s,'test')""",
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
            json.dumps(
                {
                    "strategy_id": strategy_id,
                    "strategy_version": version,
                    "strategy_config_fingerprint": strategy_fp,
                    "entry_plan_fingerprint": entry_fp,
                }
            ),
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
            json.dumps(exit_plan_json),
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
        """INSERT INTO strategy_entry.entry_decisions(
               entry_decision_id,strategy_attempt_id,signal_id,decision_code,
               reason,decided_at,payload
           ) VALUES(%s,%s,%s,'ACCEPTED','test',%s,'{}'::jsonb)""",
        (entry_decision_id, attempt_id, signal_id, NOW),
    )
    connection.execute(
        """INSERT INTO strategy_entry.execution_requests(
               execution_request_id,strategy_attempt_id,entry_decision_id,signal_id,
               strategy_id,strategy_version,strategy_config_fingerprint,
               entry_plan_fingerprint,symbol,direction,requested_at,payload,
               exit_plan_fingerprint
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'LONG',%s,'{}'::jsonb,%s)""",
        (
            entry_request_id,
            attempt_id,
            entry_decision_id,
            signal_id,
            strategy_id,
            version,
            strategy_fp,
            entry_fp,
            symbol,
            NOW,
            exit_fp,
        ),
    )
    connection.execute(
        """INSERT INTO runtime.position_ownership(
               position_id,trade_id,bot_instance_id,strategy_id,strategy_version,
               signal_id,entry_command_id,symbol,side,actual_avg_fill,actual_qty,
               fill_at,exchange_order_ids,client_order_ids,execution_ids,state,
               exchange_position_key,position_idx,account_ref,
               strategy_config_fingerprint,strategy_activation_id,
               strategy_attempt_id,entry_decision_id,entry_execution_request_id,
               entry_plan_fingerprint,exit_plan_fingerprint
           ) VALUES(%s,%s,'universal-entry',%s,%s,%s,%s,%s,'Buy',10,2,%s,
                    '[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'OPEN',%s,0,
                    'BYBIT:UNIFIED',%s,%s,%s,%s,%s,%s,%s)""",
        (
            position_id,
            f"{prefix}-trade",
            strategy_id,
            version,
            signal_id,
            entry_command_id,
            symbol,
            NOW - timedelta(minutes=1),
            f"BYBIT:UNIFIED:LINEAR:USDT:{symbol}:0",
            strategy_fp,
            activation_id,
            attempt_id,
            entry_decision_id,
            entry_request_id,
            entry_fp,
            exit_fp,
        ),
    )
    connection.execute(
        """INSERT INTO strategy_exit.exit_observations(
               observation_id,strategy_position_id,exit_plan_fingerprint,
               symbol,event_kind,event_at,observed_at,received_at,
               attributes,source_refs
           ) VALUES(%s,%s,%s,%s,'PRICE',%s,%s,%s,'{}'::jsonb,%s::jsonb)""",
        (
            observation_id,
            position_id,
            exit_fp,
            symbol,
            decided_at,
            decided_at,
            decided_at,
            json.dumps([f"p6:{prefix}"]),
        ),
    )
    connection.execute(
        """INSERT INTO strategy_exit.exit_decisions(
               exit_decision_id,strategy_position_id,strategy_id,strategy_version,
               strategy_config_fingerprint,exit_plan_fingerprint,observation_id,
               rule_id,rule_priority,repeat_policy,action_kind,requested_mutation,
               source_refs,decided_at
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,'rule-p6',10,'EACH_MATCH',%s,
                    %s::jsonb,%s::jsonb,%s)""",
        (
            exit_decision_id,
            position_id,
            strategy_id,
            version,
            strategy_fp,
            exit_fp,
            observation_id,
            action_kind,
            json.dumps(mutation),
            json.dumps([f"p6:{prefix}"]),
            decided_at,
        ),
    )
    connection.commit()
    return {
        "position_id": position_id,
        "exit_decision_id": exit_decision_id,
        "exit_fp": exit_fp,
        "symbol": symbol,
    }


def _materialize_target(
    connection: psycopg.Connection,
    *,
    exit_decision_id: str,
    now: datetime,
) -> str:
    for _ in range(50):
        row = connection.execute(
            """SELECT execution_request_id
                 FROM strategy_exit.execution_requests
                WHERE exit_decision_id=%s""",
            (exit_decision_id,),
        ).fetchone()
        if row is not None:
            return str(row["execution_request_id"])
        blocked = connection.execute(
            """SELECT reason
                 FROM strategy_exit.execution_materialization_blocks
                WHERE exit_decision_id=%s""",
            (exit_decision_id,),
        ).fetchone()
        if blocked is not None:
            raise AssertionError(
                f"target decision was materialization-blocked: {blocked['reason']}"
            )
        result = materialize_once(connection, now=now)
        if result == "NO_DECISION":
            raise AssertionError("target decision was not materialized before queue emptied")
    raise AssertionError("materialization queue did not converge")


def _dispatch_target(
    connection: psycopg.Connection,
    *,
    execution_request_id: str,
    now: datetime,
) -> dict[str, object]:
    for _ in range(50):
        row = connection.execute(
            """SELECT state,command_id,reason
                 FROM strategy_exit.execution_dispatches
                WHERE execution_request_id=%s""",
            (execution_request_id,),
        ).fetchone()
        if row is not None:
            return dict(row)
        result = dispatch_once(connection, now=now)
        if result in {"NO_REQUEST", "EXECUTION_GATE_DISARMED"}:
            raise AssertionError(
                f"target request was not dispatched before queue stopped: {result}"
            )
    raise AssertionError("dispatch queue did not converge")


def test_materialize_request_is_exact_and_gate_independent() -> None:
    assert DSN is not None
    prefix = "p6-mat-" + uuid4().hex[:8]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        ids = _seed(connection, prefix)
        _gate(connection, False)
        connection.commit()

        request_id = _materialize_target(
            connection,
            exit_decision_id=ids["exit_decision_id"],
            now=NOW,
        )
        row = connection.execute(
            """SELECT r.*,d.exit_decision_id AS decision_id
                 FROM strategy_exit.execution_requests r
                 JOIN strategy_exit.exit_decisions d
                   ON d.exit_decision_id=r.exit_decision_id
                WHERE d.exit_decision_id=%s""",
            (ids["exit_decision_id"],),
        ).fetchone()
        assert row is not None
        assert row["execution_request_id"] == request_id
        assert row["strategy_position_id"] == ids["position_id"]
        assert row["exit_plan_fingerprint"] == ids["exit_fp"]
        assert row["requested_at"] == NOW
        assert row["expires_at"] == NOW + timedelta(seconds=30)
        assert dispatch_once(connection, now=NOW) == "EXECUTION_GATE_DISARMED"
        count = connection.execute(
            "SELECT count(*) FROM runtime.trade_commands WHERE command_type='strategy_exit'"
        ).fetchone()
        assert count == {"count": 0}


def test_dispatch_is_atomic_and_duplicate_publish_is_idempotent() -> None:
    assert DSN is not None
    prefix = "p6-dispatch-" + uuid4().hex[:8]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        ids = _seed(connection, prefix)
        _gate(connection, True)
        connection.commit()
        request_id = _materialize_target(
            connection,
            exit_decision_id=ids["exit_decision_id"],
            now=NOW,
        )

        result = dispatch_once(connection, now=NOW)
        assert result.startswith("DISPATCHED:")
        request = load_exit_execution_request(
            connection,
            execution_request_id=request_id,
        )
        position = exact_open_position(connection, request)
        prepared = prepare_runtime_exit_command(request, position, now=NOW)
        publish_runtime_exit_command(connection, prepared, now=NOW)
        connection.commit()

        dispatch = connection.execute(
            """SELECT state,command_id FROM strategy_exit.execution_dispatches
                WHERE execution_request_id=%s""",
            (request_id,),
        ).fetchone()
        command_count = connection.execute(
            """SELECT count(*) FROM runtime.trade_commands
                WHERE command_type='strategy_exit'
                  AND payload_json::jsonb->>'strategy_position_id'=%s""",
            (ids["position_id"],),
        ).fetchone()
        assert dispatch == {"state": "DISPATCHED", "command_id": prepared.command_id}
        assert command_count == {"count": 1}


def test_expired_exit_request_is_persisted_then_blocked_without_command() -> None:
    assert DSN is not None
    prefix = "p6-expired-" + uuid4().hex[:8]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        _seed(
            connection,
            prefix,
            decided_at=NOW - timedelta(seconds=40),
            max_age=30,
        )
        _gate(connection, True)
        connection.commit()

        request_id = _materialize_target(
            connection,
            exit_decision_id=f"{prefix}-exit-decision",
            now=NOW,
        )
        blocked = _dispatch_target(
            connection,
            execution_request_id=request_id,
            now=NOW,
        )
        assert blocked["state"] == "BLOCKED"
        assert blocked["command_id"] is None
        assert str(blocked["reason"]).startswith("REQUEST_EXPIRED:")
        command_count = connection.execute(
            """SELECT count(*) FROM runtime.trade_commands
                WHERE command_type='strategy_exit'
                  AND payload_json::jsonb->>'strategy_position_id'=%s""",
            (f"{prefix}-position",),
        ).fetchone()
        assert command_count == {"count": 0}


def test_ambiguous_exit_command_marks_exact_position_reconciliation_required() -> None:
    assert DSN is not None
    prefix = "p6-amb-" + uuid4().hex[:8]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        ids = _seed(connection, prefix)
        _gate(connection, True)
        connection.commit()
        _materialize_target(
            connection,
            exit_decision_id=ids["exit_decision_id"],
            now=NOW,
        )
        request_row = connection.execute(
            """SELECT execution_request_id FROM strategy_exit.execution_requests
                WHERE exit_decision_id=%s""",
            (ids["exit_decision_id"],),
        ).fetchone()
        assert request_row is not None
        dispatch = _dispatch_target(
            connection,
            execution_request_id=str(request_row["execution_request_id"]),
            now=NOW,
        )
        assert dispatch["state"] == "DISPATCHED"
        command_id = str(dispatch["command_id"])
        state = mark_ambiguous_exit_command(
            connection,
            command_id=command_id,
            reason="test ambiguous outcome",
        )
        connection.commit()
        assert state == "RECONCILIATION_REQUIRED"
        position = connection.execute(
            "SELECT state FROM runtime.position_ownership WHERE position_id=%s",
            (ids["position_id"],),
        ).fetchone()
        command = connection.execute(
            "SELECT error FROM runtime.trade_commands WHERE command_id=%s",
            (command_id,),
        ).fetchone()
        assert position == {"state": "RECONCILIATION_REQUIRED"}
        assert command is not None
        assert str(command["error"]).startswith("EXIT_RECONCILIATION_REQUIRED:")


def test_unsupported_mutation_is_blocked_before_runtime_command() -> None:
    assert DSN is not None
    prefix = "p6-unsupported-" + uuid4().hex[:8]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        _seed(
            connection,
            prefix,
            action_kind="SET_STOP",
            mutation={
                "stop_price": "9.9",
                "trigger_by": "LastPrice",
                "tpsl_mode": "Full",
                "order_type": "Market",
                "ignored_policy": "forbidden",
            },
        )
        _gate(connection, True)
        connection.commit()
        _materialize_target(
            connection,
            exit_decision_id=f"{prefix}-exit-decision",
            now=NOW,
        )
        request_row = connection.execute(
            """SELECT execution_request_id FROM strategy_exit.execution_requests
                WHERE exit_decision_id=%s""",
            (f"{prefix}-exit-decision",),
        ).fetchone()
        assert request_row is not None
        blocked = _dispatch_target(
            connection,
            execution_request_id=str(request_row["execution_request_id"]),
            now=NOW,
        )
        assert blocked["state"] == "BLOCKED"
        assert str(blocked["reason"]).startswith("POLICY_UNSUPPORTED:")
        count = connection.execute(
            """SELECT count(*) FROM runtime.trade_commands
                WHERE command_type='strategy_exit'
                  AND payload_json::jsonb->>'strategy_position_id'=%s""",
            (f"{prefix}-position",),
        ).fetchone()
        assert count == {"count": 0}
