from __future__ import annotations

import json
import os
import signal
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from bybit_workbench.account_entry_safety import account_entry_safety_ready
from bybit_workbench.account_live_arm import account_live_arm_ready
from bybit_workbench.strategy_execution_binding_policy import (
    real_binding_allocation_allowed,
)
from bybit_workbench.account_state_generation import (
    AccountStateGenerationUnavailable,
    current_complete_account_state_generation,
)
from bybit_workbench.entry_consumer_route import (
    ConsumerWorkerRoute,
    check_consumer_worker_route,
)
from bybit_workbench.legacy_live_arm_account_scope import check_legacy_live_arm_account_scope
from bybit_workbench.live_arm_readiness import LiveArmContext, active_live_arm_session
from bybit_workbench.release_arm_safety import check_release_arm_invariant
from bybit_workbench.strategy_position_binding import exchange_position_key
from bybit_workbench.sub_worker_scope import require_exact_sub_instance
from bybit_workbench.universal_entry.contracts import ExecutionRequest, FrozenPolicy, TradeDirection
from bybit_workbench.universal_entry.execution_bridge import (
    BridgePolicyBundle,
    ExecutionBridgeBlocked,
    PreparedRuntimeEntryCommand,
    prepare_runtime_entry_command,
)
from bybit_workbench.universal_entry.fingerprint import canonical_json, fingerprint
from bybit_workbench.universal_entry.owner_full_contour_control import (
    require_parent_p90_diagnostic_scope,
    retire_entry_rights as retire_owner_diagnostic_entry_rights,
    set_run_state as set_owner_diagnostic_run_state,
)
from bybit_workbench.universal_entry.owner_full_contour_diagnostic import (
    is_diagnostic_strategy,
)
from bybit_workbench.universal_entry.owner_full_contour_runtime import (
    materialize_requested_run,
    refresh_run_lifecycle,
)
from bybit_workbench.universal_entry.runtime_loader import MappingSequenceRow

CURRENT_ACCOUNT_REF = "BYBIT:UNIFIED"
CURRENT_POSITION_IDX = 0


def mapping_sequence_row(cursor: Any) -> Any:
    """dict semantics plus positional compatibility for the legacy Entry call graph."""
    make_dict = dict_row(cursor)

    def make_row(values: list[Any] | tuple[Any, ...]) -> MappingSequenceRow:
        return MappingSequenceRow(make_dict(values))

    return make_row


DB_DSN = os.environ.get(
    "CRIPTA_DATABASE_DSN",
    "dbname=cripta user=cripta host=/var/run/postgresql application_name=universal-entry-consumer",
)
ENTRY_COMMAND_SOURCE = os.environ.get(
    "CRIPTA_ENTRY_COMMAND_SOURCE", "UNIVERSAL_ENTRY"
).strip().upper()
CONSUMER_ARM = os.environ.get("CRIPTA_UNIVERSAL_ENTRY_MAINNET_CONSUMER", "DISABLED").strip().upper()
POLL_SECONDS = float(os.environ.get("CRIPTA_UNIVERSAL_ENTRY_CONSUMER_POLL_SECONDS", "0.25"))
HEALTH_SECONDS = float(os.environ.get("CRIPTA_UNIVERSAL_ENTRY_HEALTH_SECONDS", "2.0"))
LOADED_RELEASE_COMMIT = os.environ.get("CRIPTA_RELEASE_COMMIT", "").strip().lower()
TRADING_ACCOUNT_ID = os.environ.get("CRIPTA_TRADING_ACCOUNT_ID", "").strip()
ACCOUNT_CONNECTOR_ID = os.environ.get("CRIPTA_ACCOUNT_CONNECTOR_ID", "").strip()

running = True


def _stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} must be an object")
    return value


_REQUIRED_DB_PRIVILEGES = (
    ("strategy_entry.execution_requests", "SELECT"),
    ("strategy_entry.strategy_attempts", "SELECT"),
    ("strategy_entry.strategy_activations", "SELECT"),
    ("strategy_entry.execution_permissions", "SELECT"),
    ("strategy_entry.execution_permissions", "UPDATE"),
    ("strategy_entry.entry_decisions", "SELECT"),
    ("strategy_entry.execution_dispatches", "SELECT"),
    ("strategy_entry.execution_dispatches", "INSERT"),
    ("strategy_entry.execution_request_state_events", "SELECT"),
    ("strategy_entry.execution_request_state_events", "INSERT"),
    ("strategy_entry.strategy_cards", "SELECT"),
    ("strategy_entry.entry_plans", "SELECT"),
    ("strategy_entry.exit_plans", "SELECT"),
    ("runtime.capital_reservations", "SELECT"),
    ("runtime.capital_reservations", "UPDATE"),
    ("runtime.exchange_position_slot_claims", "SELECT"),
    ("runtime.exchange_position_slot_claims", "UPDATE"),
    ("runtime.trade_commands", "SELECT"),
    ("runtime.trade_commands", "INSERT"),
    ("control.owner_full_contour_diagnostic_runs", "SELECT"),
    ("control.owner_full_contour_diagnostic_runs", "UPDATE"),
    ("control.live_arm_sessions", "SELECT"),
    ("control.live_arm_sessions", "UPDATE"),
    ("control.execution_gates", "SELECT"),
    ("control.execution_gates", "UPDATE"),
    ("control.execution_gate_events", "INSERT"),
)


_NEXT_REQUEST_SQL = """SELECT r.*,a.activation_id,a.enabled AS activation_enabled,
          a.strategy_id AS activation_strategy_id,
          a.strategy_version AS activation_strategy_version,
          a.strategy_config_fingerprint AS activation_strategy_config_fingerprint,
          p.execution_permission_id,p.enabled_at AS execution_enabled_at,
          c.state AS capital_reservation_state,
          c.account_ref AS capital_reservation_account_ref,
          c.capacity_snapshot_id AS capital_capacity_snapshot_id,
          c.strategy_execution_binding_id AS capital_binding_id,
          c.execution_leg AS capital_execution_leg,
          c.trading_account_id AS capital_trading_account_id,
          c.identity_status AS capital_identity_status,
          e.strategy_execution_binding_id AS decision_binding_id,
          e.execution_leg AS decision_execution_leg,
          e.trading_account_id AS decision_trading_account_id,
          e.identity_status AS decision_identity_status
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
     JOIN runtime.capital_reservations c
       ON c.reservation_id=r.capital_reservation_id
      AND c.strategy_attempt_id=r.strategy_attempt_id
      AND c.trading_account_id=r.trading_account_id
      AND c.identity_status='PROVEN'
     LEFT JOIN strategy_entry.entry_decisions e
       ON e.entry_decision_id=r.entry_decision_id
      AND e.strategy_attempt_id=r.strategy_attempt_id
      AND e.signal_id=r.signal_id
    WHERE r.trading_account_id=%s
      AND r.identity_status='PROVEN'
      AND NOT EXISTS (
          SELECT 1 FROM strategy_entry.execution_dispatches d
           WHERE d.execution_request_id=r.execution_request_id
    )
    ORDER BY r.requested_at,r.execution_request_id
    LIMIT 1
    FOR UPDATE OF c SKIP LOCKED"""


