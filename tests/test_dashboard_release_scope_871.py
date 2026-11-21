"""Permanent independent Dashboard release classification (#871).

Pure unit tests: no host service, repository mutation or Exchange API calls.
The real canonical Dashboard verifier still checks app.py AST/auth/control.
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
SOURCE = CONTROLLER.read_text(encoding="utf-8")
RUNTIME = "a" * 40
UI_BASELINE = "b" * 40
TARGET = "c" * 40
ABI_PATH = "operations/monitoring/public_market_source_union_capture.py"


class ScopeBlocked(RuntimeError):
    pass


def scope(
    changed_since_ui: tuple[str, ...],
    *,
    changed_since_runtime: tuple[str, ...] | None = None,
    dashboard_baseline: str | None = UI_BASELINE,
) -> tuple[str, bool]:
    """Exercise the exact shipping classifier body with inert Git substitutes."""
    tree = ast.parse(SOURCE)
    func = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_select_scoped_purpose"
    )
    program = compile(
        ast.Module(body=[func], type_ignores=[]),
        str(CONTROLLER),
        "exec",
    )
    runtime_paths = (
        changed_since_ui
        if changed_since_runtime is None
        else changed_since_runtime
    )

    def fake_git(*args: str) -> str:
        if args == (
            "diff", "--name-only", RUNTIME, TARGET, "--", ABI_PATH
        ):
            return ""
        if args == (
            "diff", "--name-only", "--no-renames",
            dashboard_baseline, TARGET, "--"
        ):
            return "\n".join(changed_since_ui)
        if args == (
            "diff", "--name-only", "--no-renames",
            RUNTIME, TARGET, "--"
        ):
            return "\n".join(runtime_paths)
        raise AssertionError(f"unexpected git operation: {args}")

    globals_dict = {
        "SHA": re.compile(r"^[0-9a-f]{40}$"),
        "ControllerError": ScopeBlocked,
        "_git": fake_git,
        "PRODUCER_ABI_PATHS": (ABI_PATH,),
        "DASHBOARD_PURPOSE": "DASHBOARD_PRESENTATION_RELEASE",
        "FULL_PURPOSE": "EXACT_VERIFIED_SOURCE_RELEASE",
        "NATIVE_CUTOVER_PURPOSE": "NATIVE_MARKET_SOURCE_CODE_CUTOVER_RELEASE",
    }
    exec(program, globals_dict)
    return globals_dict["_select_scoped_purpose"](
        target=TARGET,
        runtime_baseline=RUNTIME,
        dashboard_baseline=dashboard_baseline,
    )


def test_exact_ui_plus_original_869_reviewed_tests_stays_dashboard_only():
    paths = (
        "operations/dashboard/index.html",
        "tests/test_dashboard_market_monitor_open_render.py",
        "tests/test_dashboard_observer_faults_stage6b.py",
        "tests/test_dashboard_open_positions_rail.py",
        "tests/test_dashboard_p90_first_pass_351.py",
        "tests/test_dashboard_strategy_trade_control.py",
        "tests/test_dashboard_trading_layout.py",
        "tests/test_live_portal_async_ux.py",
        "tests/test_owner_controlled_live_test.py",
        "tests/test_strategy_dashboard_u6.py",
        "tests/test_strategy_monitor_readiness_ui.py",
        "tests/test_universal_entry_monitoring_runtime.py",
        "docs/DEVELOPMENT_RELEASE_RULES_RU.md",
        "docs/DOCUMENTATION_INDEX_RU.md",
        "README.md",
        "AGENTS.md",
    )
    assert scope(paths) == ("DASHBOARD_PRESENTATION_RELEASE", False)


def test_approved_read_only_dashboard_backend_routes_to_only_ui_verifier():
    assert scope((
        "operations/dashboard/app.py",
        "tests/test_dashboard_ui_release_separation.py",
    )) == ("DASHBOARD_PRESENTATION_RELEASE", False)
    assert "DASHBOARD_PRESENTATION_READ_MODEL_SCOPE=PASS" in (
        Path("operations/infrastructure/deploy_dashboard_ui.sh")
        .read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("changed", [
    ("docs/DEVELOPMENT_RELEASE_RULES_RU.md",),
    ("README.md", "AGENTS.md", "tests/test_dashboard_open_positions_rail.py"),
])
def test_docs_or_tests_only_is_not_a_full_trading_release(changed):
    with pytest.raises(ScopeBlocked, match="NON_EXECUTABLE_SOURCE_ADVANCE_ONLY"):
        scope(changed)


def test_current_dashboard_and_no_new_runtime_delta_never_full_restarts():
    with pytest.raises(ScopeBlocked, match="DASHBOARD_ALREADY_CURRENT_NO_FULL_RELEASE"):
        scope(
            (),
            dashboard_baseline=TARGET,
            changed_since_runtime=(
                "operations/dashboard/index.html",
                "docs/DOCUMENTATION_INDEX_RU.md",
                "tests/test_dashboard_open_positions_rail.py",
            ),
        )


def test_no_component_diff_is_not_a_full_release():
    with pytest.raises(ScopeBlocked, match="NO_COMPONENT_DELTA_TO_DEPLOY"):
        scope((), dashboard_baseline=TARGET, changed_since_runtime=())


@pytest.mark.parametrize("extra_path", [
    "src/bybit_workbench/source_live_identity.py",
    "operations/infrastructure/cripta-release-controller",
    "operations/infrastructure/install_verified_release.sh",
    "operations/dashboard/archive_v2.py",
    "operations/dashboard/coin_research_catalog.py",
    "operations/dashboard/instrument_research_catalog.json",
    "operations/dashboard_ui/unverified.js",
    "operations/systemd/cripta-private-runtime.service",
    "operations/migrations/0001.py",
    "tests/conftest.py",
    "tests/data/account_grants.json",
    "scripts/research/active_runner.py",
    ".github/workflows/quality.yml",
    "pyproject.toml",
])
def test_mixed_dashboard_and_other_path_never_becomes_implicit_full(extra_path):
    with pytest.raises(ScopeBlocked, match="DASHBOARD_MIXED_EXECUTABLE_SCOPE"):
        scope(("operations/dashboard/index.html", extra_path))


def test_unclassified_old_or_new_name_of_rename_is_not_ignored():
    # --no-renames includes the removed executable path and new UI path.
    with pytest.raises(ScopeBlocked, match="DASHBOARD_MIXED_EXECUTABLE_SCOPE"):
        scope((
            "operations/dashboard/index.html",
            "operations/dashboard/auth_control.py",
        ))
    assert '"--name-only", "--no-renames"' in SOURCE


def test_ui_change_cannot_hide_pending_trading_code_from_runtime_baseline():
    with pytest.raises(
        ScopeBlocked,
        match="DASHBOARD_TRADING_RUNTIME_DELTA_PENDING",
    ):
        scope(
            ("operations/dashboard/index.html", "tests/test_dashboard.py"),
            changed_since_runtime=(
                "operations/dashboard/index.html",
                "src/bybit_workbench/entry_engine.py",
            ),
        )


def test_runtime_only_release_is_not_mislabeled_as_dashboard():
    assert scope(("src/bybit_workbench/trading_engine.py",)) == (
        "EXACT_VERIFIED_SOURCE_RELEASE",
        False,
    )


def test_static_ui_release_still_has_no_trade_privileges_or_service_restarts():
    classifier = SOURCE.split("def _select_scoped_purpose(", 1)[1].split(
        "\ndef main() -> int:", 1
    )[0]
    assert "DASHBOARD_MIXED_EXECUTABLE_SCOPE" in classifier
    assert "DASHBOARD_TRADING_RUNTIME_DELTA_PENDING" in classifier
    assert "NON_EXECUTABLE_SOURCE_ADVANCE_ONLY" in classifier
    assert "NATIVE_AND_DASHBOARD_SCOPE_MIXED" in classifier
    assert "return DASHBOARD_PURPOSE, False" in classifier
    assert "systemctl restart" not in classifier
    assert "control.execution_gates" not in classifier
    assert "tradeCommand(" not in classifier
