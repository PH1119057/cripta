"""Static contract tests for the exact 5be6 post-migration recovery."""
import ast
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations/infrastructure/recover_5be6a7eb_post_migration.py"
SOURCE = SCRIPT.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)

EXPECTED_ACTIVE = {
    "cripta-bybit-latency.service",
    "cripta-dashboard.service",
    "cripta-dispatcher-v2-context-correlator.service",
    "cripta-dispatcher-v2.service",
    "cripta-health-monitor.service",
    "cripta-job-intake.service",
    "cripta-job-runner.service",
    "cripta-lifecycle-supervisor.service",
    "cripta-m3-trade-analyst.service",
    "cripta-mayak-v2.service",
    "cripta-opportunity-tracker.service",
    "cripta-position-supervisor.service",
    "cripta-private-runtime.service",
    "cripta-safety-observer.service",
    "cripta-shadow-command-worker.service",
    "cripta-universal-entry-consumer.service",
    "cripta-universal-entry-observer.service",
    "cripta-universal-exit-shadow.service",
    "cripta-universal-exit-consumer.service",
}


def _literal_assignment(name: str):
    for node in TREE.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"assignment not found: {name}")


def test_recovery_script_is_valid_python_and_fixed_identity():
    assert _literal_assignment("RELEASE") == "5be6a7eb48024a554f4c69ec8f71ea777bbd98a7"
    assert _literal_assignment("BASELINE") == "db50682d372c382ed77da9635a21d5e3a39da549"
    assert _literal_assignment("RELEASE_TREE") == "8156f34a464966d9acbe06fc9819e2c6c22b3948"
    assert "len(sys.argv) != 1" in SOURCE
    assert "argparse" not in SOURCE


def test_recovery_resumes_after_migrations_without_reapplying_them():
    assert "operations/sql/" not in SOURCE
    assert "pg_dump" not in SOURCE
    assert "runtime_schema.py" in SOURCE
    assert '"validate"' in SOURCE
    assert "SCHEMA_APPLIED_NOT_BEFORE" in SOURCE


def test_recovered_active_set_is_exact_and_sub_remains_inert():
    active_services = _literal_assignment("ACTIVE_SERVICES")
    managed_services = set(_literal_assignment("MANAGED_SERVICES"))
    sub_units = _literal_assignment("SUB_UNITS")
    assert active_services == EXPECTED_ACTIVE
    assert active_services.issubset(managed_services)
    assert all("@" not in unit for unit in active_services)
    assert all(unit not in active_services for unit in sub_units)


def test_stop_window_matches_failed_installer_event():
    start = datetime(2026, 10, 30, 20, 3, 0, tzinfo=UTC)
    end = datetime(2026, 10, 30, 20, 4, 0, tzinfo=UTC)
    assert "STOP_WINDOW_START = datetime(2026, 10, 30, 20, 3, 0, tzinfo=UTC)" in SOURCE
    assert "STOP_WINDOW_END = datetime(2026, 10, 30, 20, 4, 0, tzinfo=UTC)" in SOURCE
    value = datetime(2026, 10, 30, 20, 3, 31, tzinfo=UTC)
    assert value >= start
    assert value <= end


def test_recovery_keeps_trading_controls_fail_closed():
    assert 'if (gate, permissions, sessions) != ("0", "0", "0")' in SOURCE
    assert "OWNER_ARM=UNCHANGED" in SOURCE
    assert "systemctl\", \"disable\", \"cripta-release-controller.service" in SOURCE


def test_privileged_payload_is_sourced_from_exact_git_objects():
    assert 'f"{RELEASE}:{relative}"' in SOURCE
    assert "install_bytes(relative" in SOURCE
    assert "git_bytes(relative)" in SOURCE


def test_source_delta_is_bounded_to_recovery_and_docs():
    allowed_source_delta = _literal_assignment("ALLOWED_SOURCE_DELTA")
    assert allowed_source_delta == {
        "docs/DOCUMENTATION_INDEX_RU.md",
        "docs/RESEARCH_COMPUTE_RULES_RU.md",
        "operations/infrastructure/recover_5be6a7eb_post_migration.py",
        "tests/test_recover_5be6a7eb_post_migration.py",
    }