def _consumer_runtime_readiness(
    connection: psycopg.Connection[Any],
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if len(LOADED_RELEASE_COMMIT) != 40:
        reasons.append("LOADED_RELEASE_COMMIT_INVALID")
    try:
        route = ConsumerWorkerRoute(TRADING_ACCOUNT_ID, ACCOUNT_CONNECTOR_ID)
    except ValueError:
        reasons.append("ACCOUNT_CONNECTOR_ROUTE_NOT_READY")
    else:
        route_check = check_consumer_worker_route(connection, route=route)
        if not route_check.ready:
            reasons.extend(route_check.reasons)
        safety_ready, safety_reasons = account_entry_safety_ready(
            connection,
            trading_account_id=route.trading_account_id,
            account_connector_id=route.account_connector_id,
        )
        if not safety_ready:
            reasons.extend(safety_reasons)
    for table, privilege in _REQUIRED_DB_PRIVILEGES:
        row = connection.execute(
            "SELECT has_table_privilege(current_user,%s,%s)",
            (table, privilege),
        ).fetchone()
        allowed = bool(
            row
            and (
                row[0]
                if not isinstance(row, Mapping)
                else next(iter(row.values()))
            )
        )
        if not allowed:
            reasons.append(f"DB_PRIVILEGE_MISSING:{table}:{privilege}")
    try:
        connection.execute(
            "EXPLAIN (COSTS FALSE) " + _NEXT_REQUEST_SQL,
            (TRADING_ACCOUNT_ID,),
        ).fetchall()
    except psycopg.Error as exc:
        reasons.append(
            "ENTRY_REQUEST_SQL_PREFLIGHT_FAILED:"
            + str(exc.sqlstate or type(exc).__name__)
        )
    return not reasons, tuple(dict.fromkeys(reasons))


def _write_consumer_health(
    connection: psycopg.Connection[Any],
    *,
    ready: bool,
    detail: str,
    now: datetime,
) -> None:
    connection.execute(
        """INSERT INTO runtime.entry_consumer_health(
               trading_account_id,account_connector_id,release_commit,
               state,detail,observed_at,updated_at
           ) VALUES(%s,%s,%s,%s,%s,%s,clock_timestamp())
           ON CONFLICT(trading_account_id) DO UPDATE
             SET account_connector_id=excluded.account_connector_id,
                 release_commit=excluded.release_commit,
                 state=excluded.state,
                 detail=excluded.detail,
                 observed_at=excluded.observed_at,
                 updated_at=clock_timestamp()""",
        (
            TRADING_ACCOUNT_ID,
            ACCOUNT_CONNECTOR_ID,
            LOADED_RELEASE_COMMIT,
            "READY" if ready else "BLOCKED",
            detail,
            now.astimezone(UTC),
        ),
    )


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
        exchange_position_slot_claim_id=(
            None if row.get("exchange_position_slot_claim_id") is None
            else str(row["exchange_position_slot_claim_id"])
        ),
        position_mode_state_ref=(
            None if row.get("position_mode_state_ref") is None
            else str(row["position_mode_state_ref"])
        ),
        strategy_execution_binding_id=(
            None if row.get("strategy_execution_binding_id") is None
            else str(row["strategy_execution_binding_id"])
        ),
        execution_leg=(
            None if row.get("execution_leg") is None
            else str(row["execution_leg"])
        ),
        trading_account_id=(
            None if row.get("trading_account_id") is None
            else str(row["trading_account_id"])
        ),
    )


def _next_request(
    connection: psycopg.Connection[Any], *, route: ConsumerWorkerRoute | None = None
) -> Mapping[str, object] | None:
    # Exactly one private connector / TradingAccount is owned by this worker.
    # A route parameter cannot authorize a process to use different credentials.
    scoped = route or ConsumerWorkerRoute(TRADING_ACCOUNT_ID, ACCOUNT_CONNECTOR_ID)
    if (
        scoped.trading_account_id != TRADING_ACCOUNT_ID
        or scoped.account_connector_id != ACCOUNT_CONNECTOR_ID
    ):
        raise ValueError("CONSUMER_ROUTE_PROCESS_IDENTITY_MISMATCH")
    return connection.execute(
        _NEXT_REQUEST_SQL,
        (TRADING_ACCOUNT_ID,),
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


def _record_request_state(
    connection: psycopg.Connection[Any],
    request: ExecutionRequest,
    *,
    state: str,
    occurred_at: datetime,
    reason: str,
    payload: Mapping[str, object] | None = None,
) -> None:
    event_id = (
        "reqstate-"
        + fingerprint(
            {
                "execution_request_id": request.execution_request_id,
                "state": state,
                "reason": reason,
            }
        )[:32]
    )
    connection.execute(
        """INSERT INTO strategy_entry.execution_request_state_events(
               request_state_event_id,execution_request_id,state,occurred_at,reason,payload
           ) VALUES(%s,%s,%s,%s,%s,%s::jsonb)
           ON CONFLICT(request_state_event_id) DO NOTHING""",
        (
            event_id,
            request.execution_request_id,
            state,
            occurred_at.astimezone(UTC),
            reason,
            canonical_json(dict(payload or {})),
        ),
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
               command_id,command_type,symbol,payload_json,state,requested_at_epoch_ms,
               strategy_execution_binding_id,execution_leg,trading_account_id,
               identity_status
           ) VALUES(%s,'entry',%s,%s,'queued',%s,%s,%s,%s,'PROVEN')
           ON CONFLICT(command_id) DO NOTHING
           RETURNING command_id""",
        (
            prepared.command_id,
            prepared.symbol,
            payload_json,
            now_ms,
            prepared.strategy_execution_binding_id,
            prepared.execution_leg,
            prepared.trading_account_id,
        ),
    ).fetchone()
    if inserted is None:
        existing = connection.execute(
            """SELECT command_type,symbol,payload_json,
                      strategy_execution_binding_id,execution_leg,trading_account_id,
                      identity_status
                   FROM runtime.trade_commands
                  WHERE command_id=%s""",
            (prepared.command_id,),
        ).fetchone()
        if existing is None:
            raise RuntimeError("idempotent command conflict disappeared")
        if (
            str(existing["command_type"]) != "entry"
            or str(existing["symbol"]) != prepared.symbol
            or json.loads(str(existing["payload_json"])) != prepared.payload
            or str(existing["strategy_execution_binding_id"]) != prepared.strategy_execution_binding_id
            or str(existing["execution_leg"]) != prepared.execution_leg
            or str(existing["trading_account_id"]) != prepared.trading_account_id
            or str(existing["identity_status"]) != "PROVEN"
        ):
            raise RuntimeError("deterministic Universal command_id collision")
    connection.execute(
        """INSERT INTO strategy_entry.execution_dispatches(
               dispatch_id,execution_request_id,command_id,state,reason,
               strategy_attempt_id,entry_decision_id,signal_id,
               strategy_id,strategy_version,strategy_config_fingerprint,
               entry_plan_fingerprint,exit_plan_fingerprint,payload,
               capital_reservation_id,exchange_position_slot_claim_id,
               position_mode_state_ref
           ) VALUES(%s,%s,%s,'DISPATCHED','prepared exact Universal EntryExecutionRequest',
                    %s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
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
            prepared.exchange_position_slot_claim_id,
            prepared.position_mode_state_ref,
        ),
    )
    updated = connection.execute(
        """UPDATE runtime.capital_reservations
              SET state='DISPATCHED',state_reason='DISPATCHED_TO_EXECUTION'
            WHERE reservation_id=%s AND state='RESERVED'""",
        (prepared.capital_reservation_id,),
    )
    if updated.rowcount != 1:
        raise RuntimeError("atomic capital reservation is not RESERVED at dispatch")
    request = ExecutionRequest(
        execution_request_id=prepared.execution_request_id,
        strategy_attempt_id=prepared.strategy_attempt_id,
        entry_decision_id=prepared.entry_decision_id,
        signal_id=prepared.signal_id,
        strategy_id=prepared.strategy_id,
        strategy_version=prepared.strategy_version,
        strategy_config_fingerprint=prepared.strategy_config_fingerprint,
        entry_plan_fingerprint=prepared.entry_plan_fingerprint,
        symbol=prepared.symbol,
        direction=prepared.direction,
        requested_at=prepared.requested_at,
        payload=FrozenPolicy.from_mapping(prepared.payload),
        exit_plan_fingerprint=prepared.exit_plan_fingerprint,
        capital_reservation_id=prepared.capital_reservation_id,
        exchange_position_slot_claim_id=prepared.exchange_position_slot_claim_id,
        position_mode_state_ref=prepared.position_mode_state_ref,
        strategy_execution_binding_id=prepared.strategy_execution_binding_id,
        execution_leg=prepared.execution_leg,
        trading_account_id=prepared.trading_account_id,
    )
    _record_request_state(
        connection,
        request,
        state="REQUEST_DISPATCHED",
        occurred_at=now,
        reason="DISPATCHED_TO_EXECUTION",
        payload={"command_id": prepared.command_id},
    )


