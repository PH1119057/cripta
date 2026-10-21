from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .live_arm_readiness import (
    LiveArmContext,
    evaluate_live_arm,
)
from .universal_entry.r1_strategy import (
    R1_UNIFIED_STRATEGY_ID,
    R1_UNIFIED_VERSION,
    build_r1_unified_card,
)


class UnifiedR1PrearmBlocked(ValueError):
    """Exact unified-R1 admission evidence is missing or inconsistent."""


def unified_r1_contexts(
    connection: Any,
    *,
    release_commit: str,
    now: datetime,
) -> tuple[tuple[LiveArmContext, ...], str]:
    """One immutable Strategy, one active activation, one PRIMARY TradingAccount.

    Five LiveArmContext records are *instrument scopes*, not five Strategies,
    TradingAccounts, ExecutionPermissions or independent owner approvals.
    """
    card = build_r1_unified_card()
    current = now.astimezone(UTC)
    if len(release_commit) != 40 or any(
        c not in "0123456789abcdef" for c in release_commit.lower()
    ):
        raise UnifiedR1PrearmBlocked("LOADED_RELEASE_COMMIT_NOT_EXACT")

    saved = connection.execute(
        """SELECT strategy_config_fingerprint
             FROM strategy_entry.strategy_cards
            WHERE strategy_id=%s AND strategy_version=%s""",
        (R1_UNIFIED_STRATEGY_ID, R1_UNIFIED_VERSION),
    ).fetchall()
    if len(saved) != 1 or str(saved[0][0]) != card.strategy_config_fingerprint:
        raise UnifiedR1PrearmBlocked("UNIFIED_R1_IMMUTABLE_CARD_MISMATCH")

    activations = connection.execute(
        """SELECT activation_id,strategy_config_fingerprint
             FROM strategy_entry.strategy_activations
            WHERE strategy_id=%s AND strategy_version=%s AND enabled=true""",
        (R1_UNIFIED_STRATEGY_ID, R1_UNIFIED_VERSION),
    ).fetchall()
    if len(activations) != 1:
        raise UnifiedR1PrearmBlocked("UNIFIED_R1_EXACT_ACTIVATION_NOT_UNIQUE")
    activation_id, active_fingerprint = (str(x) for x in activations[0])
    if active_fingerprint != card.strategy_config_fingerprint:
        raise UnifiedR1PrearmBlocked("UNIFIED_R1_ACTIVATION_FINGERPRINT_MISMATCH")

    total = connection.execute(
        "SELECT count(*) FROM strategy_entry.strategy_activations WHERE enabled=true"
    ).fetchone()
    if total is None or int(str(total[0])) != 1:
        raise UnifiedR1PrearmBlocked("OTHER_ACTIVE_STRATEGY_ACTIVATION")

    bindings = connection.execute(
        """SELECT b.trading_account_id,b.execution_leg,b.execution_role,
                  b.allocation_mode,b.effective_from,b.effective_to,
                  ta.status,ta.environment
             FROM strategy_entry.strategy_execution_bindings AS b
             JOIN identity.trading_accounts AS ta
               ON ta.trading_account_id=b.trading_account_id
            WHERE b.strategy_activation_id=%s AND b.binding_state='ACTIVE'""",
        (activation_id,),
    ).fetchall()
    if len(bindings) != 1:
        raise UnifiedR1PrearmBlocked("PRIMARY_TRADING_ACCOUNT_BINDING_NOT_UNIQUE")
    account, leg, role, allocation, start, end, status, environment = bindings[0]
    if (
        not str(account).strip()
        or (str(leg), str(role), str(allocation))
        != ("PRIMARY", "PRIMARY", "DEDICATED_STRATEGY")
        or str(status) != "ACTIVE"
        or str(environment) != "MAINNET"
        or not isinstance(start, datetime)
        or start.tzinfo is None
        or start.astimezone(UTC) > current
        or (end is not None and (
            not isinstance(end, datetime)
            or end.tzinfo is None
            or end.astimezone(UTC) <= current
        ))
    ):
        raise UnifiedR1PrearmBlocked("PRIMARY_TRADING_ACCOUNT_BINDING_NOT_PROVEN")

    contexts = tuple(
        LiveArmContext(
            strategy_id=card.strategy_id,
            strategy_version=card.strategy_version,
            strategy_config_fingerprint=card.strategy_config_fingerprint,
            strategy_activation_id=activation_id,
            symbol=symbol,
            release_commit=release_commit,
        )
        for symbol in card.symbols
    )
    if len(contexts) != 5 or len({ctx.symbol for ctx in contexts}) != 5:
        raise UnifiedR1PrearmBlocked("FIVE_EXACT_R1_INSTRUMENT_SCOPES_NOT_PROVEN")
    return contexts, str(account)


