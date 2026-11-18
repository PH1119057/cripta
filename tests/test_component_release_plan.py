"""Fail-closed independent-component scope regression (#849).

The planner is deliberately NOT connected to the deployed Release Controller
until each component has an immutable package, versioned ABI and rollback.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "operations" / "infrastructure"))
from component_release_plan import git_changed_paths, plan_paths  # noqa: E402


def test_exit_change_never_claims_entry_or_financial_privileges() -> None:
    result = plan_paths([
        "src/bybit_workbench/universal_exit/engine.py",
        "operations/connectivity/universal_exit_consumer.py",
        "tests/test_p90_exit_service_pythonpath_import.py",
    ])
    assert result.release_scope == "COMPONENT_CANDIDATE"
    assert result.components == ("EXIT",)
    assert result.candidate_units == ("cripta-universal-exit-consumer.service",)
    assert not result.install_authorized
    assert not result.database_mutation_authorized
    assert not result.financial_mutation_authorized
    assert result.requires_review == ()


def test_entry_change_does_not_restart_exit_or_all_sub_services() -> None:
    result = plan_paths([
        "src/bybit_workbench/universal_entry/engine.py",
        "operations/connectivity/universal_entry_consumer.py",
    ])
    assert result.components == ("ENTRY",)
    assert result.release_scope == "COMPONENT_CANDIDATE"
    assert result.candidate_units == (
        "cripta-universal-entry-consumer.service",
        "cripta-universal-entry-consumer@*.service",
    )
    assert "cripta-universal-exit-consumer.service" not in result.candidate_units


def test_ui_static_change_has_no_trading_unit_restart() -> None:
    result = plan_paths(["operations/dashboard/index.html", "docs/TRADING_CONTOUR_RU.md"])
    assert result.release_scope == "DASHBOARD_PRESENTATION_CANDIDATE"
    assert result.components == ("DASHBOARD_PRESENTATION",)
    assert result.candidate_units == ()


def test_dashboard_app_mixed_control_is_not_declared_readonly() -> None:
    result = plan_paths(["operations/dashboard/app.py"])
    assert result.release_scope == "COMPATIBILITY_REVIEW_REQUIRED"
    assert "MIXED_DASHBOARD_READ_CONTROL:operations/dashboard/app.py" in result.requires_review
    assert not result.install_authorized
    assert not result.database_mutation_authorized


def test_schema_never_inherits_service_write_or_restart() -> None:
    result = plan_paths(["operations/sql/20261119_new_schema.sql"])
    assert result.release_scope == "COMPATIBILITY_REVIEW_REQUIRED"
    assert result.candidate_units == ()
    assert result.requires_review == ("POSTGRESQL_MIGRATION:operations/sql/20261119_new_schema.sql",)


@pytest.mark.parametrize("path,kind", [
    ("src/bybit_workbench/source_live_identity.py", "SHARED_OR_UNMAPPED_LIBRARY"),
    ("operations/infrastructure/install_verified_release.sh", "RELEASE_INFRASTRUCTURE"),
    ("operations/systemd/cripta-universal-exit-consumer.service", "SERVICE_UNIT_OR_RUNTIME_ABI"),
    ("pyproject.toml", "SHARED_DEPENDENCIES"),
    (".github/workflows/quality.yml", "CI_OR_RELEASE_CONTRACT"),
    ("operations/dashboard/unknown_api.py", "SHARED_OR_UNMAPPED_OPERATION"),
    ("some-new-surface", "UNKNOWN_PATH_OWNER"),
])
def test_unknown_and_shared_require_explicit_compatibility(path: str, kind: str) -> None:
    plan = plan_paths([path])
    assert plan.release_scope == "COMPATIBILITY_REVIEW_REQUIRED"
    assert plan.requires_review == (f"{kind}:{path}",)
    assert not plan.install_authorized


def test_independent_observer_and_dispatcher_are_separate_candidates() -> None:
    observer = plan_paths(["operations/monitoring/universal_entry_shadow.py"])
    dispatcher = plan_paths(["operations/dispatcher_v2/dispatcher.py"])
    assert observer.candidate_units == ("cripta-universal-entry-observer.service",)
    assert dispatcher.candidate_units == ("cripta-dispatcher-v2.service",)


def test_multiple_components_do_not_trigger_unrelated_core_restart() -> None:
    result = plan_paths([
        "operations/connectivity/universal_exit_consumer.py",
        "operations/connectivity/universal_entry_consumer.py",
    ])
    assert result.release_scope == "MULTI_COMPONENT_REVIEW_REQUIRED"
    assert result.components == ("ENTRY", "EXIT")
    assert not result.install_authorized


def test_one_shared_file_dominates_component_candidate() -> None:
    result = plan_paths([
        "src/bybit_workbench/universal_exit/engine.py",
        "src/bybit_workbench/source_live_identity.py",
    ])
    assert result.release_scope == "COMPATIBILITY_REVIEW_REQUIRED"
    assert result.components == ("EXIT",)
    assert len(result.requires_review) == 1


def test_documentation_research_and_tests_never_need_runtime_deploy() -> None:
    result = plan_paths([
        "docs/DOCUMENTATION_INDEX_RU.md", "research/analysis.py",
        "tests/test_release_controller_contract.py", "README.md", "AGENTS.md",
    ])
    assert result.release_scope == "SOURCE_ONLY_NO_RUNTIME_DEPLOY"
    assert result.candidate_units == ()
    assert result.requires_review == ()


@pytest.mark.parametrize("name", ["", "/absolute", "../parent", "a/../b", "a//b", "a\\b", "a\nb"])
def test_unsafe_path_fails_closed(name: str) -> None:
    with pytest.raises(ValueError, match="DELTA_PATH_UNSAFE"):
        plan_paths([name])


def test_empty_or_bad_source_delta_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="DELTA_EMPTY"):
        plan_paths([])
    with pytest.raises(ValueError, match="SHA_INVALID"):
        git_changed_paths(tmp_path, "main", "HEAD")


def test_git_renamed_file_exposes_both_owned_sides(tmp_path: Path) -> None:
    if subprocess.run(["git", "--version"], capture_output=True).returncode:
        pytest.skip("git not installed")
    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True,
            text=True, capture_output=True,
        )
        return done.stdout.strip()
    git("init")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Independent Release Test")
    f = tmp_path / "operations" / "connectivity"
    f.mkdir(parents=True)
    (f / "universal_exit_consumer.py").write_text("old", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "original")
    first = git("rev-parse", "HEAD")
    (f / "universal_exit_consumer.py").rename(f / "new_implementation.py")
    git("add", ".")
    git("commit", "-m", "renamed")
    second = git("rev-parse", "HEAD")
    changes = git_changed_paths(tmp_path, first, second)
    assert set(changes) == {
        "operations/connectivity/universal_exit_consumer.py",
        "operations/connectivity/new_implementation.py",
    }
    classified = plan_paths(changes)
    assert classified.release_scope == "COMPATIBILITY_REVIEW_REQUIRED"
    assert "SHARED_OR_UNMAPPED_OPERATION:operations/connectivity/new_implementation.py" in classified.requires_review
