"""Pure Stable Control Core v1 transition table."""

from __future__ import annotations

from dataclasses import replace

from .model import (
    ControlAction,
    ControlEvent,
    ControlState,
    TechnicalReadiness,
    TransitionDecision,
    TransitionReason,
)


def _derived_action(before: ControlState, after: ControlState) -> ControlAction:
    if before.effective_new_entry == after.effective_new_entry:
        return ControlAction.NO_CHANGE
    if after.effective_new_entry:
        return ControlAction.ALLOW_NEW_ENTRY
    return ControlAction.BLOCK_NEW_ENTRY


def _accepted(
    before: ControlState,
    after: ControlState,
    *,
    reason: TransitionReason = TransitionReason.APPLIED,
) -> TransitionDecision:
    changed = before != after
    return TransitionDecision(
        state=after,
        accepted=True,
        changed=changed,
        action=_derived_action(before, after),
        reason=reason if changed else TransitionReason.NO_CHANGE,
    )


def _rejected(
    state: ControlState,
    *,
    reason: TransitionReason,
) -> TransitionDecision:
    return TransitionDecision(
        state=state,
        accepted=False,
        changed=False,
        action=ControlAction.NO_CHANGE,
        reason=reason,
    )


def transition(state: ControlState, event: ControlEvent) -> TransitionDecision:
    """Apply one deterministic control event without any external side effect."""

    if event is ControlEvent.OWNER_ARM:
        if state.owner_authorized:
            return _accepted(state, state)
        if not state.exact_scope:
            return _rejected(
                state,
                reason=TransitionReason.OWNER_ARM_REQUIRES_EXACT_SCOPE,
            )
        if not state.strategy_authorized:
            return _rejected(
                state,
                reason=TransitionReason.OWNER_ARM_REQUIRES_STRATEGY_AUTHORIZED,
            )
        if state.technical_readiness is not TechnicalReadiness.READY:
            return _rejected(
                state,
                reason=TransitionReason.OWNER_ARM_REQUIRES_TECHNICAL_READY,
            )
        if state.hard_safety_block:
            return _rejected(
                state,
                reason=TransitionReason.OWNER_ARM_HARD_SAFETY_BLOCKED,
            )
        return _accepted(state, replace(state, owner_authorized=True))

    if event is ControlEvent.OWNER_DISARM:
        return _accepted(state, replace(state, owner_authorized=False))

    if event is ControlEvent.STRATEGY_AUTHORIZE:
        return _accepted(state, replace(state, strategy_authorized=True))

    if event is ControlEvent.STRATEGY_REVOKE:
        return _accepted(state, replace(state, strategy_authorized=False))

    if event is ControlEvent.TECHNICAL_READY:
        return _accepted(
            state,
            replace(state, technical_readiness=TechnicalReadiness.READY),
        )

    if event is ControlEvent.TECHNICAL_BLOCK:
        return _accepted(
            state,
            replace(state, technical_readiness=TechnicalReadiness.BLOCKED),
        )

    if event is ControlEvent.TECHNICAL_RECOVERING:
        return _accepted(
            state,
            replace(state, technical_readiness=TechnicalReadiness.RECOVERING),
        )

    if event is ControlEvent.SCOPE_VERIFY:
        return _accepted(state, replace(state, exact_scope=True))

    if event is ControlEvent.SCOPE_INVALIDATE:
        return _accepted(
            state,
            replace(state, exact_scope=False, owner_authorized=False),
        )

    if event is ControlEvent.HARD_BLOCK_SET:
        return _accepted(state, replace(state, hard_safety_block=True))

    if event is ControlEvent.HARD_BLOCK_CLEAR:
        return _accepted(state, replace(state, hard_safety_block=False))

    raise AssertionError(f"unsupported ControlEvent: {event!r}")
