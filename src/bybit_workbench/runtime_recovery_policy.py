"""Pure bounded runtime-recovery planning for CRIPTA services.

No I/O lives here.  The module classifies recovery intent only; privileged
execution and post-restart evidence belong to separate adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .real_service_profiles import p90_real_v3_service_specs

RECOVERY_POLICY_VERSION = "runtime-recovery-policy-v1"


class RecoveryClass(StrEnum):
    SAFE_RESTART = "SAFE_RESTART"
    STATEFUL_RECONCILE_RESTART = "STATEFUL_RECONCILE_RESTART"
    MANUAL_ONLY = "MANUAL_ONLY"


class UnitState(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"
    ACTIVATING = "activating"
    DEACTIVATING = "deactivating"
    UNKNOWN = "unknown"


class RecoveryAction(StrEnum):
    NONE = "NONE"
    START = "START"
    RESTART = "RESTART"
    START_THEN_RECONCILE = "START_THEN_RECONCILE"
    RESTART_THEN_RECONCILE = "RESTART_THEN_RECONCILE"
    BLOCKED = "BLOCKED"


class RecoveryReason(StrEnum):
    HEALTHY = "HEALTHY"
    START_REQUIRED = "START_REQUIRED"
    RESTART_REQUIRED = "RESTART_REQUIRED"
    STATEFUL_START_REQUIRES_RECONCILIATION = (
        "STATEFUL_START_REQUIRES_RECONCILIATION"
    )
    STATEFUL_RESTART_REQUIRES_RECONCILIATION = (
        "STATEFUL_RESTART_REQUIRES_RECONCILIATION"
    )
    UNIT_IDENTITY_MISMATCH = "UNIT_IDENTITY_MISMATCH"
    RELEASE_IDENTITY_MISMATCH = "RELEASE_IDENTITY_MISMATCH"
    BEHAVIOR_STATE_UNKNOWN = "BEHAVIOR_STATE_UNKNOWN"
    OS_STATE_NOT_RECOVERABLE = "OS_STATE_NOT_RECOVERABLE"
    ATTEMPT_BUDGET_EXHAUSTED = "ATTEMPT_BUDGET_EXHAUSTED"
    MANUAL_ONLY = "MANUAL_ONLY"


@dataclass(frozen=True, slots=True)
class ServiceRecoveryPolicy:
    unit: str
    recovery_class: RecoveryClass
    max_attempts: int
    cooldown_seconds: int

    def __post_init__(self) -> None:
        if not self.unit:
            raise ValueError("RECOVERY_POLICY_UNIT_REQUIRED")
        if self.max_attempts < 1:
            raise ValueError("RECOVERY_POLICY_ATTEMPTS_INVALID")
        if self.cooldown_seconds < 1:
            raise ValueError("RECOVERY_POLICY_COOLDOWN_INVALID")


@dataclass(frozen=True, slots=True)
class ServiceRecoveryObservation:
    unit: str
    unit_state: UnitState
    behavior_ready: bool | None
    loaded_commit: str
    expected_commit: str
    attempts_used: int = 0


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    action: RecoveryAction
    reason: RecoveryReason
    reconciliation_required: bool


def decide_recovery(
    policy: ServiceRecoveryPolicy,
    observation: ServiceRecoveryObservation,
) -> RecoveryDecision:
    """Return one fail-closed recovery intent without side effects."""

    if observation.unit != policy.unit:
        return RecoveryDecision(
            RecoveryAction.BLOCKED,
            RecoveryReason.UNIT_IDENTITY_MISMATCH,
            False,
        )
    if (
        not observation.expected_commit
        or observation.loaded_commit != observation.expected_commit
    ):
        return RecoveryDecision(
            RecoveryAction.BLOCKED,
            RecoveryReason.RELEASE_IDENTITY_MISMATCH,
            False,
        )

    if (
        observation.unit_state is UnitState.ACTIVE
        and observation.behavior_ready is True
    ):
        return RecoveryDecision(
            RecoveryAction.NONE,
            RecoveryReason.HEALTHY,
            False,
        )

    if policy.recovery_class is RecoveryClass.MANUAL_ONLY:
        return RecoveryDecision(
            RecoveryAction.BLOCKED,
            RecoveryReason.MANUAL_ONLY,
            False,
        )

    if observation.attempts_used >= policy.max_attempts:
        return RecoveryDecision(
            RecoveryAction.BLOCKED,
            RecoveryReason.ATTEMPT_BUDGET_EXHAUSTED,
            False,
        )

    if observation.unit_state in {
        UnitState.ACTIVATING,
        UnitState.DEACTIVATING,
        UnitState.UNKNOWN,
    }:
        return RecoveryDecision(
            RecoveryAction.BLOCKED,
            RecoveryReason.OS_STATE_NOT_RECOVERABLE,
            False,
        )

    if observation.unit_state is UnitState.ACTIVE:
        if observation.behavior_ready is None:
            return RecoveryDecision(
                RecoveryAction.BLOCKED,
                RecoveryReason.BEHAVIOR_STATE_UNKNOWN,
                False,
            )
        if policy.recovery_class is RecoveryClass.SAFE_RESTART:
            return RecoveryDecision(
                RecoveryAction.RESTART,
                RecoveryReason.RESTART_REQUIRED,
                False,
            )
        return RecoveryDecision(
            RecoveryAction.RESTART_THEN_RECONCILE,
            RecoveryReason.STATEFUL_RESTART_REQUIRES_RECONCILIATION,
            True,
        )

    if observation.unit_state is UnitState.INACTIVE:
        if policy.recovery_class is RecoveryClass.SAFE_RESTART:
            return RecoveryDecision(
                RecoveryAction.START,
                RecoveryReason.START_REQUIRED,
                False,
            )
        return RecoveryDecision(
            RecoveryAction.START_THEN_RECONCILE,
            RecoveryReason.STATEFUL_START_REQUIRES_RECONCILIATION,
            True,
        )

    if observation.unit_state is UnitState.FAILED:
        if policy.recovery_class is RecoveryClass.SAFE_RESTART:
            return RecoveryDecision(
                RecoveryAction.RESTART,
                RecoveryReason.RESTART_REQUIRED,
                False,
            )
        return RecoveryDecision(
            RecoveryAction.RESTART_THEN_RECONCILE,
            RecoveryReason.STATEFUL_RESTART_REQUIRES_RECONCILIATION,
            True,
        )

    return RecoveryDecision(
        RecoveryAction.BLOCKED,
        RecoveryReason.OS_STATE_NOT_RECOVERABLE,
        False,
    )


def p90_real_v3_recovery_policies(
    *,
    sub_account_id: str,
) -> tuple[ServiceRecoveryPolicy, ...]:
    """Recovery classification for the exact current nine-unit P90 profile."""

    policies: list[ServiceRecoveryPolicy] = []
    for spec in p90_real_v3_service_specs(sub_account_id=sub_account_id):
        recovery_class = (
            RecoveryClass.STATEFUL_RECONCILE_RESTART
            if spec.unit.startswith("cripta-private-runtime")
            else RecoveryClass.SAFE_RESTART
        )
        policies.append(
            ServiceRecoveryPolicy(
                unit=spec.unit,
                recovery_class=recovery_class,
                max_attempts=2,
                cooldown_seconds=15,
            )
        )
    return tuple(policies)
