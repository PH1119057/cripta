from __future__ import annotations

import ast
import itertools
from pathlib import Path

import pytest

from bybit_workbench.runtime_control_core import (
    CORE_CONTRACT_VERSION,
    ControlAction,
    ControlEvent,
    ControlState,
    TechnicalReadiness,
    TransitionReason,
    transition,
)


def ready_state(*, owner_authorized: bool = True) -> ControlState:
    return ControlState(
        owner_authorized=owner_authorized,
        strategy_authorized=True,
        technical_readiness=TechnicalReadiness.READY,
        exact_scope=True,
        hard_safety_block=False,
    )


@pytest.mark.parametrize(
    (
        "owner_authorized",
        "strategy_authorized",
        "technical_ready",
        "exact_scope",
        "hard_block",
    ),
    itertools.product((False, True), repeat=5),
)
def test_effective_new_entry_truth_table(
    owner_authorized: bool,
    strategy_authorized: bool,
    technical_ready: bool,
    exact_scope: bool,
    hard_block: bool,
) -> None:
    state = ControlState(
        owner_authorized=owner_authorized,
        strategy_authorized=strategy_authorized,
        technical_readiness=(
            TechnicalReadiness.READY
            if technical_ready
            else TechnicalReadiness.BLOCKED
        ),
        exact_scope=exact_scope,
        hard_safety_block=hard_block,
    )
    assert state.effective_new_entry is (
        owner_authorized
        and strategy_authorized
        and technical_ready
        and exact_scope
        and not hard_block
    )


def test_contract_version_is_explicit_and_stable() -> None:
    assert CORE_CONTRACT_VERSION == "stable-control-core-v1"


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (
            ControlState(
                strategy_authorized=True,
                technical_readiness=TechnicalReadiness.READY,
                exact_scope=False,
            ),
            TransitionReason.OWNER_ARM_REQUIRES_EXACT_SCOPE,
        ),
        (
            ControlState(
                strategy_authorized=False,
                technical_readiness=TechnicalReadiness.READY,
                exact_scope=True,
            ),
            TransitionReason.OWNER_ARM_REQUIRES_STRATEGY_AUTHORIZED,
        ),
        (
            ControlState(
                strategy_authorized=True,
                technical_readiness=TechnicalReadiness.BLOCKED,
                exact_scope=True,
            ),
            TransitionReason.OWNER_ARM_REQUIRES_TECHNICAL_READY,
        ),
        (
            ControlState(
                strategy_authorized=True,
                technical_readiness=TechnicalReadiness.READY,
                exact_scope=True,
                hard_safety_block=True,
            ),
            TransitionReason.OWNER_ARM_HARD_SAFETY_BLOCKED,
        ),
    ],
)
def test_owner_arm_is_fail_closed(
    state: ControlState,
    reason: TransitionReason,
) -> None:
    decision = transition(state, ControlEvent.OWNER_ARM)
    assert decision.accepted is False
    assert decision.changed is False
    assert decision.state == state
    assert decision.action is ControlAction.NO_CHANGE
    assert decision.reason is reason


def test_owner_arm_opens_only_derived_effective_entry() -> None:
    state = ready_state(owner_authorized=False)
    decision = transition(state, ControlEvent.OWNER_ARM)
    assert decision.accepted is True
    assert decision.state.owner_authorized is True
    assert decision.state.effective_new_entry is True
    assert decision.action is ControlAction.ALLOW_NEW_ENTRY


def test_transient_technical_failure_preserves_owner_authorization_and_resumes() -> None:
    state = ready_state()

    blocked = transition(state, ControlEvent.TECHNICAL_BLOCK)
    assert blocked.state.owner_authorized is True
    assert blocked.state.effective_new_entry is False
    assert blocked.action is ControlAction.BLOCK_NEW_ENTRY

    recovering = transition(blocked.state, ControlEvent.TECHNICAL_RECOVERING)
    assert recovering.state.owner_authorized is True
    assert recovering.state.effective_new_entry is False

    recovered = transition(recovering.state, ControlEvent.TECHNICAL_READY)
    assert recovered.state.owner_authorized is True
    assert recovered.state.effective_new_entry is True
    assert recovered.action is ControlAction.ALLOW_NEW_ENTRY


