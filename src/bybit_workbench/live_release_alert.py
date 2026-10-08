"""Pure read-only release/arm admission alert projection for the operator UI.

Do not confuse this projection with the authoritative pre-Entry gate or a
durable CRITICAL event. Those are separate runtime control-plane responsibilities.
"""
from __future__ import annotations

from collections.abc import Sequence


def project_release_arm_health(
    *,
    loaded_commit: str,
    gate_open: bool,
    sessions: Sequence[tuple[str, str, str]],
    recent_not_arm_ready: int,
    last_not_arm_ready_at: str | None,
) -> dict[str, object]:
    mismatched = [
        {
            "strategy_id": strategy_id,
            "symbol": symbol,
            "session_release_commit": release,
        }
        for strategy_id, symbol, release in sessions
        if not loaded_commit or loaded_commit != release
    ]
    return {
        "loaded_commit": loaded_commit,
        "gate_open": gate_open,
        "active_sessions": len(sessions),
        "mismatched_sessions": mismatched,
        "recent_not_arm_ready": recent_not_arm_ready,
        "last_not_arm_ready_at": last_not_arm_ready_at,
        "critical": bool(
            gate_open and (mismatched or recent_not_arm_ready > 0 or not loaded_commit)
        ),
    }
