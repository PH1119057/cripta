from __future__ import annotations

import json
import os
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from bybit_workbench.lifecycle_ack import claim_exit_position, record_plan_consumption
from bybit_workbench.lifecycle_supervisor import (
    LifecycleSupervisor,
    LifecycleSupervisorPolicy,
)
from tests.test_universal_exit_p6_postgres import (
    NOW,
    _dispatch_target,
    _gate,
    _materialize_target,
    _seed,
)

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="set CRIPTA_TRADE_LIFECYCLE_TEST_DSN to an explicitly disposable PostgreSQL DB",
)


def _identity(prefix: str) -> dict[str, str]:
    return {
        "strategy_id": f"{prefix}-strategy",
        "version": "1",
        "strategy_fp": f"{prefix}-strategy-fp",
        "activation_id": f"{prefix}-activation",
        "entry_fp": f"{prefix}-entry-fp",
        "exit_fp": f"{prefix}-exit-fp",
        "signal_id": f"{prefix}-signal",
        "attempt_id": f"{prefix}-attempt",
        "entry_decision_id": f"{prefix}-entry-decision",
        "entry_request_id": f"{prefix}-entry-request",
        "position_id": f"{prefix}-position",
        "trade_id": f"{prefix}-trade",
    }


def _ack_entry_plan(connection: psycopg.Connection, prefix: str) -> str:
    ids = _identity(prefix)
    return record_plan_consumption(
        connection,
        plan_kind="ENTRY",
        plan_fingerprint=ids["entry_fp"],
        strategy_activation_id=ids["activation_id"],
        consumer_instance_id="entry-engine:p7-test",
        seen_at=NOW,
        payload={"test": "p7"},
    )


def _seed_entry_execution_evidence(
    connection: psycopg.Connection,
    prefix: str,
) -> None:
    ids = _identity(prefix)
    reservation_id = f"{prefix}-reservation"
    slot_claim_id = f"{prefix}-slot"
    position_mode_state_ref = f"{prefix}-pmode"
    connection.execute(
        """INSERT INTO strategy_entry.execution_dispatches(
               dispatch_id,execution_request_id,command_id,state,reason,
               strategy_attempt_id,entry_decision_id,signal_id,strategy_id,
               strategy_version,strategy_config_fingerprint,
               entry_plan_fingerprint,exit_plan_fingerprint,payload,
               capital_reservation_id,exchange_position_slot_claim_id,
               position_mode_state_ref
           ) VALUES(
               %s,%s,%s,'DISPATCHED','p7 exact dispatch',%s,%s,%s,%s,%s,%s,%s,%s,
               '{}'::jsonb,%s,%s,%s
           )""",
        (
            f"{prefix}-entry-dispatch",
            ids["entry_request_id"],
            f"{prefix}-entry-command-runtime",
            ids["attempt_id"],
            ids["entry_decision_id"],
            ids["signal_id"],
            ids["strategy_id"],
            ids["version"],
            ids["strategy_fp"],
            ids["entry_fp"],
            ids["exit_fp"],
            reservation_id,
            slot_claim_id,
            position_mode_state_ref,
        ),
    )


def _dispatch_exit(connection: psycopg.Connection, prefix: str) -> tuple[str, str]:
    _gate(connection, True)
    connection.commit()
    request_id = _materialize_target(
        connection,
        exit_decision_id=f"{prefix}-exit-decision",
        now=NOW,
    )
    dispatch = _dispatch_target(
        connection,
        execution_request_id=request_id,
        now=NOW,
    )
    assert dispatch["state"] == "DISPATCHED"
    command_id = str(dispatch["command_id"])
    connection.execute(
        """UPDATE runtime.trade_commands
              SET state='completed',
                  started_at_epoch_ms=%s,
                  finished_at_epoch_ms=%s,
                  result_json=%s
            WHERE command_id=%s""",
        (
            int((NOW + timedelta(seconds=2)).timestamp() * 1000),
            int((NOW + timedelta(seconds=3)).timestamp() * 1000),
            json.dumps({"retCode": 0}),
            command_id,
        ),
    )
    connection.commit()
    return request_id, command_id


