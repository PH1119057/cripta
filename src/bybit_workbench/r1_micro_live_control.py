from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from .live_arm_readiness import (
    LiveArmContext,
    evaluate_live_arm,
    strategy_symbol_scope_key,
)
from .universal_entry.dashboard_control import StrategyDashboardStore
from .universal_entry.r1_strategy import R1_STRATEGY_IDS, R1_VERSION, build_r1_cards


def expected_contexts(
    connection: Any,
    *,
    release_commit: str,
) -> tuple[LiveArmContext, ...]:
    if len(release_commit) != 40:
        raise ValueError("R1 MICRO_LIVE loaded release commit is invalid")
    expected = {card.strategy_id: card for card in build_r1_cards()}
    contexts: list[LiveArmContext] = []
    for strategy_id in R1_STRATEGY_IDS.values():
        card = expected[strategy_id]
        stored = connection.execute(
            """SELECT strategy_config_fingerprint
                 FROM strategy_entry.strategy_cards
                WHERE strategy_id=%s AND strategy_version=%s""",
            (strategy_id, R1_VERSION),
        ).fetchone()
        if stored is None:
            raise ValueError(f"R1 StrategyCard missing: {strategy_id}")
        if str(stored[0]) != card.strategy_config_fingerprint:
            raise ValueError(f"R1 immutable fingerprint mismatch: {strategy_id}")
        activations = connection.execute(
            """SELECT activation_id
                 FROM strategy_entry.strategy_activations
                WHERE strategy_id=%s AND strategy_version=%s
                  AND strategy_config_fingerprint=%s AND enabled=true
                ORDER BY created_at,activation_id""",
            (strategy_id, R1_VERSION, card.strategy_config_fingerprint),
        ).fetchall()
        if len(activations) != 1:
            raise ValueError(
                f"R1 exact active StrategyActivation required: {strategy_id}"
            )
        contexts.append(
            LiveArmContext(
                strategy_id=strategy_id,
                strategy_version=R1_VERSION,
                strategy_config_fingerprint=card.strategy_config_fingerprint,
                strategy_activation_id=str(activations[0][0]),
                symbol=card.symbols[0],
                release_commit=release_commit,
            )
        )
    return tuple(contexts)


