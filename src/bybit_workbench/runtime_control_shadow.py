"""Pure mapping from legacy P90 facts to Stable Control Core shadow state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .runtime_control_core import ControlState, TechnicalReadiness


class ShadowClassification(StrEnum):
    """Meaning of one read-only legacy/Core comparison."""

    PARITY_ALLOW = "PARITY_ALLOW"
    PARITY_BLOCK = "PARITY_BLOCK"
    LEGACY_STUCK_AFTER_RECOVERY = "LEGACY_STUCK_AFTER_RECOVERY"
    LEGACY_OPEN_WHILE_CORE_BLOCKS = "LEGACY_OPEN_WHILE_CORE_BLOCKS"
    TRANSITIONAL_P90_STAGING = "TRANSITIONAL_P90_STAGING"
    NON_P90_CONTROL_SCOPE = "NON_P90_CONTROL_SCOPE"


@dataclass(frozen=True, slots=True)
class LegacyP90Snapshot:
    """Normalized read-only facts from the current production control surfaces."""

    gate_open: bool
    gate_reason: str
    owner_scope_exact: bool
    strategy_authorized_exact: bool
    release_scope_exact: bool
    observer_ready: bool
    consumers_ready: bool
    hard_safety_block: bool
    p90_staging: bool = False
    non_p90_control_scope: bool = False


@dataclass(frozen=True, slots=True)
class ShadowEvaluation:
    """Stable Core decision compared with the current legacy effective gate."""

    classification: ShadowClassification
    core_state: ControlState
    core_effective_new_entry: bool
    legacy_effective_new_entry: bool


def evaluate_p90_shadow(snapshot: LegacyP90Snapshot) -> ShadowEvaluation:
    """Map one legacy P90 snapshot into Stable Core without mutating anything."""

    technical = (
        TechnicalReadiness.READY
        if snapshot.observer_ready and snapshot.consumers_ready
        else TechnicalReadiness.BLOCKED
    )
    core_state = ControlState(
        owner_authorized=snapshot.owner_scope_exact,
        strategy_authorized=snapshot.strategy_authorized_exact,
        technical_readiness=technical,
        exact_scope=snapshot.release_scope_exact,
        hard_safety_block=snapshot.hard_safety_block,
    )
    core_effective = core_state.effective_new_entry
    legacy_effective = (
        snapshot.gate_open
        and snapshot.owner_scope_exact
        and snapshot.strategy_authorized_exact
        and snapshot.release_scope_exact
    )

    if snapshot.non_p90_control_scope:
        classification = ShadowClassification.NON_P90_CONTROL_SCOPE
    elif snapshot.p90_staging:
        classification = ShadowClassification.TRANSITIONAL_P90_STAGING
    elif core_effective and legacy_effective:
        classification = ShadowClassification.PARITY_ALLOW
    elif not core_effective and not legacy_effective:
        classification = ShadowClassification.PARITY_BLOCK
    elif core_effective:
        classification = ShadowClassification.LEGACY_STUCK_AFTER_RECOVERY
    else:
        classification = ShadowClassification.LEGACY_OPEN_WHILE_CORE_BLOCKS

    return ShadowEvaluation(
        classification=classification,
        core_state=core_state,
        core_effective_new_entry=core_effective,
        legacy_effective_new_entry=legacy_effective,
    )
