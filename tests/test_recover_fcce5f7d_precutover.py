"""Static contract tests for exact fcce5f7d pre-cutover recovery."""
import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations/infrastructure/recover_fcce5f7d_precutover.py"
spec = importlib.util.spec_from_file_location("recover_fcce5f7d", SCRIPT)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

SUB = "ta-853ac5fcd58185697396d33cd57459fa"
EXPECTED_ACTIVE_SUB = {
    f"cripta-private-runtime@{SUB}.service",
    f"cripta-lifecycle-account@{SUB}.service",
    f"cripta-account-position@{SUB}.service",
}


def test_recovery_script_is_exact_and_no_argument():
    source = SCRIPT.read_text(encoding="utf-8")
    ast.parse(source)
    assert module.RELEASE == "fcce5f7dd5c785b7af385aed162f4296d489290c"
    assert module.BASELINE == "10238598a374172071db0caa61ac727c796d64c2"
    assert module.RELEASE_TREE == "f3ad6f8d43330b537d5b7e589adc09a55e9a3f8f"
    assert source.find("len(sys.argv) != 1") >= 0
    assert source.find("argparse") == -1


def test_recovery_proves_ui_only_release_delta():
    assert module.RELEASE_DELTA == {
        "operations/dashboard/index.html",
        "tests/test_dashboard_p90_owner_arm_control.py",
    }
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.find("RELEASE_DELTA_NOT_UI_ONLY") >= 0


def test_recovery_does_not_repeat_failed_migration_sequence():
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.find("operations/sql/") == -1
    assert source.find('runtime_schema.py"),\n            "migrate"') == -1
    assert source.find('"validate"') >= 0
    assert source.find("SCHEMA_APPLIED_BEFORE") >= 0


def test_recovery_restores_exact_sub_supervision_set_only():
    assert module.ACTIVE_SUB_UNITS == EXPECTED_ACTIVE_SUB
    consumer = f"cripta-universal-entry-consumer@{SUB}.service"
    assert consumer in module.SUB_UNITS
    assert consumer not in module.ACTIVE_SUB_UNITS


def test_recovery_keeps_trading_fail_closed_and_flat():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'if (gate, permissions, sessions) != ("0", "0", "0")' in source
    assert 'if (positions, orders, claims) != ("0", "0", "0")' in source
    assert source.find("OWNER_ARM=UNCHANGED") >= 0
    assert source.find("TRADING_EXPOSURE_NOT_FLAT") >= 0


def test_recovery_requires_exact_failed_backup_and_controller_failure():
    assert str(module.FAILED_BACKUP).find("20261030_224919_10238598") >= 0
    assert module.FAILED_UNIT == "cripta-release-controller.service"
    source = SCRIPT.read_text(encoding="utf-8")
    assert source.find("COMMAND_FAILED:systemd-run:3") >= 0
    assert source.find("FAILED_RELEASE_AUDIT_MISMATCH") >= 0


def test_recovery_source_delta_after_target_is_only_recovery_files():
    assert module.ALLOWED_SOURCE_DELTA == {
        "operations/infrastructure/recover_fcce5f7d_precutover.py",
        "tests/test_recover_fcce5f7d_precutover.py",
    }
