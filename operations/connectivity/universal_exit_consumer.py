from __future__ import annotations

import importlib
import json
import os
import signal
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row, tuple_row

from bybit_workbench.universal_entry.contracts import FrozenPolicy
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import VERSION as P90_REAL_VERSION
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
CONSUMER_ARM = os.environ.get(
    "CRIPTA_UNIVERSAL_EXIT_MAINNET_CONSUMER", "DISABLED"
).strip().upper()
P90_R06_OWNER = os.environ.get(
    "CRIPTA_P90_R06_EXIT_OWNER", "DISABLED"
).strip().upper()
LOADED_COMMIT = os.environ.get("CRIPTA_RELEASE_COMMIT", "").strip().lower()
POLL_SECONDS = float(os.environ.get("CRIPTA_UNIVERSAL_EXIT_CONSUMER_POLL_SECONDS", "0.25"))
STATUS_PATH = Path(os.environ.get(
    "CRIPTA_P90_R06_EXIT_STATUS_PATH",
    "/var/lib/cripta-p90-r06-exit/status.json",
))
running = True


def _stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def _p90_status(*, state: str, result: str, error: str | None = None) -> None:
    if P90_R06_OWNER != "ENABLED":
        return
    payload = {
        "service": "p90_r06_exit_owner",
        "state": state,
        "result": result,
        "release_commit": LOADED_COMMIT,
        "p90_r06_exit_owner": P90_R06_OWNER,
        "generic_consumer": CONSUMER_ARM,
        "observed_at": datetime.now(UTC).isoformat(),
        "error": error,
    }
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(STATUS_PATH)


def _execution_gate_enabled(connection: psycopg.Connection[Any]) -> bool:
    row = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    return bool(row and row["enabled"])


