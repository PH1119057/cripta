from __future__ import annotations

import json
import os
import signal
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bybit_workbench.strategy_position_binding import exchange_position_key
from bybit_workbench.universal_entry.contracts import ExecutionRequest, FrozenPolicy, TradeDirection
from bybit_workbench.universal_entry.execution_bridge import (
    BridgePolicyBundle,
    ExecutionBridgeBlocked,
    PreparedRuntimeEntryCommand,
    prepare_runtime_entry_command,
)
from bybit_workbench.universal_entry.fingerprint import canonical_json, fingerprint

CURRENT_ACCOUNT_REF = "BYBIT:UNIFIED"
CURRENT_POSITION_IDX = 0

DB_DSN = os.environ.get(
    "CRIPTA_DATABASE_DSN",
    "dbname=cripta user=cripta host=/var/run/postgresql application_name=universal-entry-consumer",
)
ENTRY_COMMAND_SOURCE = os.environ.get("CRIPTA_ENTRY_COMMAND_SOURCE", "LEGACY_V1").strip().upper()
CONSUMER_ARM = os.environ.get("CRIPTA_UNIVERSAL_ENTRY_MAINNET_CONSUMER", "DISABLED").strip().upper()
POLL_SECONDS = float(os.environ.get("CRIPTA_UNIVERSAL_ENTRY_CONSUMER_POLL_SECONDS", "0.25"))

running = True


def _stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} must be an object")
    return value


def _request_from_row(row: Mapping[str, object]) -> ExecutionRequest:
    requested_at = row["requested_at"]
    if not isinstance(requested_at, datetime):
        raise RuntimeError("execution request requested_at is invalid")
    return ExecutionRequest(
        execution_request_id=str(row["execution_request_id"]),
        strategy_attempt_id=str(row["strategy_attempt_id"]),
        entry_decision_id=str(row["entry_decision_id"]),
        signal_id=str(row["signal_id"]),
        strategy_id=str(row["strategy_id"]),
        strategy_version=str(row["strategy_version"]),
        strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
        entry_plan_fingerprint=str(row["entry_plan_fingerprint"]),
        symbol=str(row["symbol"]),
        direction=TradeDirection(str(row["direction"])),
        requested_at=requested_at.astimezone(UTC),
        payload=FrozenPolicy.from_mapping(_mapping(row["payload"], "execution request payload")),
        exit_plan_fingerprint=(
            None if row.get("exit_plan_fingerprint") is None
            else str(row["exit_plan_fingerprint"])
        ),
        capital_reservation_id=(
            None if row.get("capital_reservation_id") is None
            else str(row["capital_reservation_id"])
        ),
    )


def _next_request(connection: psycopg.Connection[Any]) -> Mapping[str, object] | None:
    return connection.execute(
        """SELECT r.*,a.activation_id,a.enabled AS activation_enabled,
                  a.strategy_id AS activation_strategy_id,
                  a.strategy_version AS activation_strategy_version,
                  a.strategy_config_fingerprint AS activation_strategy_config_fingerprint,
                  p.execution_permission_id,p.enabled_at AS execution_enabled_at,
                  c.state AS capital_reservation_state,
                  c.account_ref AS capital_reservation_account_ref
             FROM strategy_entry.execution_requests r
             JOIN strategy_entry.strategy_attempts t
               ON t.strategy_attempt_id=r.strategy_attempt_id
              AND t.signal_id=r.signal_id
             JOIN strategy_entry.strategy_activations a
               ON a.activation_id=t.strategy_activation_id
              AND a.enabled=true
             JOIN strategy_entry.execution_permissions p
               ON p.strategy_id=r.strategy_id
              AND p.strategy_version=r.strategy_version
              AND p.strategy_config_fingerprint=r.strategy_config_fingerprint
              AND p.enabled=true
              AND p.enabled_at IS NOT NULL
              AND r.requested_at >= p.enabled_at
             LEFT JOIN runtime.capital_reservations c
               ON c.reservation_id=r.capital_reservation_id
              AND c.strategy_attempt_id=r.strategy_attempt_id
            WHERE NOT EXISTS (
                  SELECT 1 FROM strategy_entry.execution_dispatches d
                   WHERE d.execution_request_id=r.execution_request_id
            )
            ORDER BY r.requested_at,r.execution_request_id
            LIMIT 1"""
    ).fetchone()