def _finalize_owner_diagnostic_dispatch(
    connection: psycopg.Connection[Any],
    prepared: PreparedRuntimeEntryCommand,
    *,
    now: datetime,
) -> None:
    if not is_diagnostic_strategy(prepared.strategy_id, prepared.strategy_version):
        return
    row = connection.execute(
        """SELECT run_id,state
             FROM control.owner_full_contour_diagnostic_runs
            WHERE entry_execution_request_id=%s
              AND strategy_id=%s
              AND strategy_version=%s
              AND trading_account_id=%s
            ORDER BY requested_at DESC
            FOR UPDATE""",
        (
            prepared.execution_request_id,
            prepared.strategy_id,
            prepared.strategy_version,
            prepared.trading_account_id,
        ),
    ).fetchall()
    if len(row) != 1 or str(row[0]["state"]) != "MATERIALIZED":
        raise RuntimeError("OWNER_FULL_CONTOUR_DISPATCH_RUN_IDENTITY_INVALID")
    run_id = str(row[0]["run_id"])
    set_owner_diagnostic_run_state(
        connection,
        run_id=run_id,
        state="DISPATCHED",
        now=now,
        lineage={
            "entry_execution_request_id": prepared.execution_request_id,
            "entry_command_id": prepared.command_id,
        },
    )
    retire_owner_diagnostic_entry_rights(
        connection,
        run_id=run_id,
        at=now,
        reason="diagnostic Entry dispatched",
    )


def _generation_pre_dispatch_status(
    connection: psycopg.Connection[Any],
    row: Mapping[str, object],
    request: ExecutionRequest,
) -> tuple[str, str]:
    """Reprove current terminal COMPLETE and exact admitted account-mode ref.

    This is separate from historical mode fresh_until and does not replace
    Strategy-owned request expiration, incompatible-mode or fault gates.
    """
    if not request.trading_account_id:
        return "STALE_MODE", "REQUEST_ACCOUNT_IDENTITY_MISSING"
    try:
        generation = current_complete_account_state_generation(
            connection,
            account_ref=CURRENT_ACCOUNT_REF,
            trading_account_id=request.trading_account_id,
            acquire_account_lock=True,
        )
    except AccountStateGenerationUnavailable:
        return "STALE_MODE", "CURRENT_ACCOUNT_GENERATION_UNAVAILABLE"
    if (
        str(row.get("capital_capacity_snapshot_id") or "") != generation.generation_id
        or generation.position_mode_refs.get(request.symbol)
        != request.position_mode_state_ref
    ):
        return "STALE_MODE", "ADMISSION_ACCOUNT_GENERATION_CHANGED"
    return "OK", ""