def _next_decision(connection: psycopg.Connection[Any]) -> Mapping[str, object] | None:
    return connection.execute(
        """SELECT d.*
             FROM strategy_exit.exit_decisions d
            WHERE NOT (
                d.strategy_version=%s AND d.strategy_id IN (%s,%s)
            )
              AND NOT EXISTS (
                SELECT 1 FROM strategy_exit.execution_requests r
                 WHERE r.exit_decision_id=d.exit_decision_id
            )
              AND NOT EXISTS (
                SELECT 1 FROM strategy_exit.execution_materialization_blocks b
                 WHERE b.exit_decision_id=d.exit_decision_id
            )
            ORDER BY d.decided_at,d.exit_decision_id
            LIMIT 1""",
        (P90_REAL_VERSION, MAIN_ID, SUB_ID),
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
            WHERE r.identity_status='PROVEN'
              AND r.trading_account_id IS NOT NULL
              AND NOT (
                  r.strategy_version=%s AND r.strategy_id IN (%s,%s)
              )
              AND NOT EXISTS (
                SELECT 1 FROM strategy_exit.execution_dispatches d
                 WHERE d.execution_request_id=r.execution_request_id
            )
            ORDER BY r.requested_at,r.execution_request_id
            LIMIT 1""",
        (P90_REAL_VERSION, MAIN_ID, SUB_ID),
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
    # Exit ownership survives Entry disarm. The mainnet gate controls new Entry,
    # not lifecycle completion of an already-owned StrategyPosition.
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
    source_connection: psycopg.Connection[Any] | None = None,
    now: datetime | None = None,
    run_generic: bool = True,
) -> str:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    # A protected OPEN position is an Exit obligation even when new Entry is
    # disarmed. P90 first-fill TRAIL035 remains independently owned here.
    firstfill_module = importlib.import_module(
        f"{__package__}.p90_real_independent_exit"
        if __package__
        else "p90_real_independent_exit"
    )
    p90 = str(firstfill_module.run_p90_exit_once(connection, now=current))
    firstfill_module.audit_firstfill_exit_result(
        connection, result=p90, observed_at=current,
    )
    p90_success = {
        "P90_NO_PROTECTED_POSITION_REQUIRING_TRAIL",
        "P90_EXIT_TRAIL035_REQUEST_PUBLISHED",
        "P90_EXIT_NO_MUTATION_PROVED",
    }
    p90_firstfill_blocked = (
        P90_R06_OWNER == "ENABLED" and p90 not in p90_success
    )
    # A first-fill denial is durable safety evidence. Commit it BEFORE the
    # independent continuous owner runs, so a later source/private transient
    # cannot erase the exact reason by rolling back this cycle.
    if p90_firstfill_blocked:
        connection.commit()

    continuous = ""
    if P90_R06_OWNER == "ENABLED":
        if source_connection is None:
            raise RuntimeError("P90_R06_EXIT_OWNER_SOURCE_CONNECTION_REQUIRED")
        continuous_module = importlib.import_module(
            f"{__package__}.p90_r06_exit_owner"
            if __package__
            else "p90_r06_exit_owner"
        )
        continuous = str(continuous_module.run_p90_r06_exit_once(
            connection,
            source_connection,
            loaded_commit=LOADED_COMMIT,
            now=current,
        ))
        if not run_generic:
            if p90_firstfill_blocked:
                return (
                    "P90_R06_FIRSTFILL_OWNER_BLOCKED|FIRSTFILL="
                    + p90 + "|CONTINUOUS=" + continuous
                )
            return (
                p90 + "|" + continuous
                if p90 == "P90_EXIT_TRAIL035_REQUEST_PUBLISHED"
                else continuous
            )

    # Preserve the original priority contract: a freshly published P90
    # trailing request ends this cycle so generic materialization cannot race
    # or duplicate the account-local mutation.
    if p90 == "P90_EXIT_TRAIL035_REQUEST_PUBLISHED":
        return p90

    if run_generic:
        materialized = materialize_once(connection, now=current)
        if materialized.startswith("REQUEST_CREATED:"):
            generic = materialized
        elif materialized.startswith("DECISION_BLOCKED:"):
            dispatched = dispatch_once(connection, now=current)
            generic = dispatched if dispatched != "NO_REQUEST" else materialized
        else:
            generic = dispatch_once(connection, now=current)
        if p90_firstfill_blocked:
            return (
                "P90_R06_FIRSTFILL_OWNER_BLOCKED|FIRSTFILL="
                + p90 + "|CONTINUOUS=" + continuous + "|GENERIC=" + generic
            )
        return generic

    return p90


def main() -> int:
    global running
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    if CONSUMER_ARM not in {"DISABLED", "ENABLED"}:
        raise SystemExit("Universal Exit generic consumer mode is invalid")
    if P90_R06_OWNER not in {"DISABLED", "ENABLED"}:
        raise SystemExit("P90 R06 Exit owner mode is invalid")
    if CONSUMER_ARM != "ENABLED" and P90_R06_OWNER != "ENABLED":
        raise SystemExit("Universal Exit consumers are explicitly disabled")
    if (
        P90_R06_OWNER == "ENABLED"
        and (
            len(LOADED_COMMIT) != 40
            or any(c not in "0123456789abcdef" for c in LOADED_COMMIT)
        )
    ):
        raise SystemExit("P90 R06 Exit owner release identity is invalid")

    connection = psycopg.connect(
        DB_DSN,
        autocommit=False,
        row_factory=dict_row,
        options="-c idle_in_transaction_session_timeout=120000",
    )
    source_connection = (
        psycopg.connect(
            DB_DSN,
            autocommit=False,
            row_factory=tuple_row,
            options="-c idle_in_transaction_session_timeout=120000",
        )
        if P90_R06_OWNER == "ENABLED"
        else None
    )
    try:
        while running:
            try:
                result = (
                    run_once(
                        connection,
                        source_connection=source_connection,
                        run_generic=CONSUMER_ARM == "ENABLED",
                    )
                    if P90_R06_OWNER == "ENABLED"
                    else run_once(connection)
                )
                connection.commit()
                if result.startswith("P90_R06_FIRSTFILL_OWNER_BLOCKED|"):
                    _p90_status(
                        state="P90_R06_EXIT_OWNER_BLOCKED",
                        result=result,
                        error="P90_R06_FIRSTFILL_OWNER_BLOCKED",
                    )
                    time.sleep(POLL_SECONDS)
                    continue
                state = (
                    "P90_R06_EXIT_OWNER_READY_FLAT"
                    if result.endswith("P90_R06_EXIT_OWNER_READY_FLAT")
                    else "P90_R06_EXIT_OWNER_RUNNING"
                )
                _p90_status(state=state, result=result)
            except Exception as exc:
                connection.rollback()
                if source_connection is not None:
                    source_connection.rollback()
                code = str(exc).split(":", 1)[0]
                if not code.startswith(("P90_", "R06_", "R09_")):
                    code = type(exc).__name__
                _p90_status(
                    state="P90_R06_EXIT_OWNER_BLOCKED",
                    result="BLOCKED",
                    error=code[:120],
                )
                # A source/private transient may recover. Keep the service
                # alive but never manufacture READY from an exception.
            time.sleep(POLL_SECONDS)
    finally:
        if source_connection is not None:
            source_connection.close()
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