def _policy_bundle(
    connection: psycopg.Connection[Any], row: Mapping[str, object]
) -> BridgePolicyBundle:
    identity = (
        str(row["strategy_id"]),
        str(row["strategy_version"]),
        str(row["strategy_config_fingerprint"]),
    )
    card = connection.execute(
        """SELECT card_json FROM strategy_entry.strategy_cards
            WHERE strategy_id=%s AND strategy_version=%s
              AND strategy_config_fingerprint=%s""",
        identity,
    ).fetchone()
    if card is None:
        raise RuntimeError("exact StrategyCard is missing")
    entry = connection.execute(
        """SELECT plan_json FROM strategy_entry.entry_plans
            WHERE entry_plan_fingerprint=%s
              AND strategy_id=%s AND strategy_version=%s
              AND strategy_config_fingerprint=%s""",
        (str(row["entry_plan_fingerprint"]), *identity),
    ).fetchone()
    if entry is None:
        raise RuntimeError("exact EntryPlan is missing")
    exit_plan_fingerprint = str(row.get("exit_plan_fingerprint") or "")
    if not exit_plan_fingerprint:
        raise RuntimeError("EntryExecutionRequest exact ExitPlan fingerprint is missing")
    exit_row = connection.execute(
        """SELECT plan_json FROM strategy_entry.exit_plans
            WHERE exit_plan_fingerprint=%s
              AND strategy_id=%s AND strategy_version=%s
              AND strategy_config_fingerprint=%s""",
        (exit_plan_fingerprint, *identity),
    ).fetchone()
    if exit_row is None:
        raise RuntimeError("exact EntryExecutionRequest ExitPlan is missing")
    activation: dict[str, object] = {
        "activation_id": str(row["activation_id"]),
        "enabled": bool(row["activation_enabled"]),
        "strategy_id": str(row["activation_strategy_id"]),
        "strategy_version": str(row["activation_strategy_version"]),
        "strategy_config_fingerprint": str(row["activation_strategy_config_fingerprint"]),
    }
    return BridgePolicyBundle(
        strategy_card=_mapping(card["card_json"], "StrategyCard"),
        entry_plan=_mapping(entry["plan_json"], "EntryPlan"),
        exit_plan=_mapping(exit_row["plan_json"], "ExitPlan"),
        activation=activation,
    )


def _dispatch_id(execution_request_id: str, state: str) -> str:
    return (
        "dispatch-"
        + fingerprint({"execution_request_id": execution_request_id, "state": state})[:32]
    )


def _record_blocked(
    connection: psycopg.Connection[Any],
    request: ExecutionRequest,
    *,
    reason: str,
    payload: Mapping[str, object],
) -> None:
    connection.execute(
        """INSERT INTO strategy_entry.execution_dispatches(
               dispatch_id,execution_request_id,command_id,state,reason,
               strategy_attempt_id,entry_decision_id,signal_id,
               strategy_id,strategy_version,strategy_config_fingerprint,
               entry_plan_fingerprint,exit_plan_fingerprint,payload,
               capital_reservation_id
           ) VALUES(%s,%s,NULL,'BLOCKED',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
           ON CONFLICT(execution_request_id) DO NOTHING""",
        (
            _dispatch_id(request.execution_request_id, "BLOCKED"),
            request.execution_request_id,
            reason,
            request.strategy_attempt_id,
            request.entry_decision_id,
            request.signal_id,
            request.strategy_id,
            request.strategy_version,
            request.strategy_config_fingerprint,
            request.entry_plan_fingerprint,
            request.exit_plan_fingerprint,
            canonical_json(payload),
            request.capital_reservation_id,
        ),
    )


