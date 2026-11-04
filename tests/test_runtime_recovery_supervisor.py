from __future__ import annotations

from dataclasses import replace
from pathlib import Path


from bybit_workbench.runtime_recovery_policy import (
    RecoveryAction,
    RecoveryClass,
    ServiceRecoveryObservation,
    ServiceRecoveryPolicy,
    UnitState,
)
from bybit_workbench.runtime_recovery_supervisor import (
    OperationStage,
    RecoveryEvidence,
    RecoveryRecord,
    broker_accepted,
    broker_outcome_unknown,
    plan_recovery,
)

UNIT = "cripta-universal-entry-observer.service"
RELEASE = "a" * 40
POLICY = ServiceRecoveryPolicy(
    unit=UNIT,
    recovery_class=RecoveryClass.SAFE_RESTART,
    max_attempts=2,
    cooldown_seconds=15,
)
VERIFIED = RecoveryEvidence(
    scope_verified=True, exit_continuity_verified=True,
)


def obs(*, ready: bool | None = False, state: UnitState = UnitState.ACTIVE):
    return ServiceRecoveryObservation(
        unit=UNIT, unit_state=state, behavior_ready=ready,
        loaded_commit=RELEASE, expected_commit=RELEASE,
    )


def record() -> RecoveryRecord:
    return RecoveryRecord(unit=UNIT, release_commit=RELEASE)


def plan(**overrides: object):
    args: dict[str, object] = dict(
        policy=POLICY, observation=obs(), prior=record(),
        evidence=VERIFIED, now_epoch=1000, execution_enabled=True,
    )
    args.update(overrides)
    return plan_recovery(**args)  # type: ignore[arg-type]


def test_healthy_unit_never_restarts() -> None:
    result = plan(observation=obs(ready=True))
    assert result.action is RecoveryAction.NONE
    assert not result.dispatch


def test_persist_pending_before_broker() -> None:
    result = plan()
    assert result.dispatch
    assert result.action is RecoveryAction.RESTART
    assert result.record.stage is OperationStage.PENDING
    assert result.record.attempts == 1
    assert result.reason == "PERSIST_BEFORE_BROKER"


def test_observe_only_never_dispatches() -> None:
    result = plan(execution_enabled=False)
    assert not result.dispatch
    assert result.record.stage is OperationStage.IDLE


def test_no_exit_proof_means_no_restart() -> None:
    no_exit = RecoveryEvidence(scope_verified=True, exit_continuity_verified=False)
    result = plan(evidence=no_exit)
    assert result.action is RecoveryAction.BLOCKED
    assert result.reason == "EXIT_CONTINUITY_UNVERIFIED"


def test_crashed_inflight_operation_never_auto_retries() -> None:
    pending = plan().record
    result = plan(prior=pending)
    assert not result.dispatch
    assert result.reason == "PENDING_OPERATION_UNCERTAIN_NO_AUTO_RETRY"


def test_broker_ack_is_not_behavior_proof() -> None:
    pending = plan().record
    accepted = broker_accepted(pending)
    assert accepted.stage is OperationStage.AWAITING_PROOF
    result = plan(prior=accepted)
    assert not result.dispatch
    assert result.reason == "PENDING_OPERATION_UNCERTAIN_NO_AUTO_RETRY"


def test_recovered_evidence_completes_without_replay() -> None:
    accepted = broker_accepted(plan().record)
    result = plan(prior=accepted, observation=obs(ready=True))
    assert result.record.stage is OperationStage.VERIFIED
    assert result.action is RecoveryAction.NONE


def test_uncertain_broker_outcome_is_terminal() -> None:
    failed = broker_outcome_unknown(plan().record)
    assert failed.stage is OperationStage.BLOCKED
    assert not plan(prior=failed, now_epoch=2000).dispatch


def test_release_identity_change_blocks() -> None:
    result = plan(prior=replace(record(), release_commit="b" * 40))
    assert result.action is RecoveryAction.BLOCKED
    assert result.reason == "RECOVERY_RELEASE_MISMATCH"


def test_finite_attempt_budget_and_cooldown() -> None:
    previous = replace(record(), attempts=2)
    assert plan(prior=previous).reason == "ATTEMPT_BUDGET_EXHAUSTED"
    previous = replace(record(), attempts=1, last_attempt_epoch=995)
    assert plan(prior=previous).reason == "RECOVERY_COOLDOWN"


def test_stateful_unit_requires_reconciliation_path() -> None:
    stateful = replace(
        POLICY, recovery_class=RecoveryClass.STATEFUL_RECONCILE_RESTART
    )
    result = plan(policy=stateful)
    assert result.reason == "RECONCILIATION_PATH_UNVERIFIED"
    assert not result.dispatch


def test_supervisor_is_inert_and_does_not_use_broker_client() -> None:
    source = Path(
        "operations/monitoring/runtime_recovery_supervisor.py"
    ).read_text(encoding="utf-8")
    unit = Path(
        "operations/systemd/cripta-runtime-recovery-supervisor.service"
    ).read_text(encoding="utf-8")
    installer = Path(
        "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    assert "execution_enabled=False" in source
    assert "RECOVERY_EXECUTION_NOT_WIRED" in source
    assert "exit_continuity_verified=False" in source
    assert "request_recovery(" not in source
    assert "StateDirectory=cripta-runtime-recovery" in unit
    assert "Environment=CRIPTA_RUNTIME_RECOVERY_MODE=OBSERVE_ONLY" in unit
    assert "[Install]" not in unit.splitlines()
    assert "cripta-runtime-recovery-supervisor" not in installer


def test_supervisor_probes_only_and_keeps_engine_unmodified() -> None:
    path = Path("operations/monitoring/runtime_recovery_supervisor.py")
    source = path.read_text(encoding="utf-8")
    forbidden = (
        "execution_gates", "live_arm_sessions", "account_live_arm_sessions",
        "UPDATE ", "DELETE FROM", "INSERT INTO", "/v5/order/",
        '"restart"', "request_recovery(",
    )
    assert not any(token in source for token in forbidden)
