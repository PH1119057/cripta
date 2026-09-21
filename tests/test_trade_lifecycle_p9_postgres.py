from __future__ import annotations

import os
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from bybit_workbench.lifecycle_ack import mark_exit_claims_stale
from bybit_workbench.lifecycle_supervisor import (
    LifecycleSupervisor,
    LifecycleSupervisorPolicy,
)
from operations.monitoring.lifecycle_supervisor_runtime import scan_once
from operations.monitoring.universal_exit_shadow_runtime import claim_cycle
from tests.test_universal_exit_p6_postgres import NOW, _seed

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="set CRIPTA_TRADE_LIFECYCLE_TEST_DSN to an explicitly disposable PostgreSQL DB",
)


def _fault_state(
    connection: psycopg.Connection,
    position_id: str,
) -> str | None:
    row = connection.execute(
        """SELECT state
             FROM runtime.lifecycle_faults
            WHERE fault_code='POSITION_WITHOUT_EXIT_OWNER'
              AND strategy_position_id=%s""",
        (position_id,),
    ).fetchone()
    return None if row is None else str(row["state"])


def test_p9_exit_owner_heartbeat_restart_and_recovery_are_fail_closed() -> None:
    assert DSN is not None
    prefix = "p9-owner-" + uuid4().hex[:8]
    consumer_id = "universal-exit-shadow-v1"

    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        ids = _seed(connection, prefix)
        position_id = ids["position_id"]
        claimed = claim_cycle(
            connection,
            now=NOW,
            consumer_instance_id=consumer_id,
        )
        connection.commit()
        assert position_id in claimed.claimed_positions

        supervisor = LifecycleSupervisor(
            connection,
            policy=LifecycleSupervisorPolicy(exit_owner_max_age_seconds=3),
        )
        supervisor.scan(now=NOW + timedelta(seconds=1))
        assert _fault_state(connection, position_id) in {None, "RESOLVED"}

        supervisor.scan(now=NOW + timedelta(seconds=5))
        assert _fault_state(connection, position_id) == "OPEN"

    # Simulate process restart: new DB connection, same stable logical consumer.
    with psycopg.connect(DSN, row_factory=dict_row) as restarted_connection:
        claimed = claim_cycle(
            restarted_connection,
            now=NOW + timedelta(seconds=6),
            consumer_instance_id=consumer_id,
        )
        restarted_connection.commit()
        assert f"{prefix}-position" in claimed.claimed_positions

        LifecycleSupervisor(
            restarted_connection,
            policy=LifecycleSupervisorPolicy(exit_owner_max_age_seconds=3),
        ).scan(now=NOW + timedelta(seconds=7))
        assert _fault_state(restarted_connection, f"{prefix}-position") == "RESOLVED"

        claim = restarted_connection.execute(
            """SELECT consumer_instance_id,status,last_seen_at
                 FROM runtime.position_exit_claims
                WHERE strategy_position_id=%s""",
            (f"{prefix}-position",),
        ).fetchone()
        assert claim is not None
        assert claim["consumer_instance_id"] == consumer_id
        assert claim["status"] == "CLAIMED"
        assert claim["last_seen_at"] == NOW + timedelta(seconds=6)


def test_p9_reconciliation_required_position_keeps_exact_exit_owner() -> None:
    assert DSN is not None
    prefix = "p9-reconcile-" + uuid4().hex[:8]
    consumer_id = "universal-exit-shadow-v1"
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        ids = _seed(connection, prefix)
        connection.execute(
            """UPDATE runtime.position_ownership
                  SET state='RECONCILIATION_REQUIRED'
                WHERE position_id=%s""",
            (ids["position_id"],),
        )
        connection.commit()

        claimed = claim_cycle(
            connection,
            now=NOW,
            consumer_instance_id=consumer_id,
        )
        connection.commit()
        assert ids["position_id"] in claimed.claimed_positions
        row = connection.execute(
            """SELECT status,exit_plan_fingerprint
                 FROM runtime.position_exit_claims
                WHERE strategy_position_id=%s""",
            (ids["position_id"],),
        ).fetchone()
        assert row == {
            "status": "CLAIMED",
            "exit_plan_fingerprint": ids["exit_fp"],
        }


