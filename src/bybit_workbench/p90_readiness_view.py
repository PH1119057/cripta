"""Read-only prerequisites for displaying/enforcing the separate OWNER ARM action."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime


def observer_arm_ready(payload: Mapping[str, object], release: str, now: datetime) -> bool:
    try:
        at = datetime.fromisoformat(str(payload.get("updated_at") or ""))
        return (
            at.tzinfo is not None
            and 0 <= (now - at.astimezone(UTC)).total_seconds() <= 10
            and payload.get("state") == "RUNNING"
            and payload.get("observer_ready") is True
            and payload.get("runtime_mode") == "MULTI_STRATEGY_OBSERVER"
            and payload.get("source_commit") == release
        )
    except (TypeError, ValueError):
        return False


def technical_preparation_fresh(
    operation: Mapping[str, object] | None, release: str, now: datetime,
) -> bool:
    if not operation:
        return False
    try:
        value = operation.get("completed_at")
        at = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        return (
            operation.get("state") == "READY_FOR_OWNER_ARM"
            and operation.get("release_commit") == release
            and at.tzinfo is not None
            and 0 <= (now - at.astimezone(UTC)).total_seconds() <= 90
        )
    except (TypeError, ValueError):
        return False
