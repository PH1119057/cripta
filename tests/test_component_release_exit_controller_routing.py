"""#849: no-argument management controller must never route Exit-only to FULL.

This tests the exact existing controller AST with fake Git, not live systemd.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

CONTROLLER = (
    Path(__file__).resolve().parents[1]
    / "operations/infrastructure/cripta-release-controller"
)
BASE = "a" * 40
TARGET = "b" * 40
UI = "c" * 40


class ControllerScopeDenied(Exception):
    pass


def _selector(changed: tuple[str, ...]):
    source = CONTROLLER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(
        x for x in tree.body
        if isinstance(x, ast.FunctionDef) and x.name == "_select_scoped_purpose"
    )
    compiled = compile(
        ast.Module(body=[node], type_ignores=[]), str(CONTROLLER), "exec",
    )

    def fake_git(*args: str) -> str:
        if args[:2] == ("diff", "--name-only"):
            # Old native producer ABI unchanged in this task.
            if "--no-renames" not in args:
                return ""
            if args[3] == UI:
                return "\n".join(changed)
            if args[3] == BASE:
                return "\n".join(changed)
        raise AssertionError("Unexpected fake Git operation: " + repr(args))

    scope = {
        "SHA": re.compile(r"[0-9a-f]{40}\Z"),
        "ControllerError": ControllerScopeDenied,
        "PRODUCER_ABI_PATHS": (
            "operations/monitoring/public_market_source_union_capture.py",
        ),
        "NATIVE_CUTOVER_PURPOSE": "NATIVE",
        "DASHBOARD_PURPOSE": "DASHBOARD",
        "FULL_PURPOSE": "FULL",
        "_git": fake_git,
    }
    exec(compiled, scope)
    return scope["_select_scoped_purpose"]


@pytest.mark.parametrize(
    "path",
    (
        "operations/connectivity/universal_exit_consumer.py",
        "operations/connectivity/p90_real_independent_exit.py",
        "operations/connectivity/p90_real_persistent_trail.py",
        "operations/connectivity/p90_r06_exit_owner.py",
        "operations/connectivity/p90l90_real_postfill_source_client.py",
        "src/bybit_workbench/universal_exit/p90l90_real_v3.py",
    ),
)
def test_exit_delta_never_implicitly_deploys_global_runtime(path: str) -> None:
    select = _selector((path,))
    with pytest.raises(
        ControllerScopeDenied, match="EXIT_COMPONENT_RELEASE_RAIL_NOT_INSTALLED",
    ):
        select(target=TARGET, runtime_baseline=BASE, dashboard_baseline=UI)


@pytest.mark.parametrize(
    "path",
    (
        "operations/infrastructure/component_release_bundle.py",
        "operations/infrastructure/component_release_exit_identity.py",
        "operations/infrastructure/component_release_exit_process_readback.py",
        "operations/infrastructure/component_release_exit_offline_stage.py",
        "operations/infrastructure/component_release_plan.py",
        "operations/infrastructure/exit_component_requirements.lock",
        "operations/infrastructure/cripta-release-controller",
    ),
)
def test_component_tooling_never_implicitly_deploys_global_runtime(path: str) -> None:
    select = _selector((path,))
    with pytest.raises(
        ControllerScopeDenied, match="COMPONENT_RELEASE_TOOLING_NOT_DEPLOYABLE",
    ):
        select(target=TARGET, runtime_baseline=BASE, dashboard_baseline=UI)


def test_mixed_exit_and_private_runtime_still_blocks_full() -> None:
    select = _selector((
        "operations/connectivity/p90_real_persistent_trail.py",
        "operations/connectivity/private_runtime.py",
    ))
    with pytest.raises(
        ControllerScopeDenied, match="EXIT_COMPONENT_RELEASE_RAIL_NOT_INSTALLED",
    ):
        select(target=TARGET, runtime_baseline=BASE, dashboard_baseline=None)


def test_mixed_infra_and_dashboard_blocks_implicit_full() -> None:
    select = _selector((
        "operations/infrastructure/component_release_bundle.py",
        "operations/dashboard/index.html",
    ))
    with pytest.raises(
        ControllerScopeDenied, match="COMPONENT_RELEASE_TOOLING_NOT_DEPLOYABLE",
    ):
        select(target=TARGET, runtime_baseline=BASE, dashboard_baseline=UI)


def test_unrelated_full_contour_path_unchanged() -> None:
    select = _selector(("operations/connectivity/private_runtime.py",))
    assert select(target=TARGET, runtime_baseline=BASE, dashboard_baseline=None) == (
        "FULL", False,
    )


def test_existing_dashboard_only_path_is_still_scoped() -> None:
    select = _selector(("operations/dashboard/index.html",))
    assert select(target=TARGET, runtime_baseline=BASE, dashboard_baseline=UI) == (
        "DASHBOARD", False,
    )


def test_bad_input_identity_is_still_rejected() -> None:
    select = _selector(("operations/infrastructure/component_release_bundle.py",))
    with pytest.raises(ControllerScopeDenied, match="RELEASE_SCOPE_IDENTITY_INVALID"):
        select(target="not-a-commit", runtime_baseline=BASE, dashboard_baseline=UI)
