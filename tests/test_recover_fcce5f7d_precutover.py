"""Static contract tests for exact fcce5f7d pre-cutover recovery."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations/infrastructure/recover_fcce5f7d_precutover.py"
SOURCE = SCRIPT.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
SUB = "ta-853ac5fcd58185697396d33cd57459fa"
EXPECTED_ACTIVE_SUB = {
    f"cripta-private-runtime@{SUB}.service",
    f"cripta-lifecycle-account@{SUB}.service",
    f"cripta-account-position@{SUB}.service",
}


def _literal_assignment(name: str):
    for node in TREE.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"assignment not found: {name}")


def test_recovery_script_is_exact_and_no_argument():
    assert _literal_assignment("RELEASE") == "fcce5f7dd5c785b7af385aed162f4296d489290c"
    assert _literal_assignment("BASELINE") == "10238598a374172071db0caa61ac727c796d64c2"
    assert _literal_assignment("RELEASE_TREE") == "f3ad6f8d43330b537d5b7e589adc09a55e9a3f8f"
    assert SOURCE.find("len(sys.argv) != 1") >= 0
    assert SOURCE.find("argparse") < 0


def test_recovery_proves_ui_only_release_delta():
    release_delta = _literal_assignment("RELEASE_DELTA")
    assert release_delta == {
        "operations/dashboard/index.html",
        "tests/test_dashboard_p90_owner_arm_control.py",
    }
    assert SOURCE.find("RELEASE_DELTA_NOT_UI_ONLY") >= 0


def test_recovery_does_not_repeat_failed_migration_sequence():
    assert SOURCE.find("migration_files=(") < 0
    assert SOURCE.find('runtime_schema.py"),\n            "migrate"') < 0
    assert SOURCE.find('"/usr/bin/pg_dump"') < 0
    assert SOURCE.find('"validate"') >= 0
    assert SOURCE.find("SCHEMA_APPLIED_BEFORE") >= 0


def test_recovery_restores_exact_sub_supervision_set_only():
    active_sub = _literal_assignment("ACTIVE_SUB_UNITS")
    sub_units = _literal_assignment("SUB_UNITS")
    assert active_sub == EXPECTED_ACTIVE_SUB
    consumer = f"cripta-universal-entry-consumer@{SUB}.service"
    assert consumer in sub_units
    assert consumer not in active_sub


def test_recovery_keeps_trading_fail_closed_and_flat():
    assert SOURCE.find('if (gate, permissions, sessions) != ("0", "0", "0")') >= 0
    assert SOURCE.find('if (positions, orders, claims) != ("0", "0", "0")') >= 0
    assert SOURCE.find("OWNER_ARM=UNCHANGED") >= 0
    assert SOURCE.find("TRADING_EXPOSURE_NOT_FLAT") >= 0


def test_recovery_requires_exact_failed_backup_and_controller_failure():
    assert SOURCE.find("20261030_224919_10238598") >= 0
    assert _literal_assignment("FAILED_UNIT") == "cripta-release-controller.service"
    assert SOURCE.find("COMMAND_FAILED:systemd-run:3") >= 0
    assert SOURCE.find("FAILED_RELEASE_AUDIT_MISMATCH") >= 0


def test_recovery_source_delta_after_target_is_only_recovery_files():
    allowed_source_delta = _literal_assignment("ALLOWED_SOURCE_DELTA")
    assert allowed_source_delta == {
        "operations/infrastructure/recover_fcce5f7d_precutover.py",
        "operations/sql/20261004_r1_live_arm_waiver.sql",
        "tests/test_live_arm_evidence_migration_replay.py",
        "tests/test_recover_fcce5f7d_precutover.py",
    }
