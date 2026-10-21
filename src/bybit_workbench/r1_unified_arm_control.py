from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from .live_arm_readiness import (
    active_live_arm_session,
    evaluate_live_arm,
    strategy_symbol_scope_key,
)
from .r1_unified_prearm import unified_r1_contexts
from .universal_entry.dashboard_control import StrategyDashboardStore
from .universal_entry.r1_strategy import (
    R1_UNIFIED_STRATEGY_ID,
    R1_UNIFIED_VERSION,
)


def _require_operator_intent(
    *, operator: str, request_id: str, now: datetime
) -> datetime:
    if not operator.strip() or operator.upper() == "UNKNOWN":
        raise ValueError("UNIFIED_R1_OWNER_IDENTITY_REQUIRED")
    if len(request_id.strip()) < 8:
        raise ValueError("UNIFIED_R1_OWNER_REQUEST_ID_REQUIRED")
    if now.tzinfo is None:
        raise ValueError("UNIFIED_R1_OWNER_ACTION_TIME_REQUIRES_UTC")
    return now.astimezone(UTC)


def arm_unified_r1(
    connection: Any,
    *,
    release_commit: str,
    now: datetime,
    operator: str,
    request_id: str,
    confirmed: bool,
    observer_ready: bool,
    baseline_ready: bool,
    baseline_reasons: tuple[str, ...] = (),
) -> dict[str, object]:
    """One explicit owner action; one Strategy permission; five arm scope rows.

    The five symbol-scoped DB sessions are physical admission witnesses used by
    Observer/Universal Entry Consumer, NOT five independent owner approvals.
    No Exchange order is issued here. Caller owns authenticated UI and top
    connection lifetime; this method owns one atomic DB transaction.
    """
    current = _require_operator_intent(
        operator=operator, request_id=request_id, now=now
    )
    if confirmed is not True:
        raise ValueError("UNIFIED_R1_OWNER_ARM_NOT_EXPLICITLY_CONFIRMED")
    if not baseline_ready:
        raise ValueError(
            "UNIFIED_R1_OPERATIONAL_BASELINE_BLOCKED:"
            + ",".join(baseline_reasons)
        )
    if not observer_ready:
        raise ValueError("UNIFIED_R1_OBSERVER_NOT_READY")

    with connection.transaction():
        gate = connection.execute(
            "SELECT enabled FROM control.execution_gates "
            "WHERE mode='mainnet' FOR UPDATE"
        ).fetchone()
        if gate is None or bool(gate[0]):
            raise ValueError("UNIFIED_R1_MAINNET_GATE_NOT_CLOSED")

        contexts, trading_account_id = unified_r1_contexts(
            connection, release_commit=release_commit, now=current
        )
        if len(contexts) != 5:
            raise ValueError("UNIFIED_R1_FIVE_INSTRUMENT_SCOPES_MISSING")

        # Lock exact binding + TradingAccount + activation using the
        # canonical SECURITY DEFINER helper (no broad UPDATE grant to cripta).
        binding_ids = connection.execute(
            """SELECT strategy_execution_binding_id
                 FROM strategy_entry.strategy_execution_bindings
                WHERE strategy_activation_id=%s AND binding_state='ACTIVE'""",
            (contexts[0].strategy_activation_id,),
        ).fetchall()
        if len(binding_ids) != 1:
            raise ValueError("UNIFIED_R1_EXACT_BINDING_LOCK_ID_NOT_UNIQUE")
        locked = connection.execute(
            """SELECT strategy_activation_id,execution_leg,trading_account_id,
                      execution_role,allocation_mode,binding_state,
                      trading_account_status,environment,activation_enabled
                 FROM strategy_entry.lock_strategy_execution_binding(%s)""",
            (str(binding_ids[0][0]),),
        ).fetchone()
        if locked is None or (
            str(locked[0]), str(locked[1]), str(locked[2]),
            str(locked[3]), str(locked[4]), str(locked[5]),
            str(locked[6]), str(locked[7]), locked[8],
        ) != (
            contexts[0].strategy_activation_id, "PRIMARY",
            trading_account_id, "PRIMARY", "DEDICATED_STRATEGY",
            "ACTIVE", "ACTIVE", "MAINNET", True,
        ):
            raise ValueError("UNIFIED_R1_LOCKED_BINDING_IDENTITY_CHANGED")

        permissions = connection.execute(
            """SELECT strategy_id FROM strategy_entry.execution_permissions
                WHERE enabled=true"""
        ).fetchall()
        if permissions:
            raise ValueError("UNIFIED_R1_OTHER_EXECUTION_PERMISSION_ACTIVE")
        sessions = connection.execute(
            "SELECT live_arm_session_id FROM control.live_arm_sessions "
            "WHERE state='ACTIVE'"
        ).fetchall()
        if sessions:
            raise ValueError("UNIFIED_R1_PREVIOUS_OR_UNKNOWN_ARM_SESSION_ACTIVE")

        # No copying of old per-Strategy checks or special CRITICAL delivery
        # waiver. Each exact new Strategy-symbol context needs independently
        # current release-scoped durable evidence for all 22 pre-owner checks.
        for context in contexts:
            proof = evaluate_live_arm(
                connection,
                context=context,
                now=current,
                require_owner_approval=False,
            )
            if not proof.ready:
                raise ValueError(
                    "UNIFIED_R1_PRE_OWNER_EVIDENCE_MISSING:"
                    + context.symbol + ":" + ",".join(proof.failed_codes)
                )

        source = "dashboard:r1-unified-micro-live"
        ids: list[str] = []
        for context in contexts:
            scope = strategy_symbol_scope_key(context)
            witness = request_id + "|" + scope + "|" + release_commit
            fingerprint = hashlib.sha256(witness.encode()).hexdigest()[:32]
            evidence_id = "r1-unified-owner-" + fingerprint
            session_id = "r1-unified-session-" + fingerprint
            connection.execute(
                """INSERT INTO control.live_arm_evidence(
                     evidence_id,check_code,scope_type,scope_key,status,
                     checked_at,valid_until,release_commit,source,evidence)
                   VALUES(
                     %s,'MAINNET_GATE_EXPLICIT_OWNER_APPROVAL',
                     'STRATEGY_SYMBOL',%s,'PASS',%s,NULL,%s,%s,%s::jsonb)""",
                (
                    evidence_id, scope, current, release_commit, source,
                    json.dumps({
                        "owner_action_request_id": request_id,
                        "operator": operator,
                        "strategy_id": R1_UNIFIED_STRATEGY_ID,
                        "strategy_version": R1_UNIFIED_VERSION,
                        "trading_account_id": trading_account_id,
                        "symbol": context.symbol,
                        "one_strategy_owner_arm": True,
                        "requested_amount_usdt": "10",
                        "aggregate_committed_usdt_max": "50",
                        "leverage": 1,
                        "catastrophic_stop_pct": "10.0",
                    }, sort_keys=True),
                ),
            )
            full = evaluate_live_arm(
                connection,
                context=context,
                now=current,
                require_owner_approval=True,
            )
            if not full.ready:
                raise ValueError(
                    "UNIFIED_R1_OWNER_ARM_FINAL_EVIDENCE_BLOCKED:"
                    + context.symbol + ":" + ",".join(full.failed_codes)
                )
            ids.append(session_id)

        # Only ONE immutable Strategy receives an ExecutionPermission.
        ctx = contexts[0]
        StrategyDashboardStore(connection).set_execution_permission(
            strategy_id=ctx.strategy_id,
            strategy_version=ctx.strategy_version,
            strategy_config_fingerprint=ctx.strategy_config_fingerprint,
            enabled=True,
            changed_at=current,
            operator=operator,
            source=source,
            reason="owner confirmed one Strategy/five instrument MICRO_LIVE",
            observer_ready=True,
        )
        for ctx, session_id in zip(contexts, ids, strict=True):
            connection.execute(
                """INSERT INTO control.live_arm_sessions(
                     live_arm_session_id,strategy_id,strategy_version,
                     strategy_config_fingerprint,strategy_activation_id,
                     symbol,release_commit,state,owner_approved_at,
                     activated_at,deactivated_at,source)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s,%s,NULL,%s)""",
                (
                    session_id, ctx.strategy_id, ctx.strategy_version,
                    ctx.strategy_config_fingerprint,
                    ctx.strategy_activation_id, ctx.symbol,
                    release_commit, current, current, source,
                ),
            )
            if active_live_arm_session(connection, context=ctx) != session_id:
                raise ValueError(
                    "UNIFIED_R1_ARM_SESSION_READBACK_FAILED:" + ctx.symbol
                )

        connection.execute(
            """UPDATE control.execution_gates
                  SET enabled=1,
                      reason='unified R1 MICRO_LIVE explicitly armed by owner',
                      updated_at_epoch_ms=%s
                WHERE mode='mainnet' AND enabled=0""",
            (int(current.timestamp() * 1000),),
        )
        connection.execute(
            """INSERT INTO control.execution_gate_events(
                   at_epoch_ms,mode,previous_enabled,requested_enabled,
                   resulting_enabled,reason,source,origin,request_id,
                   settings_version)
               VALUES(%s,'mainnet',false,true,true,%s,%s,'owner',%s,%s)""",
            (
                int(current.timestamp() * 1000),
                "unified R1 MICRO_LIVE explicitly armed by owner",
                source, request_id, R1_UNIFIED_VERSION,
            ),
        )
    return {
        "status": "ARMED",
        "strategies": 1,
        "instrument_scopes": 5,
        "trading_account_id": trading_account_id,
        "session_ids": ids,
        "requested_amount_usdt": "10",
        "aggregate_cap_usdt": "50",
        "gate_enabled": True,
    }


