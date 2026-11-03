"""Immutable types for Stable Control Core v1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

CORE_CONTRACT_VERSION = "stable-control-core-v1"


class TechnicalReadiness(StrEnum):
    """Machine-owned readiness for NEW REAL Entry."""

    READY = "READY"
    BLOCKED = "BLOCKED"
    RECOVERING = "RECOVERING"


class ControlEvent(StrEnum):
    """Typed inputs accepted by the pure transition function."""

    OWNER_ARM = "OWNER_ARM"
    OWNER_DISARM = "OWNER_DISARM"
    STRATEGY_AUTHORIZE = "STRATEGY_AUTHORIZE"
    STRATEGY_REVOKE = "STRATEGY_REVOKE"
    TECHNICAL_READY = "TECHNICAL_READY"
    TECHNICAL_BLOCK = "TECHNICAL_BLOCK"
    TECHNICAL_RECOVERING = "TECHNICAL_RECOVERING"
    SCOPE_VERIFY = "SCOPE_VERIFY"
    SCOPE_INVALIDATE = "SCOPE_INVALIDATE"
    HARD_BLOCK_SET = "HARD_BLOCK_SET"
    HARD_BLOCK_CLEAR = "HARD_BLOCK_CLEAR"


class ControlAction(StrEnum):
    """Derived NEW Entry intent; an adapter may apply it later."""

    NO_CHANGE = "NO_CHANGE"
    ALLOW_NEW_ENTRY = "ALLOW_NEW_ENTRY"
    BLOCK_NEW_ENTRY = "BLOCK_NEW_ENTRY"


class TransitionReason(StrEnum):
    """Stable result/rejection codes for one Core transition."""

    APPLIED = "APPLIED"
    NO_CHANGE = "NO_CHANGE"
    OWNER_ARM_REQUIRES_EXACT_SCOPE = "OWNER_ARM_REQUIRES_EXACT_SCOPE"
    OWNER_ARM_REQUIRES_STRATEGY_AUTHORIZED = (
        "OWNER_ARM_REQUIRES_STRATEGY_AUTHORIZED"
    )
    OWNER_ARM_REQUIRES_TECHNICAL_READY = "OWNER_ARM_REQUIRES_TECHNICAL_READY"
    OWNER_ARM_HARD_SAFETY_BLOCKED = "OWNER_ARM_HARD_SAFETY_BLOCKED"


@dataclass(frozen=True, slots=True)
class ControlState:
    """Minimal stable state from which effective NEW Entry is derived."""

    owner_authorized: bool = False
    strategy_authorized: bool = False
    technical_readiness: TechnicalReadiness = TechnicalReadiness.BLOCKED
    exact_scope: bool = False
    hard_safety_block: bool = False

    @property
    def effective_new_entry(self) -> bool:
        return (
            self.owner_authorized
            and self.strategy_authorized
            and self.technical_readiness is TechnicalReadiness.READY
            and self.exact_scope
            and not self.hard_safety_block
        )


@dataclass(frozen=True, slots=True)
class TransitionDecision:
    """Pure result of applying one event to one immutable state."""

    state: ControlState
    accepted: bool
    changed: bool
    action: ControlAction
    reason: TransitionReason
