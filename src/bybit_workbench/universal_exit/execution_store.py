from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Protocol

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import FrozenPolicy, TradeDirection
from bybit_workbench.universal_entry.fingerprint import canonical_json, fingerprint

from .contracts import ExitActionKind, ExitExecutionRequest
from .execution_bridge import PreparedRuntimeExitCommand


class CursorLike(Protocol):
    @property
    def rowcount(self) -> int: ...

    def fetchone(self) -> Mapping[str, object] | None: ...


class ConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> CursorLike: ...

    def transaction(self) -> AbstractContextManager[object]: ...


def _request_from_row(row: Mapping[str, object]) -> ExitExecutionRequest:
    requested_at = row.get("requested_at")
    expires_at = row.get("expires_at")
    if not isinstance(requested_at, datetime) or not isinstance(expires_at, datetime):
        raise RuntimeError("ExitExecutionRequest timestamps are invalid")
    mutation = row.get("requested_mutation")
    if not isinstance(mutation, Mapping):
        raise RuntimeError("ExitExecutionRequest mutation is invalid")
    return ExitExecutionRequest(
        execution_request_id=str(row["execution_request_id"]),
        exit_decision_id=str(row["exit_decision_id"]),
        strategy_position_id=str(row["strategy_position_id"]),
        strategy_id=str(row["strategy_id"]),
        strategy_version=str(row["strategy_version"]),
        strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
        exit_plan_fingerprint=str(row["exit_plan_fingerprint"]),
        account_ref=str(row["account_ref"]),
        exchange_position_key=str(row["exchange_position_key"]),
        position_idx=int(str(row["position_idx"])),
        symbol=str(row["symbol"]),
        direction=TradeDirection(str(row["direction"])),
        action_kind=ExitActionKind(str(row["action_kind"])),
        requested_mutation=FrozenPolicy.from_mapping(mutation),
        requested_at=requested_at.astimezone(UTC),
        expires_at=expires_at.astimezone(UTC),
    )


