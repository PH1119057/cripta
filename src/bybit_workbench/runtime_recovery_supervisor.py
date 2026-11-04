"""Pure, fail-closed durable planning for the Runtime Recovery Supervisor.

The planner never performs I/O, issues trading rights, or touches the gate.
A privileged restart is a separate, explicitly authorized broker operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .runtime_recovery_policy import (
    RecoveryAction,
    RecoveryClass,
    ServiceRecoveryObservation,
    ServiceRecoveryPolicy,
    decide_recovery,
)


class OperationStage(StrEnum):
    IDLE = "IDLE"
    PENDING = "PENDING"
    AWAITING_PROOF = "AWAITING_PROOF"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    unit: str
    release_commit: str
    attempts: int = 0
    stage: OperationStage = OperationStage.IDLE
    last_attempt_epoch: int = 0
    request_id: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RecoveryEvidence:
    scope_verified: bool
    exit_continuity_verified: bool
    reconciliation_path_verified: bool = False


@dataclass(frozen=True, slots=True)
class SupervisorPlan:
    action: RecoveryAction
    dispatch: bool
    record: RecoveryRecord
    reason: str


def plan_recovery(
    *,
    policy: ServiceRecoveryPolicy,
    observation: ServiceRecoveryObservation,
    prior: RecoveryRecord,
    evidence: RecoveryEvidence,
    now_epoch: int,
    execution_enabled: bool,
) -> SupervisorPlan:
    """Plan at most one attempt; do not retry uncertain/in-flight operations."""

    def block(reason: str) -> SupervisorPlan:
        return SupervisorPlan(RecoveryAction.BLOCKED, False, prior, reason)

    if prior.unit != policy.unit or observation.unit != policy.unit:
        return block("RECOVERY_IDENTITY_MISMATCH")
    if (
        not observation.expected_commit
        or prior.release_commit != observation.expected_commit
        or observation.loaded_commit != observation.expected_commit
    ):
        return block("RECOVERY_RELEASE_MISMATCH")
    if now_epoch <= 0:
        return block("RECOVERY_CLOCK_UNVERIFIED")

    healthy = (
        observation.unit_state.value == "active"
        and observation.behavior_ready is True
    )
    if prior.stage in {OperationStage.PENDING, OperationStage.AWAITING_PROOF}:
        if healthy and evidence.scope_verified and evidence.exit_continuity_verified:
            if (
                policy.recovery_class is RecoveryClass.STATEFUL_RECONCILE_RESTART
                and not evidence.reconciliation_path_verified
            ):
                return block("POST_RESTART_RECONCILIATION_UNPROVEN")
            complete = RecoveryRecord(
                unit=prior.unit,
                release_commit=prior.release_commit,
                attempts=prior.attempts,
                stage=OperationStage.VERIFIED,
                last_attempt_epoch=prior.last_attempt_epoch,
                request_id=prior.request_id,
                reason="POST_RESTART_BEHAVIOR_VERIFIED",
            )
            return SupervisorPlan(RecoveryAction.NONE, False, complete, complete.reason)
        return block("PENDING_OPERATION_UNCERTAIN_NO_AUTO_RETRY")

    raw = decide_recovery(policy, observation)
    if raw.action is RecoveryAction.NONE:
        return SupervisorPlan(RecoveryAction.NONE, False, prior, "HEALTHY")
    if raw.action is RecoveryAction.BLOCKED:
        return block(raw.reason.value)
    if not evidence.scope_verified:
        return block("RECOVERY_SCOPE_UNVERIFIED")
    if not evidence.exit_continuity_verified:
        return block("EXIT_CONTINUITY_UNVERIFIED")
    if (
        policy.recovery_class is RecoveryClass.STATEFUL_RECONCILE_RESTART
        and not evidence.reconciliation_path_verified
    ):
        return block("RECONCILIATION_PATH_UNVERIFIED")
    if prior.attempts >= policy.max_attempts:
        return block("RECOVERY_BUDGET_EXHAUSTED")
    if (
        prior.last_attempt_epoch > 0
        and now_epoch - prior.last_attempt_epoch < policy.cooldown_seconds
    ):
        return block("RECOVERY_COOLDOWN")
    if not execution_enabled:
        return SupervisorPlan(raw.action, False, prior, "OBSERVE_ONLY")
    pending = RecoveryRecord(
        unit=policy.unit,
        release_commit=observation.expected_commit,
        attempts=prior.attempts + 1,
        stage=OperationStage.PENDING,
        last_attempt_epoch=now_epoch,
        request_id=(
            f"recovery-{now_epoch}-{prior.attempts + 1}-"
            f"{policy.unit.replace('.', '-').replace('@', '-')}"
        ),
        reason="BROKER_OPERATION_PENDING",
    )
    return SupervisorPlan(raw.action, True, pending, "PERSIST_BEFORE_BROKER")


def broker_accepted(record: RecoveryRecord) -> RecoveryRecord:
    """A successful systemd action is not evidence of behavior readiness."""
    if record.stage is not OperationStage.PENDING:
        raise ValueError("RECOVERY_OPERATION_NOT_PENDING")
    return RecoveryRecord(
        unit=record.unit,
        release_commit=record.release_commit,
        attempts=record.attempts,
        stage=OperationStage.AWAITING_PROOF,
        last_attempt_epoch=record.last_attempt_epoch,
        request_id=record.request_id,
        reason="POST_RESTART_PROOF_REQUIRED",
    )


def broker_outcome_unknown(record: RecoveryRecord) -> RecoveryRecord:
    """Never replay after timeout/crash/unknown broker outcome."""
    if record.stage is not OperationStage.PENDING:
        raise ValueError("RECOVERY_OPERATION_NOT_PENDING")
    return RecoveryRecord(
        unit=record.unit,
        release_commit=record.release_commit,
        attempts=record.attempts,
        stage=OperationStage.BLOCKED,
        last_attempt_epoch=record.last_attempt_epoch,
        request_id=record.request_id,
        reason="BROKER_OUTCOME_UNKNOWN_MANUAL_RECONCILIATION",
    )