def _admission_pre_dispatch_status(
    connection: psycopg.Connection[Any],
    row: Mapping[str, object],
    request: ExecutionRequest,
    *,
    now: datetime,
) -> tuple[str, str]:
    if (
        request.strategy_execution_binding_id is None
        or request.execution_leg is None
        or request.trading_account_id is None
        or str(row.get("identity_status") or "") != "PROVEN"
    ):
        return "BINDING_INVALID", "REQUEST_BINDING_LINEAGE_MISSING"
    if not TRADING_ACCOUNT_ID or request.trading_account_id != TRADING_ACCOUNT_ID:
        return "BINDING_INVALID", "REQUEST_TRADING_ACCOUNT_MISMATCH"
    if (
        str(row.get("capital_binding_id") or "") != request.strategy_execution_binding_id
        or str(row.get("capital_execution_leg") or "") != request.execution_leg
        or str(row.get("capital_trading_account_id") or "") != request.trading_account_id
        or str(row.get("capital_identity_status") or "") != "PROVEN"
    ):
        return "BINDING_INVALID", "CAPITAL_BINDING_LINEAGE_MISMATCH"
    if (
        str(row.get("decision_binding_id") or "") != request.strategy_execution_binding_id
        or str(row.get("decision_execution_leg") or "") != request.execution_leg
        or str(row.get("decision_trading_account_id") or "") != request.trading_account_id
        or str(row.get("decision_identity_status") or "") != "PROVEN"
    ):
        return "BINDING_INVALID", "ENTRY_DECISION_BINDING_LINEAGE_MISMATCH"
    if is_diagnostic_strategy(request.strategy_id, request.strategy_version):
        if not ACCOUNT_CONNECTOR_ID or not LOADED_RELEASE_COMMIT:
            return "BINDING_INVALID", "OWNER_FULL_CONTOUR_PARENT_SCOPE_CONTEXT_MISSING"
        try:
            require_parent_p90_diagnostic_scope(
                connection,
                symbol=request.symbol,
                account_id=request.trading_account_id,
                connector_id=ACCOUNT_CONNECTOR_ID,
                release_commit=LOADED_RELEASE_COMMIT,
            )
        except ValueError as exc:
            return "BINDING_INVALID", str(exc)

    binding = connection.execute(
        """SELECT b.strategy_activation_id,b.execution_leg,b.execution_role,
                  b.trading_account_id,b.allocation_mode,b.binding_state,
                  b.effective_from,b.effective_to,a.enabled,ta.status,ta.environment
             FROM strategy_entry.strategy_execution_bindings b
             JOIN strategy_entry.strategy_activations a
               ON a.activation_id=b.strategy_activation_id
             JOIN identity.trading_accounts ta
               ON ta.trading_account_id=b.trading_account_id
            WHERE b.strategy_execution_binding_id=%s""",
        (request.strategy_execution_binding_id,),
    ).fetchone()
    if binding is None:
        return "BINDING_INVALID", "STRATEGY_EXECUTION_BINDING_MISSING"
    if (
        str(binding["strategy_activation_id"]) != str(row["activation_id"])
        or str(binding["execution_leg"]) != request.execution_leg
        or str(binding["execution_role"]) != "PRIMARY"
        or str(binding["trading_account_id"]) != request.trading_account_id
        or not real_binding_allocation_allowed(
            strategy_id=request.strategy_id,
            strategy_version=request.strategy_version,
            allocation_mode=str(binding["allocation_mode"]),
        )
        or str(binding["binding_state"]) != "ACTIVE"
        or binding["effective_from"].astimezone(UTC) > now
        or (
            binding["effective_to"] is not None
            and binding["effective_to"].astimezone(UTC) <= now
        )
        or not bool(binding["enabled"])
        or str(binding["status"]) != "ACTIVE"
        or str(binding["environment"]) != "MAINNET"
    ):
        return "BINDING_INVALID", "STRATEGY_EXECUTION_BINDING_NOT_ACTIVE"
    # Match the explicitly configured private-runtime connector identity.
    # Other registered connectors for this TradingAccount may serve different
    # roles; never infer which one owns this execution from account alone.
    if not ACCOUNT_CONNECTOR_ID:
        return "BINDING_INVALID", "ACCOUNT_CONNECTOR_ID_CONFIG_MISSING"
    connector = connection.execute(
        """SELECT account_connector_id,connector_kind,adapter_id,
                  credential_ref,status
             FROM identity.account_connectors
            WHERE trading_account_id=%s AND account_connector_id=%s
              AND retired_at IS NULL""",
        (request.trading_account_id, ACCOUNT_CONNECTOR_ID),
    ).fetchone()
    if (
        connector is None
        or str(connector["status"]) != "ACTIVE"
        or str(connector["connector_kind"]) != "CEX_PRIVATE_RUNTIME"
        or str(connector["adapter_id"]) != "BYBIT_V5_PRIVATE"
        or not str(connector["credential_ref"] or "").strip()
    ):
        return "BINDING_INVALID", "ACCOUNT_CONNECTOR_IDENTITY_NOT_ACTIVE"
    account_ref = str(row.get("capital_reservation_account_ref") or "")
    if account_ref != CURRENT_ACCOUNT_REF:
        return "INVARIANT_BROKEN", f"ACCOUNT_REF_UNSUPPORTED:{account_ref or 'MISSING'}"
    if (
        request.capital_reservation_id is None
        or request.exchange_position_slot_claim_id is None
        or request.position_mode_state_ref is None
    ):
        return "INVARIANT_BROKEN", "ACCEPTED_REQUEST_MISSING_ADMISSION_LINEAGE"

    claim = connection.execute(
        """SELECT exchange_position_key,account_ref,symbol,position_idx,
                  strategy_attempt_id,position_mode_state_ref,capital_reservation_id,
                  claim_state,strategy_execution_binding_id,execution_leg,
                  trading_account_id,identity_status
             FROM runtime.exchange_position_slot_claims
            WHERE exchange_position_slot_claim_id=%s""",
        (request.exchange_position_slot_claim_id,),
    ).fetchone()
    if claim is None:
        return "INVARIANT_BROKEN", "SLOT_CLAIM_MISSING"
    expected_key = exchange_position_key(
        request.trading_account_id,
        request.symbol,
        CURRENT_POSITION_IDX,
    )
    actual = (
        str(claim["exchange_position_key"]),
        str(claim["account_ref"]),
        str(claim["symbol"]),
        int(claim["position_idx"]),
        str(claim["strategy_attempt_id"]),
        str(claim["position_mode_state_ref"]),
        str(claim["capital_reservation_id"]),
        str(claim["claim_state"]),
        str(claim["strategy_execution_binding_id"]),
        str(claim["execution_leg"]),
        str(claim["trading_account_id"]),
        str(claim["identity_status"]),
    )
    expected = (
        expected_key,
        CURRENT_ACCOUNT_REF,
        request.symbol,
        CURRENT_POSITION_IDX,
        request.strategy_attempt_id,
        request.position_mode_state_ref,
        request.capital_reservation_id,
        "CLAIMED",
        request.strategy_execution_binding_id,
        request.execution_leg,
        request.trading_account_id,
        "PROVEN",
    )
    if actual != expected:
        return "INVARIANT_BROKEN", f"SLOT_CLAIM_IDENTITY_MISMATCH:{actual!r}"

    generation_status, generation_detail = _generation_pre_dispatch_status(
        connection, row, request
    )
    if generation_status != "OK":
        return generation_status, generation_detail

    mode = connection.execute(
        """SELECT position_mode,position_idx,observed_at,received_at,fresh_until,
                  trading_account_id,identity_status
             FROM runtime.position_mode_states
            WHERE position_mode_state_ref=%s""",
        (request.position_mode_state_ref,),
    ).fetchone()
    if mode is None:
        return "STALE_MODE", "POSITION_MODE_STATE_MISSING"
    fresh_until = mode["fresh_until"]
    observed_at = mode["observed_at"]
    received_at = mode["received_at"]
    if not all(isinstance(value, datetime) for value in (fresh_until, observed_at, received_at)):
        return "STALE_MODE", "POSITION_MODE_STATE_INVALID_TIMESTAMPS"
    if (
        observed_at.astimezone(UTC) > now
        or received_at.astimezone(UTC) > now
    ):
        # fresh_until is provenance for recovery/activation, not an additional
        # generation-backed per-entry expiry gate (ARCH 5.1.1 / TC 2.7).
        return "STALE_MODE", "POSITION_MODE_STATE_INVALID_TIMESTAMPS"
    mode_position_idx = (
        -1 if mode["position_idx"] is None else int(mode["position_idx"])
    )
    if (
        str(mode["trading_account_id"]) != request.trading_account_id
        or str(mode["identity_status"]) != "PROVEN"
    ):
        return "BINDING_INVALID", "POSITION_MODE_TRADING_ACCOUNT_MISMATCH"
    if str(mode["position_mode"]) != "ONE_WAY" or mode_position_idx != 0:
        return (
            "MODE_MISMATCH",
            f"EXCHANGE_POSITION_MODE_MISMATCH:{mode['position_mode']}:{mode['position_idx']}",
        )

    newer = connection.execute(
        """SELECT position_mode_state_ref,position_mode,position_idx,fresh_until
             FROM runtime.position_mode_states
            WHERE account_ref=%s
              AND trading_account_id=%s
              AND identity_status='PROVEN'
              AND product_category='LINEAR' AND instrument=%s
            ORDER BY observed_at DESC,created_at DESC LIMIT 1""",
        (CURRENT_ACCOUNT_REF, request.trading_account_id, request.symbol),
    ).fetchone()
    if (
        newer is not None
        and str(newer["position_mode_state_ref"])
        != request.position_mode_state_ref
        and (
            str(newer["position_mode"]) != "ONE_WAY"
            or (
                -1 if newer["position_idx"] is None else int(newer["position_idx"])
            )
            != 0
        )
    ):
        return (
            "MODE_MISMATCH",
            "EXCHANGE_POSITION_MODE_MISMATCH:NEWER_STATE:"
            + str(newer["position_mode_state_ref"]),
        )

    owned = connection.execute(
        """SELECT position_id,state,exchange_position_slot_claim_id
             FROM runtime.position_ownership
            WHERE exchange_position_key=%s
              AND state IN ('OPEN','RECONCILIATION_REQUIRED')
            ORDER BY fill_at DESC LIMIT 1""",
        (expected_key,),
    ).fetchone()
    if owned is not None:
        return (
            "INVARIANT_BROKEN",
            f"OWNED_POSITION:{owned['position_id']}:{owned['state']}:"
            f"{owned['exchange_position_slot_claim_id']}",
        )

    hot_position = connection.execute(
        """SELECT side,size
             FROM runtime.hot_positions
            WHERE symbol=%s AND position_idx=0
              AND trading_account_id=%s
              AND identity_status='PROVEN'
              AND NULLIF(size,'')::numeric > 0
            LIMIT 1""",
        (request.symbol, request.trading_account_id),
    ).fetchone()
    if hot_position is not None:
        return (
            "INVARIANT_BROKEN",
            f"EXCHANGE_POSITION:{hot_position['side']}:{hot_position['size']}",
        )

    pending_command = connection.execute(
        """SELECT command_id,state
             FROM runtime.trade_commands
            WHERE command_type='entry' AND symbol=%s
              AND state IN ('queued','running')
              AND (
                   (trading_account_id=%s AND identity_status='PROVEN')
              )
            ORDER BY requested_at_epoch_ms LIMIT 1""",
        (request.symbol, request.trading_account_id),
    ).fetchone()
    if pending_command is not None:
        return (
            "INVARIANT_BROKEN",
            f"PENDING_ENTRY_COMMAND:{pending_command['command_id']}:{pending_command['state']}",
        )

    pending_order = connection.execute(
        """SELECT order_id,order_status
             FROM runtime.hot_orders
            WHERE symbol=%s
              AND trading_account_id=%s
              AND identity_status='PROVEN'
              AND order_status IN ('New','PartiallyFilled','Untriggered')
              AND coalesce((payload_json::jsonb->>'reduceOnly')::boolean,false)=false
            ORDER BY order_id LIMIT 1""",
        (request.symbol, request.trading_account_id),
    ).fetchone()
    if pending_order is not None:
        return (
            "INVARIANT_BROKEN",
            f"PENDING_ENTRY_ORDER:{pending_order['order_id']}:{pending_order['order_status']}",
        )
    return "OK", ""


