"""Owner P90 pre-send recovery wiring: no Exchange or implicit LIVE mutation."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "operations/implementation/recover_p90_pre_send_safety.py"
DASHBOARD = ROOT / "operations/dashboard/app.py"
INSTALL = ROOT / "operations/infrastructure/install_verified_release.sh"
MIGRATION = ROOT / "operations/sql/20261117_p90_pre_send_account_recovery.sql"


def _load_cli_module():
    spec = importlib.util.spec_from_file_location(
        "p90_pre_send_recovery_cli_under_test", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_is_exact_scope_and_never_calls_exchange_mutation() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "P90PrivateProofClient" in source
    assert ".read_flat_account()" in source
    assert "control.recover_p90_pre_send_account_safety" in source
    assert "exchange_mutation" in source
    assert '"exchange_mutation": False' in source
    assert '"gate_mutation": False' in source
    assert '"owner_arm": False' in source
    forbidden = ("/v5/order/create", "/v5/order/cancel", "/v5/position/trading-stop")
    for token in forbidden:
        assert token not in source
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_flat_account"
    ]
    assert len(calls) == 1


def test_cli_sanitizes_unknown_errors() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "P90_PRE_SEND_RECOVERY_ERROR=" in source
    assert "P90_PRE_SEND_RECOVERY_PROOF_OR_RUNTIME_ERROR" in source
    assert "print(str(exc)" not in source
    assert "traceback" not in source


def test_dashboard_runs_pre_send_recovery_before_ws_and_prearm_ready() -> None:
    app = DASHBOARD.read_text(encoding="utf-8")
    start = app.index("def _u6_prepare_p90_real_v3_readiness(")
    end = app.index("def _execute_p90_real_v3_owner_arm(", start)
    body = app[start:end]
    pre = "pre_send_recovery = _recover_p90_real_pre_send_safety_if_needed("
    ws = "ws_recovery = _recover_p90_real_ws_safety_if_needed("
    assert body.index(pre) < body.index(ws) < body.index("mark_real_readiness_ready(")
    helper = app[
        app.index("def _recover_p90_real_pre_send_safety_if_needed("):
        app.index("def _recover_p90_real_ws_safety_if_needed(")
    ]
    assert "PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED" not in helper
    assert "P90_REAL_PRE_SEND_RECOVERY_REASON" in helper
    assert "recover_p90_pre_send_safety.py" not in helper
    assert "P90_REAL_PRE_SEND_RECOVERY_SCRIPT" in helper
    assert "exchange_mutation" in helper
    assert "gate_mutation" in helper
    assert "owner_arm" in helper


def test_migration_is_installed_and_does_not_touch_financial_rights() -> None:
    installer = INSTALL.read_text(encoding="utf-8")
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "operations/sql/20261117_p90_pre_send_account_recovery.sql" in installer
    assert "PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED" in sql
    assert "PRE_MUTATION_SAFETY_BLOCK:PreMutationSafetyBlock" in sql
    assert "ENTRY_NO_MUTATION:" in sql
    assert "exchange_commitment_ref IS NULL" in sql
    assert "runtime.executions" in sql
    assert "runtime.exchange_order_history" in sql
    assert "runtime.hot_orders" in sql
    assert "pre_send_account_recovery_events" in sql
    assert "DELETE FROM runtime.account_entry_safety_blocks" in sql
    assert "UPDATE control.execution_gates" not in sql
    assert "UPDATE strategy_entry.execution_permissions" not in sql
    assert "INSERT INTO control.live_arm_sessions" not in sql
    assert "INSERT INTO control.account_live_arm_sessions" not in sql


@pytest.mark.parametrize(
    "error",
    [
        "PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN",
        "EXCHANGE_MUTATION_BARRIER:AmbiguousBybitMutation",
        "PRE_MUTATION_SAFETY_BLOCK:UnsafeBybitClock",
    ],
)
def test_sql_recovery_cannot_accept_other_safety_classes(error: str) -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    if error == "PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN":
        assert (
            "v_latch.reason IS DISTINCT FROM "
            "'PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED'"
        ) in sql
    else:
        assert error not in sql