def test_p9_shadow_restart_does_not_create_mutation_or_lose_reservation() -> None:
    assert DSN is not None
    prefix = "p9-nomutation-" + uuid4().hex[:8]
    consumer_id = "universal-exit-shadow-v1"
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        ids = _seed(connection, prefix)
        reservation_id = f"{prefix}-reservation"
        before_reservation = connection.execute(
            """SELECT state,state_reason,strategy_position_id
                 FROM runtime.capital_reservations
                WHERE reservation_id=%s""",
            (reservation_id,),
        ).fetchone()
        assert before_reservation is not None
        connection.commit()

        before_commands = connection.execute(
            "SELECT count(*) AS count FROM runtime.trade_commands"
        ).fetchone()
        assert before_commands is not None

        claim_cycle(connection, now=NOW, consumer_instance_id=consumer_id)
        LifecycleSupervisor(
            connection,
            policy=LifecycleSupervisorPolicy(exit_owner_max_age_seconds=10),
        ).scan(now=NOW + timedelta(seconds=1))
        mark_exit_claims_stale(
            connection,
            consumer_instance_id=consumer_id,
            seen_at=NOW + timedelta(seconds=2),
            reason="p9 restart test",
        )
        claim_cycle(
            connection,
            now=NOW + timedelta(seconds=3),
            consumer_instance_id=consumer_id,
        )
        connection.commit()

        reservation = connection.execute(
            """SELECT state,state_reason,strategy_position_id
                 FROM runtime.capital_reservations
                WHERE reservation_id=%s""",
            (reservation_id,),
        ).fetchone()
        after_commands = connection.execute(
            "SELECT count(*) AS count FROM runtime.trade_commands"
        ).fetchone()
        exit_requests = connection.execute(
            """SELECT count(*) AS count
                 FROM strategy_exit.execution_requests
                WHERE strategy_position_id=%s""",
            (ids["position_id"],),
        ).fetchone()
        assert reservation == before_reservation
        assert after_commands == before_commands
        assert exit_requests == {"count": 0}


def test_p9_graceful_stop_stale_claim_opens_fault_until_restart() -> None:
    assert DSN is not None
    prefix = "p9-stale-" + uuid4().hex[:8]
    consumer_id = "universal-exit-shadow-v1"
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        ids = _seed(connection, prefix)
        claim_cycle(connection, now=NOW, consumer_instance_id=consumer_id)
        mark_exit_claims_stale(
            connection,
            consumer_instance_id=consumer_id,
            seen_at=NOW + timedelta(seconds=1),
            reason="graceful stop",
        )
        connection.commit()

        LifecycleSupervisor(
            connection,
            policy=LifecycleSupervisorPolicy(exit_owner_max_age_seconds=10),
        ).scan(now=NOW + timedelta(seconds=1))
        assert _fault_state(connection, ids["position_id"]) == "OPEN"

        claim_cycle(
            connection,
            now=NOW + timedelta(seconds=2),
            consumer_instance_id=consumer_id,
        )
        LifecycleSupervisor(
            connection,
            policy=LifecycleSupervisorPolicy(exit_owner_max_age_seconds=10),
        ).scan(now=NOW + timedelta(seconds=3))
        assert _fault_state(connection, ids["position_id"]) == "RESOLVED"


def test_p9_long_running_cycles_return_connection_to_idle_transaction_state() -> None:
    assert DSN is not None
    prefix = "p9-tx-scope-" + uuid4().hex[:8]
    consumer_id = "universal-exit-shadow-v1"
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        ids = _seed(connection, prefix)
        connection.commit()

        with connection.transaction():
            claim_cycle(
                connection,
                now=NOW,
                consumer_instance_id=consumer_id,
            )
        assert connection.info.transaction_status is TransactionStatus.IDLE

        result = scan_once(
            connection,
            now=NOW + timedelta(seconds=1),
            policy=LifecycleSupervisorPolicy(exit_owner_max_age_seconds=10),
        )
        assert result.projected_events >= 0
        assert connection.info.transaction_status is TransactionStatus.IDLE
        assert ids["position_id"]