def _release_pre_exchange_admission(
    connection: psycopg.Connection[Any],
    request: ExecutionRequest,
    *,
    reason: str,
    now: datetime,
) -> None:
    if request.capital_reservation_id is None or request.exchange_position_slot_claim_id is None:
        raise RuntimeError("pre-exchange admission release requires reservation and slot claim")
    updated = connection.execute(
        """UPDATE runtime.capital_reservations
              SET state='RELEASED',state_reason=%s
            WHERE reservation_id=%s AND state='RESERVED'""",
        (reason, request.capital_reservation_id),
    )
    if updated.rowcount != 1:
        existing = connection.execute(
            "SELECT state FROM runtime.capital_reservations WHERE reservation_id=%s",
            (request.capital_reservation_id,),
        ).fetchone()
        if existing is None or str(existing["state"]) != "RELEASED":
            raise RuntimeError("pre-exchange capital reservation release race")
    claim_updated = connection.execute(
        """UPDATE runtime.exchange_position_slot_claims
              SET claim_state='RELEASED',released_at=%s,release_reason=%s,updated_at=%s
            WHERE exchange_position_slot_claim_id=%s
              AND claim_state='CLAIMED'""",
        (
            now.astimezone(UTC),
            reason,
            now.astimezone(UTC),
            request.exchange_position_slot_claim_id,
        ),
    )
    if claim_updated.rowcount != 1:
        existing = connection.execute(
            """SELECT claim_state FROM runtime.exchange_position_slot_claims
                WHERE exchange_position_slot_claim_id=%s""",
            (request.exchange_position_slot_claim_id,),
        ).fetchone()
        if existing is None or str(existing["claim_state"]) != "RELEASED":
            raise RuntimeError("pre-exchange slot claim release race")


def _mark_admission_reconciliation_required(
    connection: psycopg.Connection[Any],
    request: ExecutionRequest,
    *,
    reason: str,
    now: datetime,
) -> None:
    if request.capital_reservation_id is None or request.exchange_position_slot_claim_id is None:
        raise RuntimeError("reconciliation marking requires complete admission lineage")
    connection.execute(
        """UPDATE runtime.capital_reservations
              SET state='RECONCILIATION_REQUIRED',state_reason=%s
            WHERE reservation_id=%s
              AND state IN ('RESERVED','DISPATCHED','PENDING_EXCHANGE_REFLECTION')""",
        (reason, request.capital_reservation_id),
    )
    connection.execute(
        """UPDATE runtime.exchange_position_slot_claims
              SET claim_state='RECONCILIATION_REQUIRED',release_reason=%s,updated_at=%s
            WHERE exchange_position_slot_claim_id=%s
              AND claim_state IN ('CLAIMED','BOUND','RECONCILIATION_REQUIRED')""",
        (
            reason,
            now.astimezone(UTC),
            request.exchange_position_slot_claim_id,
        ),
    )