def unified_r1_prearm_state(
    connection: Any, *, release_commit: str, now: datetime
) -> dict[str, object]:
    """Read-only exact-released R1 readiness; never mutates arm/gate state."""
    current = now.astimezone(UTC)
    gate = connection.execute(
        "SELECT enabled,reason FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    gate_enabled = bool(gate and gate[0])
    gate_reason = str(gate[1]) if gate is not None else None
    base = {
        "cohort": "R1_UNIFIED_FIVE_SYMBOLS",
        "release_commit": release_commit,
        "gate_enabled": gate_enabled,
        "gate_reason": gate_reason,
        "ready_to_arm": False,
        "arm_route_enabled": True,
        "owner_approval_required": True,
        "strategies": [],
        "instrument_scopes": [],
        "reasons": [],
    }
    try:
        contexts, account = unified_r1_contexts(
            connection, release_commit=release_commit, now=current
        )
    except UnifiedR1PrearmBlocked as exc:
        return {
            **base,
            "installed": False,
            "binding_verified": False,
            "reasons": [str(exc)],
        }

    permission = connection.execute(
        """SELECT enabled FROM strategy_entry.execution_permissions
            WHERE strategy_id=%s AND strategy_version=%s
              AND strategy_config_fingerprint=%s""",
        (
            contexts[0].strategy_id,
            contexts[0].strategy_version,
            contexts[0].strategy_config_fingerprint,
        ),
    ).fetchone()
    all_permissions = connection.execute(
        """SELECT strategy_id,strategy_version,strategy_config_fingerprint
             FROM strategy_entry.execution_permissions WHERE enabled=true"""
    ).fetchall()
    arms = connection.execute(
        """SELECT strategy_id,strategy_version,strategy_config_fingerprint,
                  strategy_activation_id,symbol,release_commit
             FROM control.live_arm_sessions WHERE state='ACTIVE'"""
    ).fetchall()
    expected = {
        (
            ctx.strategy_id, ctx.strategy_version,
            ctx.strategy_config_fingerprint, ctx.strategy_activation_id,
            ctx.symbol, ctx.release_commit,
        )
        for ctx in contexts
    }
    observed = {tuple(str(v) for v in arm) for arm in arms}
    exact_permission = (
        len(all_permissions) == 1
        and tuple(str(v) for v in all_permissions[0])
        == (
            contexts[0].strategy_id,
            contexts[0].strategy_version,
            contexts[0].strategy_config_fingerprint,
        )
    )
    armed = (
        gate_enabled
        and bool(permission and permission[0])
        and exact_permission
        and len(arms) == 5
        and observed == expected
    )
    reasons: list[str] = []
    if gate_enabled and not armed:
        reasons.append("MAINNET_GATE_NOT_CLOSED")
    if all_permissions and not armed:
        reasons.append("EXECUTION_PERMISSION_ALREADY_ENABLED")
    if arms and not armed:
        reasons.append("ACTIVE_OR_LEGACY_ARM_SESSION_REQUIRES_REVIEW")

    scopes: list[dict[str, object]] = []
    failed: set[str] = set()
    for context in contexts:
        decision = evaluate_live_arm(
            connection, context=context, now=current, require_owner_approval=False
        )
        failed.update(decision.failed_codes)
        scopes.append({
            "symbol": context.symbol,
            "pre_owner_ready": decision.ready,
            "failed_checks": list(decision.failed_codes),
        })
    if failed:
        reasons.extend(sorted(failed))

    technical_ready = not reasons
    # Strategy identity occurs ONCE; the five slots are separate scopes.
    return {
        **base,
        "installed": True,
        "binding_verified": True,
        "trading_account_id": account,
        "technical_pre_owner_ready": technical_ready,
        "ready_to_arm": technical_ready and not armed,
        "owner_approval_required": not armed,
        "armed": armed,
        "reasons": [
            *reasons,
            *(["EXPLICIT_OWNER_ARM_NOT_GRANTED"] if not armed else []),
        ],
        "strategies": [{
            "strategy_id": contexts[0].strategy_id,
            "strategy_version": contexts[0].strategy_version,
            "strategy_activation_id": contexts[0].strategy_activation_id,
            "symbols": [item.symbol for item in contexts],
            "directional_monitor_lanes": 10,
            "execution_permission": bool(permission and permission[0]),
            "pre_owner_ready": technical_ready,
            "failed_checks": sorted(failed),
        }],
        "instrument_scopes": scopes,
    }