def _close_with_economics(
    connection: psycopg.Connection,
    prefix: str,
    command_id: str,
) -> None:
    ids = _identity(prefix)
    closed_at = NOW + timedelta(seconds=4)
    connection.execute(
        """UPDATE runtime.position_ownership
              SET state='CLOSED',closed_at=%s,close_link_status='EXACT'
            WHERE position_id=%s""",
        (closed_at, ids["position_id"]),
    )
    connection.execute(
        """INSERT INTO runtime.position_exit_attribution(
               attribution_id,position_id,trade_id,closed_at,link_status,
               link_method,exit_owner,exit_mechanism,exit_execution_ids,
               actual_avg_entry,actual_exit_avg_fill,actual_exit_qty,
               gross_pnl,entry_fee_actual,exit_fee_actual,
               actual_net_without_funding,economics_completeness,evidence,
               exit_order_ids
           ) VALUES(
               %s,%s,%s,%s,'EXACT','P7_DISPOSABLE_TEST','ALGORITHM',
               'STRATEGY_EXIT',%s::jsonb,10,10.2,2,0.4,0.01,0.01,0.38,
               'PARTIAL_NO_FUNDING','{}'::jsonb,'[]'::jsonb
           )""",
        (
            f"{prefix}-attribution",
            ids["position_id"],
            ids["trade_id"],
            closed_at,
            json.dumps([command_id]),
        ),
    )
    connection.commit()


def test_exit_claim_is_idempotent_and_cannot_be_taken_over() -> None:
    assert DSN is not None
    prefix = "p7-claim-" + uuid4().hex[:8]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        _seed(connection, prefix)
        first = claim_exit_position(
            connection,
            strategy_position_id=f"{prefix}-position",
            consumer_instance_id="exit-engine-a",
            claimed_at=NOW,
        )
        second = claim_exit_position(
            connection,
            strategy_position_id=f"{prefix}-position",
            consumer_instance_id="exit-engine-a",
            claimed_at=NOW + timedelta(seconds=1),
        )
        connection.commit()
        assert first == second
        row = connection.execute(
            """SELECT claim_id,consumer_instance_id,last_seen_at,status
                 FROM runtime.position_exit_claims
                WHERE strategy_position_id=%s""",
            (f"{prefix}-position",),
        ).fetchone()
        assert row is not None
        assert row["claim_id"] == first
        assert row["consumer_instance_id"] == "exit-engine-a"
        assert row["last_seen_at"] == NOW + timedelta(seconds=1)
        assert row["status"] == "CLAIMED"
        consumption = connection.execute(
            """SELECT plan_kind,consumer_kind,status
                 FROM runtime.plan_consumptions
                WHERE exit_plan_fingerprint=%s
                  AND strategy_activation_id=%s""",
            (f"{prefix}-exit-fp", f"{prefix}-activation"),
        ).fetchone()
        assert consumption == {
            "plan_kind": "EXIT",
            "consumer_kind": "EXIT_ENGINE",
            "status": "LOADED",
        }
        with pytest.raises(
            RuntimeError,
            match="already claimed by another Exit Engine",
        ):
            claim_exit_position(
                connection,
                strategy_position_id=f"{prefix}-position",
                consumer_instance_id="exit-engine-b",
                claimed_at=NOW + timedelta(seconds=2),
            )