def micro_live_state(
    connection: Any,
    *,
    release_commit: str,
    now: datetime,
) -> dict[str, object]:
    current = now.astimezone(UTC)
    gate_row = connection.execute(
        "SELECT enabled,reason FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    gate_enabled = bool(gate_row and gate_row[0])
    gate_reason = None if gate_row is None else str(gate_row[1])
    try:
        contexts = expected_contexts(connection, release_commit=release_commit)
    except ValueError as exc:
        return {
            "installed": False,
            "armed": False,
            "gate_enabled": gate_enabled,
            "gate_reason": gate_reason,
            "ready_to_arm": False,
            "reason": str(exc),
            "strategies": [],
        }

    rows: list[dict[str, object]] = []
    pre_owner_ready = True
    active_sessions = 0
    for context in contexts:
        decision = evaluate_live_arm(
            connection,
            context=context,
            now=current,
            require_owner_approval=False,
        )
        permission = connection.execute(
            """SELECT enabled
                 FROM strategy_entry.execution_permissions
                WHERE strategy_id=%s AND strategy_version=%s
                  AND strategy_config_fingerprint=%s""",
            (
                context.strategy_id,
                context.strategy_version,
                context.strategy_config_fingerprint,
            ),
        ).fetchone()
        session = connection.execute(
            """SELECT live_arm_session_id
                 FROM control.live_arm_sessions
                WHERE strategy_id=%s AND strategy_version=%s
                  AND strategy_config_fingerprint=%s
                  AND strategy_activation_id=%s AND symbol=%s
                  AND release_commit=%s AND state='ACTIVE'
                ORDER BY activated_at DESC,created_at DESC LIMIT 1""",
            (
                context.strategy_id,
                context.strategy_version,
                context.strategy_config_fingerprint,
                context.strategy_activation_id,
                context.symbol,
                context.release_commit,
            ),
        ).fetchone()
        if session is not None:
            active_sessions += 1
        if not decision.ready:
            pre_owner_ready = False
        rows.append(
            {
                "strategy_id": context.strategy_id,
                "strategy_version": context.strategy_version,
                "symbol": context.symbol,
                "execution_permission": bool(permission and permission[0]),
                "active_session": None if session is None else str(session[0]),
                "pre_owner_ready": decision.ready,
                "failed_checks": list(decision.failed_codes),
            }
        )
    return {
        "installed": True,
        "armed": gate_enabled and active_sessions == len(contexts),
        "gate_enabled": gate_enabled,
        "gate_reason": gate_reason,
        "ready_to_arm": pre_owner_ready and not gate_enabled,
        "release_commit": release_commit,
        "strategies": rows,
    }


def _assert_no_other_execution_permission(connection: Any) -> None:
    row = connection.execute(
        """SELECT strategy_id,strategy_version
             FROM strategy_entry.execution_permissions
            WHERE enabled=true
              AND NOT (
                strategy_id = ANY(%s)
                AND strategy_version=%s
              )
            LIMIT 1""",
        (list(R1_STRATEGY_IDS.values()), R1_VERSION),
    ).fetchone()
    if row is not None:
        raise ValueError(
            "R1 MICRO_LIVE requires all non-R1 execution permissions OFF: "
            f"{row[0]} {row[1]}"
        )


def arm(
    connection: Any,
    *,
    release_commit: str,
    now: datetime,
    operator: str,
    request_id: str,
    observer_ready: bool,
    baseline_ready: bool,
    baseline_reasons: tuple[str, ...] = (),
) -> dict[str, object]:
    current = now.astimezone(UTC)
    if not baseline_ready:
        raise ValueError(
            "R1 MICRO_LIVE operational preflight blocked: "
            + "; ".join(baseline_reasons)
        )
    if not observer_ready:
        raise ValueError("R1 MICRO_LIVE observer is not fresh/ready")
    gate = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet' FOR UPDATE"
    ).fetchone()
    if gate is None or bool(gate[0]):
        raise ValueError("R1 MICRO_LIVE requires a closed mainnet gate")
    _assert_no_other_execution_permission(connection)
    contexts = expected_contexts(connection, release_commit=release_commit)

    for context in contexts:
        pre = evaluate_live_arm(
            connection,
            context=context,
            now=current,
            require_owner_approval=False,
        )
        if not pre.ready:
            raise ValueError(
                f"R1 MICRO_LIVE readiness blocked {context.strategy_id}: "
                + ",".join(pre.failed_codes)
            )

    # Owner explicitly re-armed after the global gate was closed. Retire
    # previous ACTIVE sessions for the same exact cohort transactionally;
    # a deployment/restart must never close them on its own.
    # No execution permission or Exchange object is modified here.
    connection.execute(
        """UPDATE control.live_arm_sessions
              SET state='CLOSED',deactivated_at=%s,updated_at=clock_timestamp()
            WHERE state='ACTIVE'
              AND (strategy_id,strategy_version,strategy_config_fingerprint,
                   strategy_activation_id,symbol) IN (
                  SELECT strategy_id,strategy_version,strategy_config_fingerprint,
                         strategy_activation_id,symbol
                    FROM strategy_entry.strategy_activations
                   WHERE enabled=true AND strategy_version=%s
                     AND strategy_id = ANY(%s)
              )""",
        (current, R1_VERSION, list(R1_STRATEGY_IDS.values())),
    )

    store = StrategyDashboardStore(connection)
    for context in contexts:
        store.set_execution_permission(
            strategy_id=context.strategy_id,
            strategy_version=context.strategy_version,
            strategy_config_fingerprint=context.strategy_config_fingerprint,
            enabled=True,
            changed_at=current,
            operator=operator,
            source="dashboard:r1-micro-live",
            reason="owner armed exact R1 MICRO_LIVE cohort",
            observer_ready=True,
        )

    sessions: list[str] = []
    for context in contexts:
        scope_key = strategy_symbol_scope_key(context)
        identity = request_id + "|" + scope_key + "|" + release_commit
        evidence_id = "live-owner-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
        session_id = "live-session-" + hashlib.sha256(identity.encode()).hexdigest()[:32]
        connection.execute(
            """INSERT INTO control.live_arm_evidence(
                   evidence_id,check_code,scope_type,scope_key,status,
                   checked_at,valid_until,release_commit,source,evidence)
               VALUES(
                   %s,'MAINNET_GATE_EXPLICIT_OWNER_APPROVAL',
                   'STRATEGY_SYMBOL',%s,'PASS',%s,NULL,%s,
                   'dashboard:r1-micro-live',%s::jsonb)""",
            (
                evidence_id,
                scope_key,
                current,
                release_commit,
                json.dumps(
                    {
                        "request_id": request_id,
                        "strategy_id": context.strategy_id,
                        "symbol": context.symbol,
                        "requested_amount_usdt": "10",
                        "leverage": 1,
                        "catastrophic_stop_pct": "10.0",
                    },
                    sort_keys=True,
                ),
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
                f"R1 MICRO_LIVE final readiness blocked {context.strategy_id}: "
                + ",".join(full.failed_codes)
            )
        connection.execute(
            """INSERT INTO control.live_arm_sessions(
                   live_arm_session_id,strategy_id,strategy_version,
                   strategy_config_fingerprint,strategy_activation_id,
                   symbol,release_commit,state,owner_approved_at,
                   activated_at,deactivated_at,source)
               VALUES(%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s,%s,NULL,
                      'dashboard:r1-micro-live')""",
            (
                session_id,
                context.strategy_id,
                context.strategy_version,
                context.strategy_config_fingerprint,
                context.strategy_activation_id,
                context.symbol,
                release_commit,
                current,
                current,
            ),
        )
        sessions.append(session_id)

    connection.execute(
        """UPDATE control.execution_gates
              SET enabled=1,
                  reason='R1 MICRO_LIVE explicitly armed by owner',
                  updated_at_epoch_ms=%s
            WHERE mode='mainnet'""",
        (int(current.timestamp() * 1000),),
    )
    connection.execute(
        """INSERT INTO control.execution_gate_events(
               at_epoch_ms,mode,previous_enabled,requested_enabled,
               resulting_enabled,reason,source,origin,request_id,
               settings_version)
           VALUES(%s,'mainnet',false,true,true,%s,
                  'dashboard:r1-micro-live','owner',%s,%s)""",
        (
            int(current.timestamp() * 1000),
            "R1 MICRO_LIVE explicitly armed by owner",
            request_id,
            R1_VERSION,
        ),
    )
    return {
        "status": "ARMED",
        "gate_enabled": True,
        "strategies": len(contexts),
        "sessions": sessions,
        "requested_amount_usdt": "10",
        "leverage": 1,
    }


def disarm(
    connection: Any,
    *,
    now: datetime,
    operator: str,
    request_id: str,
) -> dict[str, object]:
    current = now.astimezone(UTC)
    gate = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet' FOR UPDATE"
    ).fetchone()
    previous = bool(gate and gate[0])
    connection.execute(
        """UPDATE control.execution_gates
              SET enabled=0,
                  reason='R1 MICRO_LIVE stopped by owner',
                  updated_at_epoch_ms=%s
            WHERE mode='mainnet'""",
        (int(current.timestamp() * 1000),),
    )
    connection.execute(
        """UPDATE control.live_arm_sessions
              SET state='CLOSED',deactivated_at=%s,updated_at=clock_timestamp()
            WHERE state='ACTIVE' AND strategy_id = ANY(%s)""",
        (current, list(R1_STRATEGY_IDS.values())),
    )
    connection.execute(
        """UPDATE strategy_entry.execution_permissions
              SET enabled=false,disabled_at=%s,operator=%s,
                  source='dashboard:r1-micro-live',
                  change_reason='R1 MICRO_LIVE stopped by owner'
            WHERE enabled=true
              AND strategy_id = ANY(%s)
              AND strategy_version=%s""",
        (current, operator, list(R1_STRATEGY_IDS.values()), R1_VERSION),
    )
    connection.execute(
        """INSERT INTO control.execution_gate_events(
               at_epoch_ms,mode,previous_enabled,requested_enabled,
               resulting_enabled,reason,source,origin,request_id,
               settings_version)
           VALUES(%s,'mainnet',%s,false,false,%s,
                  'dashboard:r1-micro-live','owner',%s,%s)""",
        (
            int(current.timestamp() * 1000),
            previous,
            "R1 MICRO_LIVE stopped by owner",
            request_id,
            R1_VERSION,
        ),
    )
    return {"status": "DISARMED", "gate_enabled": False, "strategies": 5}