def disarm_unified_r1(
    connection: Any, *, now: datetime, operator: str, request_id: str
) -> dict[str, object]:
    """Owner kill is allowed even with stale evidence or disconnected WS."""
    current = _require_operator_intent(
        operator=operator, request_id=request_id, now=now
    )
    source = "dashboard:r1-unified-micro-live"
    with connection.transaction():
        gate = connection.execute(
            "SELECT enabled FROM control.execution_gates "
            "WHERE mode='mainnet' FOR UPDATE"
        ).fetchone()
        previous = bool(gate and gate[0])
        connection.execute(
            """UPDATE control.execution_gates SET enabled=0,
                   reason='unified R1 MICRO_LIVE stopped by owner',
                   updated_at_epoch_ms=%s WHERE mode='mainnet'""",
            (int(current.timestamp() * 1000),),
        )
        connection.execute(
            """UPDATE control.live_arm_sessions
                  SET state='CLOSED',deactivated_at=%s,
                      updated_at=clock_timestamp()
                WHERE state='ACTIVE' AND strategy_id=%s""",
            (current, R1_UNIFIED_STRATEGY_ID),
        )
        connection.execute(
            """UPDATE strategy_entry.execution_permissions
                  SET enabled=false,disabled_at=%s,operator=%s,
                      source=%s,
                      change_reason='unified R1 MICRO_LIVE stopped by owner'
                WHERE enabled=true AND strategy_id=%s""",
            (current, operator, source, R1_UNIFIED_STRATEGY_ID),
        )
        connection.execute(
            """INSERT INTO control.execution_gate_events(
                   at_epoch_ms,mode,previous_enabled,requested_enabled,
                   resulting_enabled,reason,source,origin,request_id,
                   settings_version)
               VALUES(%s,'mainnet',%s,false,false,%s,%s,'owner',%s,%s)""",
            (
                int(current.timestamp() * 1000),
                previous, "unified R1 MICRO_LIVE stopped by owner",
                source, request_id, R1_UNIFIED_VERSION,
            ),
        )
    return {"status": "DISARMED", "strategies": 1, "gate_enabled": False}