def _release_expired_reserved_requests(
    connection: psycopg.Connection[Any],
    *,
    now: datetime,
) -> int:
    with connection.transaction():
        rows = connection.execute(
            """SELECT r.*,c.state AS capital_reservation_state,
                      c.account_ref AS capital_reservation_account_ref,
                      c.pre_dispatch_expires_at AS pre_dispatch_expires_at
                 FROM strategy_entry.execution_requests r
                 JOIN runtime.capital_reservations c
                   ON c.reservation_id=r.capital_reservation_id
                  AND c.strategy_attempt_id=r.strategy_attempt_id
                WHERE r.trading_account_id=%s
                  AND r.identity_status='PROVEN'
                  AND c.trading_account_id=%s
                  AND c.identity_status='PROVEN'
                  AND c.state='RESERVED'
                  AND c.pre_dispatch_expires_at IS NOT NULL
                  AND c.pre_dispatch_expires_at <= %s
                  AND NOT EXISTS (
                      SELECT 1 FROM strategy_entry.execution_dispatches d
                       WHERE d.execution_request_id=r.execution_request_id
                  )
                ORDER BY c.pre_dispatch_expires_at,r.execution_request_id
                FOR UPDATE OF c SKIP LOCKED""",
            (TRADING_ACCOUNT_ID, TRADING_ACCOUNT_ID, now.astimezone(UTC)),
        ).fetchall()
        for row in rows:
            request = _request_from_row(row)
            if request.capital_reservation_id is None:
                raise RuntimeError("expired real Entry request lost capital reservation")
            _record_blocked(
                connection,
                request,
                reason="REQUEST_EXPIRED_PRE_DISPATCH",
                payload={
                    "block_code": "REQUEST_EXPIRED",
                    "pre_dispatch_expires_at": str(row["pre_dispatch_expires_at"]),
                },
            )
            _release_pre_exchange_admission(
                connection,
                request,
                reason="REQUEST_EXPIRED_PRE_DISPATCH",
                now=now,
            )
            _record_request_state(
                connection,
                request,
                state="REQUEST_EXPIRED",
                occurred_at=now,
                reason="REQUEST_EXPIRED_PRE_DISPATCH",
            )
    return len(rows)


def _execution_gate_enabled(connection: psycopg.Connection[Any]) -> bool:
    row = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    return bool(row and row["enabled"])


def _critical_lifecycle_fault_code(
    connection: psycopg.Connection[Any], *, trading_account_id: str,
) -> str | None:
    """Fail closed on unscoped or matching-account OPEN CRITICAL faults.

    An exact fault for another TradingAccount is independent; legacy/unscoped
    faults cannot be attributed safely and remain an Entry blocker.
    """
    if not trading_account_id:
        raise RuntimeError("required TradingAccount identity missing")
    row = connection.execute(
        """SELECT fault_code FROM runtime.lifecycle_faults
            WHERE state='OPEN' AND severity='CRITICAL'
              AND (NULLIF(exact_ids->>'trading_account_id','') IS NULL
                   OR exact_ids->>'trading_account_id'=%s)
            ORDER BY detected_at,fault_id LIMIT 1""",
        (trading_account_id,),
    ).fetchone()
    return None if row is None else str(row["fault_code"])


def _live_arm_ready(
    connection: psycopg.Connection[Any],
    row: Mapping[str, object],
    *,
    now: datetime,
) -> tuple[bool, tuple[str, ...]]:
    if not LOADED_RELEASE_COMMIT:
        return False, ("LOADED_RELEASE_COMMIT_MISSING",)
    try:
        context = LiveArmContext(
            strategy_id=str(row["strategy_id"]),
            strategy_version=str(row["strategy_version"]),
            strategy_config_fingerprint=str(row["strategy_config_fingerprint"]),
            strategy_activation_id=str(row["activation_id"]),
            symbol=str(row["symbol"]),
            release_commit=LOADED_RELEASE_COMMIT,
        )
    except ValueError as exc:
        return False, (f"LIVE_ARM_CONTEXT_INVALID:{exc}",)
    scope_ready, scope_reasons = check_legacy_live_arm_account_scope(
        connection,
        strategy_activation_id=context.strategy_activation_id,
        trading_account_id=str(row.get("trading_account_id") or ""),
        now=now,
    )
    if not scope_ready:
        if scope_reasons != ("MULTI_ACCOUNT_OWNER_ARM_SCOPE_UNPROVEN",):
            return False, scope_reasons
        binding_id = str(row.get("strategy_execution_binding_id") or "")
        if not binding_id or not ACCOUNT_CONNECTOR_ID:
            return False, scope_reasons
        # A Strategy/symbol arm NEVER authorizes a second account. Multi-account
        # permission can come only from an exact owner-issued account session.
        return account_live_arm_ready(
            connection,
            strategy_activation_id=context.strategy_activation_id,
            strategy_execution_binding_id=binding_id,
            trading_account_id=str(row.get("trading_account_id") or ""),
            account_connector_id=ACCOUNT_CONNECTOR_ID,
            strategy_id=context.strategy_id,
            strategy_version=context.strategy_version,
            strategy_config_fingerprint=context.strategy_config_fingerprint,
            symbol=context.symbol,
            release_commit=context.release_commit,
            now=now,
        )
    # Durable LIVE-arm evidence is the pre-arm authorization contract. Once
    # an exact release-bound arm session exists, per-request safety is enforced
    # by _admission_pre_dispatch_status() and the request's admission lineage.
    # Re-evaluating short-lived pre-arm evidence here would make an otherwise
    # valid active session self-expire after arm.
    if active_live_arm_session(connection, context=context) is None:
        return False, ("ACTIVE_LIVE_ARM_SESSION",)
    return True, ()


def _refresh_owner_diagnostic_terminal_exit(
    connection: psycopg.Connection[Any], *, now: datetime,
) -> str | None:
    row = connection.execute(
        """SELECT run_id,strategy_position_id,entry_execution_request_id
             FROM control.owner_full_contour_diagnostic_runs
            WHERE trading_account_id=%s
              AND state IN ('DISPATCHED','POSITION_OPEN')
              AND entry_execution_request_id IS NOT NULL
            ORDER BY requested_at DESC LIMIT 1""",
        (TRADING_ACCOUNT_ID,),
    ).fetchone()
    if row is None:
        return None
    position = connection.execute(
        """SELECT position_id,state FROM runtime.position_ownership
            WHERE entry_execution_request_id=%s
              AND trading_account_id=%s
              AND identity_status='PROVEN'
            ORDER BY created_at DESC LIMIT 1""",
        (str(row["entry_execution_request_id"]), TRADING_ACCOUNT_ID),
    ).fetchone()
    if position is None or str(position["state"]) != "CLOSED":
        return None
    position_id = str(position["position_id"])
    universal_exit = connection.execute(
        """SELECT d.exit_decision_id,r.execution_request_id,
                  x.command_id,c.state
             FROM strategy_exit.exit_decisions d
             JOIN strategy_exit.execution_requests r
               ON r.exit_decision_id=d.exit_decision_id
             JOIN strategy_exit.execution_dispatches x
               ON x.execution_request_id=r.execution_request_id
              AND x.state='DISPATCHED'
             JOIN runtime.trade_commands c
               ON c.command_id=x.command_id
              AND c.command_type='strategy_exit'
              AND c.trading_account_id=%s
              AND c.identity_status='PROVEN'
            WHERE d.strategy_position_id=%s
              AND d.action_kind='CLOSE'
              AND c.state='completed'
              AND c.payload_json::jsonb->>'source'='universal_exit'
            ORDER BY d.decided_at DESC,x.created_at DESC
            LIMIT 1""",
        (TRADING_ACCOUNT_ID, position_id),
    ).fetchone()
    if universal_exit is None:
        set_owner_diagnostic_run_state(
            connection,
            run_id=str(row["run_id"]),
            state="FAILED",
            now=now,
            lineage={"strategy_position_id": position_id},
            error=(
                "POSITION_CLOSED_WITHOUT_COMPLETED_UNIVERSAL_EXIT:"
                "exchange protection or another close path ended the "
                "position before full-contour Exit proof"
            ),
        )
        return (
            "OWNER_DIAGNOSTIC_FAILED:"
            "POSITION_CLOSED_WITHOUT_COMPLETED_UNIVERSAL_EXIT"
        )
    set_owner_diagnostic_run_state(
        connection,
        run_id=str(row["run_id"]),
        state="CLOSED",
        now=now,
        lineage={"strategy_position_id": position_id},
    )
    return "OWNER_DIAGNOSTIC_CLOSED:" + position_id


