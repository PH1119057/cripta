from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class EarlyLossContext:
    hold_status: str
    supervisor_state: str
    structural_event: str | None
    hold_observed_at: datetime | None
    structure_observed_at: datetime | None
    decided_at: datetime
    max_age_seconds: int = 90


def early_loss_eligible(context: EarlyLossContext) -> bool:
    """Единственная разрешённая в V1.1 причинная комбинация раннего выхода."""
    if (
        context.hold_status != "INCOMPATIBLE"
        or context.supervisor_state != "BROKEN"
        or context.structural_event != "protective_clean_break_against"
        or context.hold_observed_at is None
        or context.structure_observed_at is None
    ):
        return False
    for observed_at in (context.hold_observed_at, context.structure_observed_at):
        age = (context.decided_at - observed_at).total_seconds()
        if age < 0 or age > context.max_age_seconds:
            return False
    return True
