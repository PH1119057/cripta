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

from bybit_workbench.live_arm_readiness import (
    LiveArmContext,
    active_live_arm_session,
    evaluate_live_arm,
)
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
LOADED_RELEASE_COMMIT = os.environ.get("CRIPTA_RELEASE_COMMIT", "").strip().lower()

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
        exchange_position_slot_claim_id=(
            None if row.get("exchange_position_slot_claim_id") is None
            else str(row["exchange_position_slot_claim_id"])
        ),
        position_mode_state_ref=(
            None if row.get("position_mode_state_ref") is None
            else str(row["position_mode_state_ref"])
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
    )
    _record_request_state(
        connection,
        request,
        state="REQUEST_DISPATCHED",
        occurred_at=now,
        reason="DISPATCHED_TO_EXECUTION",
        payload={"command_id": prepared.command_id},
    )


def _admission_pre_dispatch_status(
    connection: psycopg.Connection[Any],
    row: Mapping[str, object],
    request: ExecutionRequest,
    *,
    now: datetime,
) -> tuple[str, str]:
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
                  claim_state
             FROM runtime.exchange_position_slot_claims
            WHERE exchange_position_slot_claim_id=%s""",
        (request.exchange_position_slot_claim_id,),
    ).fetchone()
    if claim is None:
        return "INVARIANT_BROKEN", "SLOT_CLAIM_MISSING"
    expected_key = exchange_position_key(request.symbol, CURRENT_POSITION_IDX)
    actual = (
        str(claim["exchange_position_key"]),
        str(claim["account_ref"]),
        str(claim["symbol"]),
        int(claim["position_idx"]),
        str(claim["strategy_attempt_id"]),
        str(claim["position_mode_state_ref"]),
        str(claim["capital_reservation_id"]),
        str(claim["claim_state"]),
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
    )
    if actual != expected:
        return "INVARIANT_BROKEN", f"SLOT_CLAIM_IDENTITY_MISMATCH:{actual!r}"

    mode = connection.execute(
        """SELECT position_mode,position_idx,observed_at,received_at,fresh_until
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
        or fresh_until.astimezone(UTC) < now
    ):
        return "STALE_MODE", "POSITION_MODE_STATE_STALE"
    mode_position_idx = (
        -1 if mode["position_idx"] is None else int(mode["position_idx"])
    )
    if str(mode["position_mode"]) != "ONE_WAY" or mode_position_idx != 0:
        return (
            "MODE_MISMATCH",
            f"EXCHANGE_POSITION_MODE_MISMATCH:{mode['position_mode']}:{mode['position_idx']}",
        )

    newer = connection.execute(
        """SELECT position_mode_state_ref,position_mode,position_idx,fresh_until
             FROM runtime.position_mode_states
            WHERE account_ref=%s AND product_category='LINEAR' AND instrument=%s
            ORDER BY observed_at DESC,created_at DESC LIMIT 1""",
        (CURRENT_ACCOUNT_REF, request.symbol),
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
              AND NULLIF(size,'')::numeric > 0
            LIMIT 1""",
        (request.symbol,),
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
            ORDER BY requested_at_epoch_ms LIMIT 1""",
        (request.symbol,),
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
              AND order_status IN ('New','PartiallyFilled','Untriggered')
              AND coalesce((payload_json::jsonb->>'reduceOnly')::boolean,false)=false
            ORDER BY order_id LIMIT 1""",
        (request.symbol,),
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
                WHERE c.state='RESERVED'
                  AND c.pre_dispatch_expires_at IS NOT NULL
                  AND c.pre_dispatch_expires_at <= %s
                  AND NOT EXISTS (
                      SELECT 1 FROM strategy_entry.execution_dispatches d
                       WHERE d.execution_request_id=r.execution_request_id
                  )
                ORDER BY c.pre_dispatch_expires_at,r.execution_request_id
                FOR UPDATE OF c SKIP LOCKED""",
            (now.astimezone(UTC),),
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
    decision = evaluate_live_arm(
        connection,
        context=context,
        now=now,
        require_owner_approval=True,
    )
    failed = list(decision.failed_codes)
    if active_live_arm_session(connection, context=context) is None:
        failed.append("ACTIVE_LIVE_ARM_SESSION")
    return not failed, tuple(failed)


def run_once(connection: psycopg.Connection[Any], *, now: datetime | None = None) -> str:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    _release_expired_reserved_requests(connection, now=current)
    if not _execution_gate_enabled(connection):
        return "EXECUTION_GATE_DISARMED"
    row = _next_request(connection)
    if row is None:
        return "NO_REQUEST"
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
