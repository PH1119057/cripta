from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bybit_workbench.real_service_profiles import p90_real_v3_service_specs
from bybit_workbench.runtime_recovery_policy import (
    RECOVERY_POLICY_VERSION,
    RecoveryAction,
    RecoveryClass,
    RecoveryReason,
    ServiceRecoveryObservation,
    ServiceRecoveryPolicy,
    UnitState,
    decide_recovery,
    p90_real_v3_recovery_policies,
)

SUB = "ta-853ac5fcd58185697396d33cd57459fa"
RELEASE = "a" * 40


def policy(
    recovery_class: RecoveryClass = RecoveryClass.SAFE_RESTART,
) -> ServiceRecoveryPolicy:
    return ServiceRecoveryPolicy(
        unit="cripta-test.service",
        recovery_class=recovery_class,
        max_attempts=2,
        cooldown_seconds=15,
    )


def observation(
    *,
    state: UnitState,
    ready: bool | None = False,
    attempts: int = 0,
) -> ServiceRecoveryObservation:
    return ServiceRecoveryObservation(
        unit="cripta-test.service",
        unit_state=state,
        behavior_ready=ready,
        loaded_commit=RELEASE,
        expected_commit=RELEASE,
        attempts_used=attempts,
    )


def test_contract_version_is_explicit() -> None:
    assert RECOVERY_POLICY_VERSION == "runtime-recovery-policy-v1"


@pytest.mark.parametrize(
    ("state", "ready", "expected"),
    [
        (UnitState.ACTIVE, True, RecoveryAction.NONE),
        (UnitState.INACTIVE, False, RecoveryAction.START),
        (UnitState.FAILED, False, RecoveryAction.RESTART),
        (UnitState.ACTIVE, False, RecoveryAction.RESTART),
    ],
)
def test_safe_restart_matrix(
    state: UnitState,
    ready: bool,
    expected: RecoveryAction,
) -> None:
    decision = decide_recovery(policy(), observation(state=state, ready=ready))
    assert decision.action is expected
    assert decision.reconciliation_required is False


@pytest.mark.parametrize(
    ("state", "ready", "expected"),
    [
        (UnitState.ACTIVE, True, RecoveryAction.NONE),
        (UnitState.INACTIVE, False, RecoveryAction.START_THEN_RECONCILE),
        (UnitState.FAILED, False, RecoveryAction.RESTART_THEN_RECONCILE),
        (UnitState.ACTIVE, False, RecoveryAction.RESTART_THEN_RECONCILE),
    ],
)
def test_stateful_recovery_is_always_reconciliation_gated(
    state: UnitState,
    ready: bool,
    expected: RecoveryAction,
) -> None:
    decision = decide_recovery(
        policy(RecoveryClass.STATEFUL_RECONCILE_RESTART),
        observation(state=state, ready=ready),
    )
    assert decision.action is expected
    assert decision.reconciliation_required is (expected is not RecoveryAction.NONE)


def test_active_unknown_behavior_blocks_instead_of_guessing() -> None:
    decision = decide_recovery(
        policy(),
        observation(state=UnitState.ACTIVE, ready=None),
    )
    assert decision.action is RecoveryAction.BLOCKED
    assert decision.reason is RecoveryReason.BEHAVIOR_STATE_UNKNOWN


@pytest.mark.parametrize(
    "state",
    [UnitState.ACTIVATING, UnitState.DEACTIVATING, UnitState.UNKNOWN],
)
def test_transitional_or_unknown_os_state_blocks(state: UnitState) -> None:
    decision = decide_recovery(policy(), observation(state=state))
    assert decision.action is RecoveryAction.BLOCKED
    assert decision.reason is RecoveryReason.OS_STATE_NOT_RECOVERABLE


def test_attempt_budget_is_finite() -> None:
    decision = decide_recovery(
        policy(),
        observation(state=UnitState.FAILED, attempts=2),
    )
    assert decision.action is RecoveryAction.BLOCKED
    assert decision.reason is RecoveryReason.ATTEMPT_BUDGET_EXHAUSTED


def test_healthy_service_does_not_fail_only_because_old_attempt_budget_is_full() -> None:
    decision = decide_recovery(
        policy(),
        observation(state=UnitState.ACTIVE, ready=True, attempts=2),
    )
    assert decision.action is RecoveryAction.NONE
    assert decision.reason is RecoveryReason.HEALTHY


def test_release_mismatch_blocks_before_recovery() -> None:
    item = observation(state=UnitState.FAILED)
    item = ServiceRecoveryObservation(
        unit=item.unit,
        unit_state=item.unit_state,
        behavior_ready=item.behavior_ready,
        loaded_commit="b" * 40,
        expected_commit=item.expected_commit,
    )
    decision = decide_recovery(policy(), item)
    assert decision.action is RecoveryAction.BLOCKED
    assert decision.reason is RecoveryReason.RELEASE_IDENTITY_MISMATCH


def test_unit_identity_mismatch_blocks() -> None:
    item = observation(state=UnitState.FAILED)
    item = ServiceRecoveryObservation(
        unit="cripta-other.service",
        unit_state=item.unit_state,
        behavior_ready=item.behavior_ready,
        loaded_commit=item.loaded_commit,
        expected_commit=item.expected_commit,
    )
    decision = decide_recovery(policy(), item)
    assert decision.action is RecoveryAction.BLOCKED
    assert decision.reason is RecoveryReason.UNIT_IDENTITY_MISMATCH


def test_manual_only_never_synthesizes_recovery_action() -> None:
    decision = decide_recovery(
        policy(RecoveryClass.MANUAL_ONLY),
        observation(state=UnitState.FAILED),
    )
    assert decision.action is RecoveryAction.BLOCKED
    assert decision.reason is RecoveryReason.MANUAL_ONLY


def test_p90_profile_has_exact_nine_unit_coverage_and_no_extras() -> None:
    specs = p90_real_v3_service_specs(sub_account_id=SUB)
    policies = p90_real_v3_recovery_policies(sub_account_id=SUB)
    assert len(specs) == 9
    assert {item.unit for item in policies} == {item.unit for item in specs}
    assert len({item.unit for item in policies}) == 9


def test_p90_private_runtime_is_stateful_and_every_other_required_unit_is_safe() -> None:
    policies = p90_real_v3_recovery_policies(sub_account_id=SUB)
    stateful = {
        item.unit
        for item in policies
        if item.recovery_class is RecoveryClass.STATEFUL_RECONCILE_RESTART
    }
    assert stateful == {
        "cripta-private-runtime.service",
        f"cripta-private-runtime@{SUB}.service",
    }
    assert all(
        item.recovery_class is RecoveryClass.SAFE_RESTART
        for item in policies
        if item.unit not in stateful
    )


def test_policy_module_is_pure() -> None:
    path = Path("src/bybit_workbench/runtime_recovery_policy.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden_roots = {
        "asyncio",
        "httpx",
        "os",
        "pathlib",
        "psycopg",
        "requests",
        "socket",
        "subprocess",
        "time",
        "urllib",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".")[0] for alias in node.names}
            assert not (roots & forbidden_roots)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            assert node.module.split(".")[0] not in forbidden_roots


def test_no_production_module_imports_recovery_policy_yet() -> None:
    allowed = Path("src/bybit_workbench/runtime_recovery_policy.py")
    offenders: list[str] = []
    for root in (Path("src/bybit_workbench"), Path("operations")):
        for path in root.rglob("*.py"):
            if path == allowed:
                continue
            if "runtime_recovery_policy" in path.read_text(encoding="utf-8"):
                offenders.append(path.as_posix())
    assert offenders == []