def _publish_command(
    connection: psycopg.Connection[Any],
    prepared: PreparedRuntimeEntryCommand,
    *,
    now: datetime,
) -> None:
    payload_json = json.dumps(prepared.payload, ensure_ascii=False, sort_keys=True)
    now_ms = int(now.timestamp() * 1000)
    inserted = connection.execute(
        """INSERT INTO runtime.trade_commands(
               command_id,command_type,symbol,payload_json,state,requested_at_epoch_ms
           ) VALUES(%s,'entry',%s,%s,'queued',%s)
           ON CONFLICT(command_id) DO NOTHING
           RETURNING command_id""",
        (prepared.command_id, prepared.symbol, payload_json, now_ms),
    ).fetchone()
    if inserted is None:
        existing = connection.execute(
            """SELECT command_type,symbol,payload_json FROM runtime.trade_commands
                WHERE command_id=%s""",
            (prepared.command_id,),
        ).fetchone()
        if existing is None:
            raise RuntimeError("idempotent command conflict disappeared")
        if (
            str(existing["command_type"]) != "entry"
            or str(existing["symbol"]) != prepared.symbol
            or json.loads(str(existing["payload_json"])) != prepared.payload
        ):
            raise RuntimeError("deterministic Universal command_id collision")
    connection.execute(
        """INSERT INTO strategy_entry.execution_dispatches(
               dispatch_id,execution_request_id,command_id,state,reason,
               strategy_attempt_id,entry_decision_id,signal_id,
               strategy_id,strategy_version,strategy_config_fingerprint,
               entry_plan_fingerprint,exit_plan_fingerprint,payload,
               capital_reservation_id
           ) VALUES(%s,%s,%s,'DISPATCHED','prepared exact Universal EntryExecutionRequest',
                    %s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
           ON CONFLICT(execution_request_id) DO NOTHING""",
        (
            _dispatch_id(prepared.execution_request_id, "DISPATCHED"),
            prepared.execution_request_id,
            prepared.command_id,
            prepared.strategy_attempt_id,
            prepared.entry_decision_id,
            prepared.signal_id,
            prepared.strategy_id,
            prepared.strategy_version,
            prepared.strategy_config_fingerprint,
            prepared.entry_plan_fingerprint,
            prepared.exit_plan_fingerprint,
            canonical_json(prepared.payload),
            prepared.capital_reservation_id,
        ),
    )
    updated = connection.execute(
        """UPDATE runtime.capital_reservations
              SET state='DISPATCHED'
            WHERE reservation_id=%s AND state='RESERVED'""",
        (prepared.capital_reservation_id,),
    )
    if updated.rowcount != 1:
        raise RuntimeError("atomic capital reservation is not RESERVED at dispatch")


def _exchange_position_slot_conflict(
    connection: psycopg.Connection[Any],
    row: Mapping[str, object],
    request: ExecutionRequest,
) -> str | None:
    account_ref = str(row.get("capital_reservation_account_ref") or "")
    if account_ref != CURRENT_ACCOUNT_REF:
        return f"ACCOUNT_REF_UNSUPPORTED:{account_ref or 'MISSING'}"

    key = exchange_position_key(request.symbol, CURRENT_POSITION_IDX)
    owned = connection.execute(
        """SELECT position_id,state
             FROM runtime.position_ownership
            WHERE exchange_position_key=%s
              AND state IN ('OPEN','RECONCILIATION_REQUIRED')
            ORDER BY fill_at DESC
            LIMIT 1""",
        (key,),
    ).fetchone()
    if owned is not None:
        return f"OWNED_POSITION:{owned['position_id']}:{owned['state']}"

    hot_position = connection.execute(
        """SELECT side,size
             FROM runtime.hot_positions
            WHERE symbol=%s AND position_idx=%s
              AND NULLIF(size,'')::numeric > 0
            LIMIT 1""",
        (request.symbol, CURRENT_POSITION_IDX),
    ).fetchone()
    if hot_position is not None:
        return f"EXCHANGE_POSITION:{hot_position['side']}:{hot_position['size']}"

    pending_command = connection.execute(
        """SELECT command_id,state
             FROM runtime.trade_commands
            WHERE command_type='entry'
              AND symbol=%s
              AND state IN ('queued','running')
            ORDER BY requested_at_epoch_ms
            LIMIT 1""",
        (request.symbol,),
    ).fetchone()
    if pending_command is not None:
        return f"PENDING_ENTRY_COMMAND:{pending_command['command_id']}:{pending_command['state']}"

    pending_order = connection.execute(
        """SELECT order_id,order_status
             FROM runtime.hot_orders
            WHERE symbol=%s
              AND order_status IN ('New','PartiallyFilled','Untriggered')
              AND coalesce((payload_json::jsonb->>'reduceOnly')::boolean,false)=false
            ORDER BY order_id
            LIMIT 1""",
        (request.symbol,),
    ).fetchone()
    if pending_order is not None:
        return f"PENDING_ENTRY_ORDER:{pending_order['order_id']}:{pending_order['order_status']}"
    return None