def _refresh_owner_diagnostic_command_failure(
    connection: psycopg.Connection[Any], *, now: datetime,
) -> str | None:
    row = connection.execute(
        """SELECT run_id,entry_command_id
             FROM control.owner_full_contour_diagnostic_runs
            WHERE trading_account_id=%s
              AND state IN ('DISPATCHED','POSITION_OPEN')
              AND entry_command_id IS NOT NULL
            ORDER BY requested_at DESC LIMIT 1""",
        (TRADING_ACCOUNT_ID,),
    ).fetchone()
    if row is None:
        return None
    command = connection.execute(
        """SELECT state,error FROM runtime.trade_commands
            WHERE command_id=%s AND trading_account_id=%s
              AND identity_status='PROVEN'""",
        (str(row["entry_command_id"]), TRADING_ACCOUNT_ID),
    ).fetchone()
    if command is None or str(command["state"]) != "failed":
        return None
    set_owner_diagnostic_run_state(
        connection,
        run_id=str(row["run_id"]),
        state="FAILED",
        now=now,
        error=str(command["error"] or "ENTRY_COMMAND_FAILED"),
    )
    return "OWNER_DIAGNOSTIC_FAILED:ENTRY_COMMAND"


def run_once(connection: psycopg.Connection[Any], *, now: datetime | None = None) -> str:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    _release_expired_reserved_requests(connection, now=current)

    # Process/account route identity is the first runtime ownership gate.
    # Diagnostic lifecycle completion may continue with NEW-Entry gate closed,
    # but never through an unproven or different account connector.
    try:
        route = ConsumerWorkerRoute(TRADING_ACCOUNT_ID, ACCOUNT_CONNECTOR_ID)
    except ValueError:
        return "ACCOUNT_CONNECTOR_ROUTE_NOT_READY"
    route_check = check_consumer_worker_route(connection, route=route)
    if not route_check.ready:
        return "ACCOUNT_CONNECTOR_ROUTE_NOT_READY:" + ",".join(route_check.reasons)

    if not _execution_gate_enabled(connection):
        diagnostic_lifecycle = refresh_run_lifecycle(
            connection,
            trading_account_id=TRADING_ACCOUNT_ID,
            now=current,
        )
        terminal_result = _refresh_owner_diagnostic_terminal_exit(
            connection,
            now=current,
        )
        if terminal_result:
            return terminal_result
        command_result = _refresh_owner_diagnostic_command_failure(
            connection,
            now=current,
        )
        if command_result:
            return command_result
        return diagnostic_lifecycle or "EXECUTION_GATE_DISARMED"
    # Registry routing is only a prerequisite. Per-request binding/connector,
    # independently authenticated private credentials and LIVE arm remain
    # separately required; no cross-account worker fallback.
    safety_ready, safety_reasons = account_entry_safety_ready(
        connection,
        trading_account_id=route.trading_account_id,
        account_connector_id=route.account_connector_id,
    )
    if not safety_ready:
        return "ACCOUNT_ENTRY_SAFETY_NOT_READY:" + ",".join(safety_reasons)
    row = _next_request(connection, route=route)
    if row is None:
        # Ordinary production requests retain priority. Only after the exact
        # process/account route and account safety checks passed, and no normal
        # Entry request is pending, may this consumer inspect an owner
        # diagnostic trigger.
        diagnostic_result = materialize_requested_run(
            connection,
            trading_account_id=TRADING_ACCOUNT_ID,
            account_connector_id=ACCOUNT_CONNECTOR_ID,
            release_commit=LOADED_RELEASE_COMMIT,
            now=current,
        )
        if diagnostic_result and diagnostic_result.startswith(
            "OWNER_DIAGNOSTIC_FAILED:"
        ):
            return diagnostic_result
        if diagnostic_result:
            row = _next_request(connection, route=route)
        if row is None:
            return diagnostic_result or "NO_REQUEST"
    request = _request_from_row(row)
    reservation_state = row.get("capital_reservation_state")
    if (
        request.capital_reservation_id is None
        or request.exchange_position_slot_claim_id is None
        or request.position_mode_state_ref is None
        or reservation_state != "RESERVED"
    ):
        with connection.transaction():
            reason = (
                "EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN:ADMISSION_LINEAGE_INVALID:"
                f"{request.capital_reservation_id}:"
                f"{request.exchange_position_slot_claim_id}:"
                f"{request.position_mode_state_ref}:{reservation_state}"
            )
            _record_blocked(
                connection,
                request,
                reason=reason,
                payload={
                    "capital_reservation_id": request.capital_reservation_id,
                    "exchange_position_slot_claim_id": request.exchange_position_slot_claim_id,
                    "position_mode_state_ref": request.position_mode_state_ref,
                    "capital_reservation_state": reservation_state,
                },
            )
            if (
                request.capital_reservation_id is not None
                and request.exchange_position_slot_claim_id is not None
            ):
                _mark_admission_reconciliation_required(
                    connection,
                    request,
                    reason=reason,
                    now=current,
                )
            _record_request_state(
                connection,
                request,
                state="REQUEST_RECONCILIATION_REQUIRED",
                occurred_at=current,
                reason=reason,
            )
        return "BLOCKED:ADMISSION_LINEAGE_INVALID"

    admission_status, admission_detail = _admission_pre_dispatch_status(
        connection, row, request, now=current
    )
    if admission_status == "BINDING_INVALID":
        reason = f"STRATEGY_EXECUTION_BINDING_NOT_READY:{admission_detail}"
        with connection.transaction():
            _record_blocked(
                connection,
                request,
                reason=reason,
                payload={
                    "block_code": "STRATEGY_EXECUTION_BINDING_NOT_READY",
                    "detail": admission_detail,
                    "strategy_execution_binding_id": request.strategy_execution_binding_id,
                    "execution_leg": request.execution_leg,
                    "trading_account_id": request.trading_account_id,
                },
            )
            _release_pre_exchange_admission(
                connection,
                request,
                reason="PRE_EXCHANGE_BLOCK:STRATEGY_EXECUTION_BINDING_NOT_READY",
                now=current,
            )
            _record_request_state(
                connection,
                request,
                state="REQUEST_CANCELLED",
                occurred_at=current,
                reason=reason,
            )
        return "BLOCKED:STRATEGY_EXECUTION_BINDING_NOT_READY"

    if admission_status in {"STALE_MODE", "MODE_MISMATCH"}:
        reason = (
            "STALE_OR_UNKNOWN_REQUIRED_STATE"
            if admission_status == "STALE_MODE"
            else "EXCHANGE_POSITION_MODE_MISMATCH"
        )
        with connection.transaction():
            _record_blocked(
                connection,
                request,
                reason=f"{reason}:{admission_detail}",
                payload={
                    "block_code": reason,
                    "detail": admission_detail,
                    "position_mode_state_ref": request.position_mode_state_ref,
                },
            )
            _release_pre_exchange_admission(
                connection,
                request,
                reason=reason,
                now=current,
            )
            _record_request_state(
                connection,
                request,
                state="REQUEST_CANCELLED",
                occurred_at=current,
                reason=f"{reason}:{admission_detail}",
            )
        return f"BLOCKED:{reason}"

    if admission_status == "INVARIANT_BROKEN":
        reason = f"EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN:{admission_detail}"
        with connection.transaction():
            _record_blocked(
                connection,
                request,
                reason=reason,
                payload={
                    "fault_code": "EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN",
                    "detail": admission_detail,
                    "exchange_position_slot_claim_id": request.exchange_position_slot_claim_id,
                },
            )
            _mark_admission_reconciliation_required(
                connection,
                request,
                reason=reason,
                now=current,
            )
            _record_request_state(
                connection,
                request,
                state="REQUEST_RECONCILIATION_REQUIRED",
                occurred_at=current,
                reason=reason,
            )
        return "BLOCKED:EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN"

    fault_code = _critical_lifecycle_fault_code(
        connection, trading_account_id=request.trading_account_id or "",
    )
    if fault_code is not None:
        # An existing EntryExecutionRequest is not grandfathered through a
        # newly detected CRITICAL fault. No Exchange mutation has been sent.
        reason = f"CRITICAL_LIFECYCLE_FAULT:{fault_code}"
        with connection.transaction():
            _record_blocked(
                connection, request, reason=reason,
                payload={"block_code": "CRITICAL_LIFECYCLE_FAULT", "fault_code": fault_code},
            )
            _release_pre_exchange_admission(
                connection, request,
                reason="PRE_EXCHANGE_BLOCK:CRITICAL_LIFECYCLE_FAULT", now=current,
            )
            _record_request_state(
                connection, request, state="REQUEST_CANCELLED",
                occurred_at=current, reason=reason,
            )
        return "BLOCKED:CRITICAL_LIFECYCLE_FAULT"

    live_ready, failed_live_checks = _live_arm_ready(connection, row, now=current)
    if not live_ready:
        reason = "LIVE_ARM_NOT_READY:" + ",".join(failed_live_checks)
        with connection.transaction():
            _record_blocked(
                connection,
                request,
                reason=reason,
                payload={
                    "block_code": "LIVE_ARM_NOT_READY",
                    "failed_checks": list(failed_live_checks),
                    "release_commit": LOADED_RELEASE_COMMIT or None,
                },
            )
            _release_pre_exchange_admission(
                connection,
                request,
                reason="PRE_EXCHANGE_BLOCK:LIVE_ARM_NOT_READY",
                now=current,
            )
            _record_request_state(
                connection,
                request,
                state="REQUEST_CANCELLED",
                occurred_at=current,
                reason=reason,
            )
        return "BLOCKED:LIVE_ARM_NOT_READY"

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
            if (
                request.capital_reservation_id is not None
                and request.exchange_position_slot_claim_id is not None
            ):
                _release_pre_exchange_admission(
                    connection,
                    request,
                    reason=f"PRE_EXCHANGE_BLOCK:{exc.code.value}",
                    now=current,
                )
            _record_request_state(
                connection,
                request,
                state="REQUEST_CANCELLED",
                occurred_at=current,
                reason=f"PRE_EXCHANGE_BLOCK:{exc.code.value}",
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
            if (
                request.capital_reservation_id is not None
                and request.exchange_position_slot_claim_id is not None
            ):
                _release_pre_exchange_admission(
                    connection,
                    request,
                    reason="PRE_EXCHANGE_BLOCK:STRUCTURAL_ERROR",
                    now=current,
                )
            _record_request_state(
                connection,
                request,
                state="REQUEST_CANCELLED",
                occurred_at=current,
                reason="PRE_EXCHANGE_BLOCK:STRUCTURAL_ERROR",
            )
        return "BLOCKED:STRUCTURAL_ERROR"
    with connection.transaction():
        _publish_command(connection, prepared, now=current)
        _finalize_owner_diagnostic_dispatch(
            connection,
            prepared,
            now=current,
        )
    return f"DISPATCHED:{prepared.command_id}"


def main() -> int:
    account_kind = os.environ.get("CRIPTA_PRIVATE_WORKER_ACCOUNT_KIND", "MAIN").strip().upper()
    require_exact_sub_instance(
        account_kind=account_kind,
        trading_account_id=TRADING_ACCOUNT_ID,
        argv=sys.argv[1:],
    )
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
    if not TRADING_ACCOUNT_ID:
        raise SystemExit("CRIPTA_TRADING_ACCOUNT_ID is required for Universal Entry consumer")
    if not ACCOUNT_CONNECTOR_ID:
        raise SystemExit("CRIPTA_ACCOUNT_CONNECTOR_ID is required for Universal Entry consumer")
    if POLL_SECONDS <= 0 or HEALTH_SECONDS <= 0:
        raise SystemExit("Universal Entry consumer poll/health seconds must be positive")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    next_health_at = 0.0
    ready = False
    reasons: tuple[str, ...] = ("STARTING",)
    with psycopg.connect(DB_DSN, row_factory=mapping_sequence_row) as connection:
        while running:
            current = datetime.now(UTC)
            monotonic_now = time.monotonic()
            if monotonic_now >= next_health_at:
                ready, reasons = _consumer_runtime_readiness(connection)
                _write_consumer_health(
                    connection,
                    ready=ready,
                    detail="READY" if ready else ",".join(reasons),
                    now=current,
                )
                # Independent no-signal safety guard; never touches positions/Exit.
                check_release_arm_invariant(
                    connection,
                    loaded_commit=LOADED_RELEASE_COMMIT,
                    now=current,
                )
                connection.commit()
                next_health_at = monotonic_now + HEALTH_SECONDS
            if not ready:
                time.sleep(POLL_SECONDS)
                continue
            result = run_once(connection, now=current)
            # Each request/dispatch/fail-closed outcome must become durable
            # before polling the next Entry. A continuous signal stream
            # must not hold slot/advisory locks or queued commands uncommitted.
            connection.commit()
            if not result.startswith("DISPATCHED") and not result.startswith("BLOCKED"):
                time.sleep(POLL_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
