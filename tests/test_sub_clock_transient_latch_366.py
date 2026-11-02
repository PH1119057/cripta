"""Guard SUB pre-send clock failures against irreversible account latching."""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "operations/connectivity/private_runtime.py"


def test_transient_clock_failure_does_not_install_durable_sub_latch() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    private_loop = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "private_loop"
    )
    guarded = [
        node for node in ast.walk(private_loop)
        if isinstance(node, ast.If)
        and "isinstance(exc, UnsafeBybitClock)" in ast.unparse(node.test)
    ]
    assert len(guarded) == 1
    branch = guarded[0]
    assert "connection.rollback()" in "\n".join(ast.unparse(node) for node in branch.body)
    assert "disarm_new_entries" not in ast.unparse(branch.body)
    assert "disarm_new_entries(connection, PRIVATE_RECONNECT_GATE_REASON)" in ast.unparse(
        branch.orelse
    )


def test_clock_validation_still_required_before_mutating_post() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "def assert_mutation_clock_safe()" in source
    assert "return fresh_bybit_time_calibration()" in source
    assert "BYBIT_TIME_PROBE_ATTEMPTS = 3" in source


def test_recovery_script_direct_execution_resolves_installed_src() -> None:
    script = SOURCE.parents[2] / "operations/implementation/recover_p90_account_ws_safety.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    configured = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "insert"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "path"
    ]
    assert configured, "direct recovery script must insert installed src before imports"