def record_exit_materialization_block(
    connection: ConnectionLike,
    *,
    exit_decision_id: str,
    strategy_position_id: str,
    reason: str,
    payload: Mapping[str, object],
) -> None:
    block_id = (
        "exit-materialization-block-" + fingerprint({"exit_decision_id": exit_decision_id})[:32]
    )
    with connection.transaction():
        connection.execute(
            """INSERT INTO strategy_exit.execution_materialization_blocks(
                   block_id,exit_decision_id,strategy_position_id,reason,payload
               ) VALUES(%s,%s,%s,%s,%s::jsonb)
               ON CONFLICT(exit_decision_id) DO NOTHING""",
            (
                block_id,
                exit_decision_id,
                strategy_position_id,
                reason,
                canonical_json(payload),
            ),
        )
        row = connection.execute(
            """SELECT strategy_position_id,reason,payload
                 FROM strategy_exit.execution_materialization_blocks
                WHERE exit_decision_id=%s""",
            (exit_decision_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Exit materialization block persistence disappeared")
        expected = (strategy_position_id, reason, dict(payload))
        actual = (
            str(row["strategy_position_id"]),
            str(row["reason"]),
            row["payload"],
        )
        if actual != expected:
            raise RuntimeError("Exit materialization block identity collision")


def load_exit_execution_request(
    connection: ConnectionLike,
    *,
    execution_request_id: str,
) -> ExitExecutionRequest:
    row = connection.execute(
        """SELECT * FROM strategy_exit.execution_requests
            WHERE execution_request_id=%s""",
        (execution_request_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"ExitExecutionRequest not found: {execution_request_id}")
    return _request_from_row(row)


def persist_exit_execution_request(
    connection: ConnectionLike,
    request: ExitExecutionRequest,
) -> None:
    with connection.transaction():
        connection.execute(
            """INSERT INTO strategy_exit.execution_requests(
                   execution_request_id,exit_decision_id,strategy_position_id,
                   strategy_id,strategy_version,strategy_config_fingerprint,
                   exit_plan_fingerprint,account_ref,exchange_position_key,
                   position_idx,symbol,direction,action_kind,requested_mutation,
                   requested_at,expires_at
               ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
               ON CONFLICT(execution_request_id) DO NOTHING""",
            (
                request.execution_request_id,
                request.exit_decision_id,
                request.strategy_position_id,
                request.strategy_id,
                request.strategy_version,
                request.strategy_config_fingerprint,
                request.exit_plan_fingerprint,
                request.account_ref,
                request.exchange_position_key,
                request.position_idx,
                request.symbol,
                request.direction.value,
                request.action_kind.value,
                request.requested_mutation.payload_json,
                request.requested_at.astimezone(UTC),
                request.expires_at.astimezone(UTC),
            ),
        )
        stored = load_exit_execution_request(
            connection,
            execution_request_id=request.execution_request_id,
        )
        if stored != request:
            raise RuntimeError("ExitExecutionRequest identity collision")


def _dispatch_id(request_id: str, state: str) -> str:
    return "exit-dispatch-" + fingerprint({"execution_request_id": request_id, "state": state})[:32]


def record_exit_dispatch_blocked(
    connection: ConnectionLike,
    request: ExitExecutionRequest,
    *,
    reason: str,
    payload: Mapping[str, object],
) -> None:
    connection.execute(
        """INSERT INTO strategy_exit.execution_dispatches(
               dispatch_id,execution_request_id,command_id,state,reason,payload
           ) VALUES(%s,%s,NULL,'BLOCKED',%s,%s::jsonb)
           ON CONFLICT(execution_request_id) DO NOTHING""",
        (
            _dispatch_id(request.execution_request_id, "BLOCKED"),
            request.execution_request_id,
            reason,
            canonical_json(payload),
        ),
    )


def exact_open_position(
    connection: ConnectionLike,
    request: ExitExecutionRequest,
) -> StrategyPosition:
    row = connection.execute(
        """SELECT position_id,account_ref,exchange_position_key,position_idx,
                  strategy_id,strategy_version,strategy_config_fingerprint,
                  strategy_activation_id,signal_id,strategy_attempt_id,
                  entry_decision_id,entry_execution_request_id,
                  entry_plan_fingerprint,exit_plan_fingerprint,entry_command_id,
                  symbol,side,actual_avg_fill,actual_qty,fill_at,state,bot_instance_id
             FROM runtime.position_ownership
            WHERE position_id=%s""",
        (request.strategy_position_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("exact StrategyPosition is missing")
    if str(row["state"]) != "OPEN":
        raise RuntimeError(f"StrategyPosition state is {row['state']}, not OPEN")
    if str(row["bot_instance_id"]) != "universal-entry":
        raise RuntimeError("StrategyPosition is not owned by Universal lifecycle")
    fill_at = row["fill_at"]
    if not isinstance(fill_at, datetime):
        raise RuntimeError("StrategyPosition fill_at is invalid")
    direction = TradeDirection.LONG if str(row["side"]) == "Buy" else TradeDirection.SHORT
    position = StrategyPosition(
        strategy_position_id=str(row["position_id"]),
        account_ref=str(row["account_ref"]),
        exchange_position_key=str(row["exchange_position_key"]),
        position_idx=int(str(row["position_idx"])),
        strategy_id=str(row["strategy_id"]),
        strategy_version=str(row["strategy_version"]),
        strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
        strategy_activation_id=str(row["strategy_activation_id"]),
        signal_id=str(row["signal_id"]),
        strategy_attempt_id=str(row["strategy_attempt_id"]),
        entry_decision_id=str(row["entry_decision_id"]),
        entry_execution_request_id=str(row["entry_execution_request_id"]),
        entry_plan_fingerprint=str(row["entry_plan_fingerprint"]),
        exit_plan_fingerprint=str(row["exit_plan_fingerprint"]),
        entry_command_id=str(row["entry_command_id"]),
        symbol=str(row["symbol"]),
        direction=direction,
        actual_avg_fill=row["actual_avg_fill"],  # type: ignore[arg-type]
        actual_qty=row["actual_qty"],  # type: ignore[arg-type]
        fill_at=fill_at.astimezone(UTC),
    )
    expected = (
        request.strategy_position_id,
        request.account_ref,
        request.exchange_position_key,
        request.position_idx,
        request.strategy_id,
        request.strategy_version,
        request.strategy_config_fingerprint,
        request.exit_plan_fingerprint,
        request.symbol,
        request.direction,
    )
    actual = (
        position.strategy_position_id,
        position.account_ref,
        position.exchange_position_key,
        position.position_idx,
        position.strategy_id,
        position.strategy_version,
        position.strategy_config_fingerprint,
        position.exit_plan_fingerprint,
        position.symbol,
        position.direction,
    )
    if actual != expected:
        raise RuntimeError("ExitExecutionRequest / StrategyPosition exact identity mismatch")
    return position


def mutation_in_flight(
    connection: ConnectionLike,
    request: ExitExecutionRequest,
) -> str | None:
    row = connection.execute(
        """SELECT command_id,state
             FROM runtime.trade_commands
            WHERE command_type='strategy_exit'
              AND state IN ('queued','running')
              AND payload_json::jsonb->>'strategy_position_id'=%s
            ORDER BY requested_at_epoch_ms,command_id
            LIMIT 1""",
        (request.strategy_position_id,),
    ).fetchone()
    if row is None:
        return None
    return f"{row['command_id']}:{row['state']}"


def publish_runtime_exit_command(
    connection: ConnectionLike,
    prepared: PreparedRuntimeExitCommand,
    *,
    now: datetime,
) -> None:
    request = prepared.execution_request
    with connection.transaction():
        exact_open_position(connection, request)
        existing_dispatch = connection.execute(
            """SELECT command_id,state,payload
                 FROM strategy_exit.execution_dispatches
                WHERE execution_request_id=%s""",
            (request.execution_request_id,),
        ).fetchone()
        if existing_dispatch is not None:
            if (
                str(existing_dispatch["state"]) == "DISPATCHED"
                and str(existing_dispatch["command_id"]) == prepared.command_id
                and existing_dispatch["payload"] == prepared.payload
            ):
                return
            raise RuntimeError("ExitExecutionRequest dispatch identity collision")

        existing_in_flight = mutation_in_flight(connection, request)
        if existing_in_flight is not None:
            raise RuntimeError(f"EXIT_MUTATION_IN_FLIGHT:{existing_in_flight}")
        payload_json = json.dumps(prepared.payload, ensure_ascii=False, sort_keys=True)
        inserted = connection.execute(
            """INSERT INTO runtime.trade_commands(
                   command_id,command_type,symbol,payload_json,state,requested_at_epoch_ms
               ) VALUES(%s,'strategy_exit',%s,%s,'queued',%s)
               ON CONFLICT(command_id) DO NOTHING
               RETURNING command_id""",
            (
                prepared.command_id,
                request.symbol,
                payload_json,
                int(now.astimezone(UTC).timestamp() * 1000),
            ),
        ).fetchone()
        if inserted is None:
            existing = connection.execute(
                """SELECT command_type,symbol,payload_json
                     FROM runtime.trade_commands WHERE command_id=%s""",
                (prepared.command_id,),
            ).fetchone()
            if existing is None:
                raise RuntimeError("idempotent Exit command conflict disappeared")
            if (
                str(existing["command_type"]) != "strategy_exit"
                or str(existing["symbol"]) != request.symbol
                or json.loads(str(existing["payload_json"])) != prepared.payload
            ):
                raise RuntimeError("deterministic Universal Exit command_id collision")

        connection.execute(
            """INSERT INTO strategy_exit.execution_dispatches(
                   dispatch_id,execution_request_id,command_id,state,reason,payload
               ) VALUES(%s,%s,%s,'DISPATCHED',
                        'prepared exact ExitExecutionRequest',%s::jsonb)
               ON CONFLICT(execution_request_id) DO NOTHING""",
            (
                _dispatch_id(request.execution_request_id, "DISPATCHED"),
                request.execution_request_id,
                prepared.command_id,
                canonical_json(prepared.payload),
            ),
        )


def mark_ambiguous_exit_command(
    connection: ConnectionLike,
    *,
    command_id: str,
    reason: str,
) -> str | None:
    row = connection.execute(
        """SELECT payload_json FROM runtime.trade_commands
            WHERE command_id=%s AND command_type='strategy_exit'""",
        (command_id,),
    ).fetchone()
    if row is None:
        return None
    payload = json.loads(str(row["payload_json"]))
    if payload.get("source") != "universal_exit":
        return None
    position_id = str(payload.get("strategy_position_id") or "")
    if not position_id:
        raise RuntimeError("Universal Exit command lost strategy_position_id")
    updated = connection.execute(
        """UPDATE runtime.position_ownership
              SET state='RECONCILIATION_REQUIRED'
            WHERE position_id=%s AND state='OPEN'""",
        (position_id,),
    )
    if updated.rowcount not in {0, 1}:
        raise RuntimeError("unexpected StrategyPosition ambiguity update count")
    connection.execute(
        """UPDATE runtime.trade_commands
              SET error=%s
            WHERE command_id=%s""",
        (f"EXIT_RECONCILIATION_REQUIRED:{reason}"[:2000], command_id),
    )
    return "RECONCILIATION_REQUIRED"