def test_position_without_exit_owner_is_critical_and_recovers_after_restart() -> None:
    assert DSN is not None
    prefix = "p7-owner-" + uuid4().hex[:8]
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        _seed(connection, prefix)
        _ack_entry_plan(connection, prefix)
        connection.commit()

        LifecycleSupervisor(connection).scan(now=NOW)
        fault = connection.execute(
            """SELECT fault_id,severity,state
                 FROM runtime.lifecycle_faults
                WHERE fault_code='POSITION_WITHOUT_EXIT_OWNER'
                  AND strategy_position_id=%s""",
            (f"{prefix}-position",),
        ).fetchone()
        assert fault is not None
        assert fault["severity"] == "CRITICAL"
        assert fault["state"] == "OPEN"

        claim_exit_position(
            connection,
            strategy_position_id=f"{prefix}-position",
            consumer_instance_id="exit-engine:p7-recovery",
            claimed_at=NOW + timedelta(seconds=1),
        )
        connection.commit()

        restarted = LifecycleSupervisor(connection)
        restarted.scan(now=NOW + timedelta(seconds=2))
        resolved = connection.execute(
            """SELECT state,resolved_at
                 FROM runtime.lifecycle_faults
                WHERE fault_id=%s""",
            (fault["fault_id"],),
        ).fetchone()
        assert resolved is not None
        assert resolved["state"] == "RESOLVED"
        assert resolved["resolved_at"] == NOW + timedelta(seconds=2)
        claim_event = connection.execute(
            """SELECT count(*) AS count
                 FROM runtime.trade_lifecycle_events
                WHERE event_type='EXIT_POSITION_CLAIMED'
                  AND strategy_position_id=%s""",
            (f"{prefix}-position",),
        ).fetchone()
        assert claim_event == {"count": 1}


def test_full_lifecycle_projection_is_exact_and_restart_idempotent() -> None:
    assert DSN is not None
    prefix = "p7-full-" + uuid4().hex[:8]
    ids = _identity(prefix)
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        _seed(connection, prefix)
        _ack_entry_plan(connection, prefix)
        _seed_entry_execution_evidence(connection, prefix)
        claim_exit_position(
            connection,
            strategy_position_id=ids["position_id"],
            consumer_instance_id="exit-engine:p7-full",
            claimed_at=NOW + timedelta(seconds=1),
        )
        connection.commit()
        request_id, command_id = _dispatch_exit(connection, prefix)
        _close_with_economics(connection, prefix, command_id)

        first = LifecycleSupervisor(connection).scan(now=NOW + timedelta(seconds=5))
        assert first.projected_events > 0
        event_rows = connection.execute(
            """SELECT DISTINCT event_type
                 FROM runtime.trade_lifecycle_events
                WHERE strategy_id=%s
                   OR strategy_activation_id=%s
                   OR strategy_position_id=%s""",
            (ids["strategy_id"], ids["activation_id"], ids["position_id"]),
        ).fetchall()
        event_types = {str(row["event_type"]) for row in event_rows}
        expected = {
            "STRATEGY_ACTIVATED",
            "PLANS_MATERIALIZED",
            "PLANS_PUBLISHED",
            "ENTRY_PLAN_CONSUMED",
            "STRATEGY_SIGNAL_CREATED",
            "ENTRY_ATTEMPT_CREATED",
            "CAPITAL_RESERVED",
            "ENTRY_DECIDED",
            "ENTRY_REQUEST_CREATED",
            "ENTRY_REQUEST_DISPATCHED",
            "ENTRY_ORDER_ACKNOWLEDGED",
            "ENTRY_FILLED",
            "STRATEGY_POSITION_CREATED",
            "EXIT_PLAN_BOUND",
            "EXIT_POSITION_CLAIMED",
            "EXIT_DECISION_CREATED",
            "EXIT_REQUEST_CREATED",
            "EXIT_REQUEST_DISPATCHED",
            "EXIT_MUTATION_ACKNOWLEDGED",
            "EXIT_MUTATION_CONFIRMED",
            "POSITION_CLOSED",
            "ECONOMICS_FINALIZED",
        }
        assert expected <= event_types
        row = connection.execute(
            """SELECT exact_ids
                 FROM runtime.trade_lifecycle_events
                WHERE event_type='EXIT_REQUEST_DISPATCHED'
                  AND exit_execution_request_id=%s""",
            (request_id,),
        ).fetchone()
        assert row is not None
        assert row["exact_ids"]["exit_execution_request_id"] == request_id
        assert row["exact_ids"]["command_id"] == command_id

        count_before = connection.execute(
            "SELECT count(*) AS count FROM runtime.trade_lifecycle_events"
        ).fetchone()
        assert count_before is not None
        restarted = LifecycleSupervisor(connection)
        second = restarted.scan(now=NOW + timedelta(seconds=5))
        count_after = connection.execute(
            "SELECT count(*) AS count FROM runtime.trade_lifecycle_events"
        ).fetchone()
        assert count_after == count_before
        assert second.projected_events == 0
        own_open_faults = connection.execute(
            """SELECT count(*) AS count
                 FROM runtime.lifecycle_faults
                WHERE strategy_position_id=%s AND state='OPEN'""",
            (ids["position_id"],),
        ).fetchone()
        assert own_open_faults == {"count": 0}


