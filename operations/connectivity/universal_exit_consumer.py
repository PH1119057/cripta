from __future__ import annotations

import os
import signal
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bybit_workbench.universal_entry.contracts import FrozenPolicy
from bybit_workbench.universal_exit.contracts import ExitActionKind, ExitDecision
from bybit_workbench.universal_exit.execution_bridge import (
    ExitExecutionBridgeBlocked,
    prepare_exit_execution_request,
    prepare_runtime_exit_command,
)
from bybit_workbench.universal_exit.execution_store import (
    exact_open_position,
    persist_exit_execution_request,
    publish_runtime_exit_command,
    record_exit_dispatch_blocked,
    record_exit_materialization_block,
)
from bybit_workbench.universal_exit.loader import load_exit_binding

DB_DSN = os.environ.get(
    "CRIPTA_DATABASE_DSN",
    "dbname=cripta user=cripta host=/var/run/postgresql application_name=universal-exit-consumer",
)
CONSUMER_ARM = os.environ.get("CRIPTA_UNIVERSAL_EXIT_MAINNET_CONSUMER", "DISABLED").strip().upper()
POLL_SECONDS = float(os.environ.get("CRIPTA_UNIVERSAL_EXIT_CONSUMER_POLL_SECONDS", "0.25"))
running = True


def _stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def _execution_gate_enabled(connection: psycopg.Connection[Any]) -> bool:
    row = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    return bool(row and row["enabled"])


def _next_decision(connection: psycopg.Connection[Any]) -> Mapping[str, object] | None:
    return connection.execute(
        """SELECT d.*
             FROM strategy_exit.exit_decisions d
            WHERE NOT EXISTS (
                SELECT 1 FROM strategy_exit.execution_requests r
                 WHERE r.exit_decision_id=d.exit_decision_id
            )
              AND NOT EXISTS (
                SELECT 1 FROM strategy_exit.execution_materialization_blocks b
                 WHERE b.exit_decision_id=d.exit_decision_id
            )
            ORDER BY d.decided_at,d.exit_decision_id
            LIMIT 1"""
    ).fetchone()


def _decision_from_row(row: Mapping[str, object]) -> ExitDecision:
    decided_at = row["decided_at"]
    mutation = row["requested_mutation"]
    refs = row["source_refs"]
    if not isinstance(decided_at, datetime):
        raise RuntimeError("ExitDecision.decided_at invalid")
    if not isinstance(mutation, Mapping):
        raise RuntimeError("ExitDecision.requested_mutation invalid")
    if not isinstance(refs, list):
        raise RuntimeError("ExitDecision.source_refs invalid")
    return ExitDecision(
        exit_decision_id=str(row["exit_decision_id"]),
        strategy_position_id=str(row["strategy_position_id"]),
        strategy_id=str(row["strategy_id"]),
        strategy_version=str(row["strategy_version"]),
        strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
        exit_plan_fingerprint=str(row["exit_plan_fingerprint"]),
        rule_id=str(row["rule_id"]),
        action_kind=ExitActionKind(str(row["action_kind"])),
        requested_mutation=FrozenPolicy.from_mapping(mutation),
        source_refs=tuple(str(value) for value in refs),
        decided_at=decided_at.astimezone(UTC),
    )


def _next_request(connection: psycopg.Connection[Any]) -> Mapping[str, object] | None:
    return connection.execute(
        """SELECT r.*
             FROM strategy_exit.execution_requests r
            WHERE NOT EXISTS (
                SELECT 1 FROM strategy_exit.execution_dispatches d
                 WHERE d.execution_request_id=r.execution_request_id
            )
            ORDER BY r.requested_at,r.execution_request_id
            LIMIT 1"""
    ).fetchone()


def _request_id_from_row(row: Mapping[str, object]) -> str:
    return str(row["execution_request_id"])


def materialize_once(
    connection: psycopg.Connection[Any],
    *,
    now: datetime,
) -> str:
    row = _next_decision(connection)
    if row is None:
        return "NO_DECISION"
    decision = _decision_from_row(row)
    position, plan = load_exit_binding(
        connection,
        strategy_position_id=decision.strategy_position_id,
    )
    try:
        request = prepare_exit_execution_request(
            decision,
            position,
            plan,
            now=now,
        )
    except ExitExecutionBridgeBlocked as exc:
        record_exit_materialization_block(
            connection,
            exit_decision_id=decision.exit_decision_id,
            strategy_position_id=decision.strategy_position_id,
            reason=f"{exc.code.value}:{exc.reason}",
            payload={"block_code": exc.code.value, "reason": exc.reason},
        )
        return f"DECISION_BLOCKED:{exc.code.value}"
    persist_exit_execution_request(connection, request)
    return f"REQUEST_CREATED:{request.execution_request_id}"


def dispatch_once(
    connection: psycopg.Connection[Any],
    *,
    now: datetime,
) -> str:
    if not _execution_gate_enabled(connection):
        return "EXECUTION_GATE_DISARMED"
    row = _next_request(connection)
    if row is None:
        return "NO_REQUEST"
    request_id = _request_id_from_row(row)
    from bybit_workbench.universal_exit.execution_store import load_exit_execution_request

    request = load_exit_execution_request(
        connection,
        execution_request_id=request_id,
    )
    try:
        position = exact_open_position(connection, request)
        prepared = prepare_runtime_exit_command(
            request,
            position,
            now=now,
        )
        publish_runtime_exit_command(connection, prepared, now=now)
    except ExitExecutionBridgeBlocked as exc:
        with connection.transaction():
            record_exit_dispatch_blocked(
                connection,
                request,
                reason=f"{exc.code.value}:{exc.reason}",
                payload={"block_code": exc.code.value, "reason": exc.reason},
            )
        return f"BLOCKED:{exc.code.value}"
    except RuntimeError as exc:
        if str(exc).startswith("EXIT_MUTATION_IN_FLIGHT:"):
            return str(exc)
        with connection.transaction():
            record_exit_dispatch_blocked(
                connection,
                request,
                reason=f"STRUCTURAL_ERROR:{type(exc).__name__}:{exc}",
                payload={"error": f"{type(exc).__name__}:{exc}"},
            )
        return "BLOCKED:STRUCTURAL_ERROR"
    return f"DISPATCHED:{prepared.command_id}"


def run_once(
    connection: psycopg.Connection[Any],
    *,
    now: datetime | None = None,
) -> str:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    materialized = materialize_once(connection, now=current)
    if materialized.startswith("REQUEST_CREATED:"):
        return materialized
    if materialized.startswith("DECISION_BLOCKED:"):
        return materialized
    return dispatch_once(connection, now=current)


def main() -> int:
    global running
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    if CONSUMER_ARM != "ENABLED":
        raise SystemExit("Universal Exit mainnet consumer is explicitly disabled")
    connection = psycopg.connect(DB_DSN, autocommit=False, row_factory=dict_row)
    try:
        while running:
            run_once(connection)
            connection.commit()
            time.sleep(POLL_SECONDS)
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