def test_owner_disarm_cannot_be_undone_by_technical_recovery() -> None:
    blocked = transition(ready_state(), ControlEvent.TECHNICAL_BLOCK).state
    disarmed = transition(blocked, ControlEvent.OWNER_DISARM)
    recovered = transition(disarmed.state, ControlEvent.TECHNICAL_READY)

    assert recovered.state.owner_authorized is False
    assert recovered.state.effective_new_entry is False
    assert recovered.action is ControlAction.NO_CHANGE


def test_scope_invalidation_revokes_owner_authorization_and_verify_does_not_restore_it() -> None:
    invalidated = transition(ready_state(), ControlEvent.SCOPE_INVALIDATE)
    assert invalidated.state.exact_scope is False
    assert invalidated.state.owner_authorized is False
    assert invalidated.action is ControlAction.BLOCK_NEW_ENTRY

    verified = transition(invalidated.state, ControlEvent.SCOPE_VERIFY)
    assert verified.state.exact_scope is True
    assert verified.state.owner_authorized is False
    assert verified.state.effective_new_entry is False


def test_hard_block_prevents_entry_without_destroying_owner_authorization() -> None:
    blocked = transition(ready_state(), ControlEvent.HARD_BLOCK_SET)
    assert blocked.state.owner_authorized is True
    assert blocked.state.hard_safety_block is True
    assert blocked.state.effective_new_entry is False

    cleared = transition(blocked.state, ControlEvent.HARD_BLOCK_CLEAR)
    assert cleared.state.owner_authorized is True
    assert cleared.state.hard_safety_block is False
    assert cleared.state.effective_new_entry is True


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (ControlEvent.OWNER_DISARM, ControlEvent.TECHNICAL_READY),
        (ControlEvent.TECHNICAL_READY, ControlEvent.OWNER_DISARM),
    ],
)
def test_disarm_and_recovery_order_has_same_safe_result(
    first: ControlEvent,
    second: ControlEvent,
) -> None:
    start = transition(ready_state(), ControlEvent.TECHNICAL_BLOCK).state
    state = transition(start, first).state
    state = transition(state, second).state

    assert state.owner_authorized is False
    assert state.technical_readiness is TechnicalReadiness.READY
    assert state.effective_new_entry is False


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (ControlEvent.SCOPE_INVALIDATE, ControlEvent.TECHNICAL_READY),
        (ControlEvent.TECHNICAL_READY, ControlEvent.SCOPE_INVALIDATE),
    ],
)
def test_scope_invalidation_wins_over_recovery_in_any_order(
    first: ControlEvent,
    second: ControlEvent,
) -> None:
    start = transition(ready_state(), ControlEvent.TECHNICAL_BLOCK).state
    state = transition(start, first).state
    state = transition(state, second).state

    assert state.exact_scope is False
    assert state.owner_authorized is False
    assert state.effective_new_entry is False


def test_core_source_has_no_io_or_runtime_dependencies() -> None:
    root = Path("src/bybit_workbench/runtime_control_core")
    forbidden_import_roots = {
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
    forbidden_calls = {"open", "exec", "eval"}

    for source_path in root.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                import_roots = {alias.name.split(".")[0] for alias in node.names}
                assert not (import_roots & forbidden_import_roots)
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                assert node.module.split(".")[0] not in forbidden_import_roots
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls


def test_only_approved_shadow_adapter_imports_core() -> None:
    protected_roots = (Path("operations"), Path("src/bybit_workbench"))
    allowed_core = Path("src/bybit_workbench/runtime_control_core")
    approved_importers = {Path("src/bybit_workbench/runtime_control_shadow.py")}

    offenders: list[str] = []
    for root in protected_roots:
        for path in root.rglob("*.py"):
            if path.is_relative_to(allowed_core) or path in approved_importers:
                continue
            if "runtime_control_core" in path.read_text(encoding="utf-8"):
                offenders.append(path.as_posix())

    assert offenders == []