def test_exchange_divergence_requires_explicit_fresh_reconciliation() -> None:
    assert DSN is not None
    prefix = "p7-exchange-" + uuid4().hex[:8]
    ids = _identity(prefix)
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        seeded = _seed(connection, prefix)
        _ack_entry_plan(connection, prefix)
        claim_exit_position(
            connection,
            strategy_position_id=ids["position_id"],
            consumer_instance_id="exit-engine:p7-exchange",
            claimed_at=NOW,
        )
        connection.commit()

        LifecycleSupervisor(connection).scan(now=NOW)
        absent = connection.execute(
            """SELECT count(*) AS count FROM runtime.lifecycle_faults
                WHERE fault_code='EXCHANGE_STATE_DIVERGED'
                  AND strategy_position_id=%s""",
            (ids["position_id"],),
        ).fetchone()
        assert absent == {"count": 0}

        now_ms = int(NOW.timestamp() * 1000)
        connection.execute(
            """INSERT INTO runtime.reconciliation_runs(
                   started_at_epoch_ms,finished_at_epoch_ms,reason,ok,
                   positions,orders,error
               ) VALUES(%s,%s,'p7-test',1,0,0,'')""",
            (now_ms - 100, now_ms),
        )
        connection.commit()
        LifecycleSupervisor(
            connection,
            policy=LifecycleSupervisorPolicy(exchange_state_max_age_seconds=5),
        ).scan(now=NOW + timedelta(seconds=1))
        diverged = connection.execute(
            """SELECT state FROM runtime.lifecycle_faults
                WHERE fault_code='EXCHANGE_STATE_DIVERGED'
                  AND strategy_position_id=%s""",
            (ids["position_id"],),
        ).fetchone()
        assert diverged == {"state": "OPEN"}

        LifecycleSupervisor(
            connection,
            policy=LifecycleSupervisorPolicy(exchange_state_max_age_seconds=1),
        ).scan(now=NOW + timedelta(seconds=3))
        still_open = connection.execute(
            """SELECT state FROM runtime.lifecycle_faults
                WHERE fault_code='EXCHANGE_STATE_DIVERGED'
                  AND strategy_position_id=%s""",
            (ids["position_id"],),
        ).fetchone()
        assert still_open == {"state": "OPEN"}

        connection.execute(
            """INSERT INTO runtime.hot_positions(
                   symbol,position_idx,side,size,entry_price,leverage,
                   exchange_updated_ms,refreshed_at_epoch_ms,payload_json
               ) VALUES(%s,0,'Buy','2','10','1',%s,%s,'{}')
               ON CONFLICT(symbol,position_idx) DO UPDATE
                 SET side=excluded.side,size=excluded.size,
                     refreshed_at_epoch_ms=excluded.refreshed_at_epoch_ms,
                     payload_json=excluded.payload_json""",
            (seeded["symbol"], now_ms, now_ms),
        )
        connection.commit()
        LifecycleSupervisor(
            connection,
            policy=LifecycleSupervisorPolicy(exchange_state_max_age_seconds=5),
        ).scan(now=NOW + timedelta(seconds=4))
        resolved = connection.execute(
            """SELECT state FROM runtime.lifecycle_faults
                WHERE fault_code='EXCHANGE_STATE_DIVERGED'
                  AND strategy_position_id=%s""",
            (ids["position_id"],),
        ).fetchone()
        assert resolved == {"state": "RESOLVED"}
