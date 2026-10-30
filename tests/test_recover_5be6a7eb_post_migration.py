"""Static and pure-contract tests for the exact 5be6 post-migration recovery."""
import ast
import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations/infrastructure/recover_5be6a7eb_post_migration.py"
spec = importlib.util.spec_from_file_location("recover_5be6", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

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


def test_recovery_script_is_valid_python_and_fixed_identity():
    source = SCRIPT.read_text(encoding="utf-8")
    ast.parse(source)
    assert module.RELEASE == "5be6a7eb48024a554f4c69ec8f71ea777bbd98a7"
    assert module.BASELINE == "db50682d372c382ed77da9635a21d5e3a39da549"
    assert module.RELEASE_TREE == "8156f34a464966d9acbe06fc9819e2c6c22b3948"
    assert "len(sys.argv) != 1" in source
    assert "argparse" not in source


def test_recovery_resumes_after_migrations_without_reapplying_them():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "operations/sql/" not in source
    assert "pg_dump" not in source
    assert "runtime_schema.py" in source
    assert '"validate"' in source
    assert "SCHEMA_APPLIED_NOT_BEFORE" in source


def test_recovered_active_set_is_exact_and_sub_remains_inert():
    assert module.ACTIVE_SERVICES == EXPECTED_ACTIVE
    assert module.ACTIVE_SERVICES <= set(module.MANAGED_SERVICES)
    assert all("@" not in unit for unit in module.ACTIVE_SERVICES)
    assert all(unit not in module.ACTIVE_SERVICES for unit in module.SUB_UNITS)


def test_stop_window_matches_failed_installer_event():
    value = module.parse_systemd_time("Fri 2026-10-30 20:03:31 UTC")
    assert value == datetime(2026, 10, 30, 20, 3, 31, tzinfo=UTC)
    assert value >= module.STOP_WINDOW_START\n    assert value <= module.STOP_WINDOW_END


def test_recovery_keeps_trading_controls_fail_closed():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'if (gate, permissions, sessions) != ("0", "0", "0")' in source
    assert "OWNER_ARM=UNCHANGED" in source
    assert "systemctl\", \"disable\", \"cripta-release-controller.service" in source


def test_privileged_payload_is_sourced_from_exact_git_objects():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'f"{module.RELEASE}:{relative}"' not in source
    assert 'f"{RELEASE}:{relative}"' in source
    assert "install_bytes(relative" in source
    assert "git_bytes(relative)" in source


def test_source_delta_is_bounded_to_recovery_and_docs():
    assert module.ALLOWED_SOURCE_DELTA == {
        "docs/DOCUMENTATION_INDEX_RU.md",
        "docs/RESEARCH_COMPUTE_RULES_RU.md",
        "operations/infrastructure/recover_5be6a7eb_post_migration.py",
        "tests/test_recover_5be6a7eb_post_migration.py",
    }