def _release_pre_exchange_reservation(
    connection: psycopg.Connection[Any],
    reservation_id: str,
) -> None:
    updated = connection.execute(
        """UPDATE runtime.capital_reservations
              SET state='RELEASED'
            WHERE reservation_id=%s AND state='RESERVED'""",
        (reservation_id,),
    )
    if updated.rowcount != 1:
        raise RuntimeError("pre-exchange capital reservation release race")


def _execution_gate_enabled(connection: psycopg.Connection[Any]) -> bool:
    row = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    return bool(row and row["enabled"])


def run_once(connection: psycopg.Connection[Any], *, now: datetime | None = None) -> str:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if not _execution_gate_enabled(connection):
        return "EXECUTION_GATE_DISARMED"
    row = _next_request(connection)
    if row is None:
        return "NO_REQUEST"
    request = _request_from_row(row)
    reservation_state = row.get("capital_reservation_state")
    if request.capital_reservation_id is None or reservation_state != "RESERVED":
        with connection.transaction():
            _record_blocked(
                connection,
                request,
                reason=(
                    "CAPITAL_RESERVATION_INVALID:"
                    f"{request.capital_reservation_id}:{reservation_state}"
                ),
                payload={
                    "capital_reservation_id": request.capital_reservation_id,
                    "capital_reservation_state": reservation_state,
                },
            )
        return "BLOCKED:CAPITAL_RESERVATION_INVALID"
    conflict = _exchange_position_slot_conflict(connection, row, request)
    if conflict is not None:
        assert request.capital_reservation_id is not None
        with connection.transaction():
            _record_blocked(
                connection,
                request,
                reason=f"EXCHANGE_POSITION_OWNERSHIP_CONFLICT:{conflict}",
                payload={
                    "fault_code": "EXCHANGE_POSITION_OWNERSHIP_CONFLICT",
                    "conflict": conflict,
                    "exchange_position_key": exchange_position_key(
                        request.symbol, CURRENT_POSITION_IDX
                    ),
                },
            )
            _release_pre_exchange_reservation(
                connection,
                request.capital_reservation_id,
            )
        return "BLOCKED:EXCHANGE_POSITION_OWNERSHIP_CONFLICT"
    try:
        bundle = _policy_bundle(connection, row)
        prepared = prepare_runtime_entry_command(request, bundle, now=current)
    except ExecutionBridgeBlocked as exc:
        with connection.transaction():
            _record_blocked(
                connection,
                request,
                reason=f"{exc.code.value}:{exc.reason}",
                payload={"block_code": exc.code.value, "reason": exc.reason},
            )
        return f"BLOCKED:{exc.code.value}"
    except Exception as exc:
        with connection.transaction():
            _record_blocked(
                connection,
                request,
                reason=f"STRUCTURAL_ERROR:{type(exc).__name__}:{exc}",
                payload={"error": f"{type(exc).__name__}: {exc}"},
            )
        return "BLOCKED:STRUCTURAL_ERROR"
    with connection.transaction():
        _publish_command(connection, prepared, now=current)
    return f"DISPATCHED:{prepared.command_id}"


def main() -> int:
    if ENTRY_COMMAND_SOURCE != "UNIVERSAL_ENTRY" or CONSUMER_ARM != "ENABLED":
        print(
            json.dumps(
                {
                    "state": "DISABLED",
                    "entry_command_source": ENTRY_COMMAND_SOURCE,
                    "universal_entry_mainnet_consumer": CONSUMER_ARM,
                    "trading_effect": "NONE",
                },
                sort_keys=True,
            )
        )
        return 0
    if POLL_SECONDS <= 0:
        raise SystemExit("CRIPTA_UNIVERSAL_ENTRY_CONSUMER_POLL_SECONDS must be positive")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    with psycopg.connect(DB_DSN, row_factory=dict_row) as connection:
        while running:
            result = run_once(connection)
            if not result.startswith("DISPATCHED") and not result.startswith("BLOCKED"):
                connection.commit()
                time.sleep(POLL_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
