from __future__ import annotations

import ast
import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "operations/dashboard/app.py"
OBSERVER = ROOT / "operations/monitoring/universal_entry_shadow.py"


def test_parity_critical_modules_are_pinned_to_current_git_release() -> None:
    tree = ast.parse(DASHBOARD.read_text(encoding="utf-8"))
    assignments = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "R1_PARITY_CRITICAL_MODULES"
            for target in node.targets
        )
    ]
    assert len(assignments) == 1
    modules = ast.literal_eval(assignments[0].value)
    assert len(modules) == 7
    assert "operations/monitoring/universal_entry_shadow.py" in modules
    assert "src/bybit_workbench/universal_entry/r1_independent_parity.py" in modules
    for relative in modules:
        expected = subprocess.check_output(
            ["git", "-C", str(ROOT), "show", f"HEAD:{relative}"]
        )
        actual = (ROOT / relative).read_bytes()
        actual = actual.replace(b"\r\n", b"\n")
        expected = expected.replace(b"\r\n", b"\n")
        assert hashlib.sha256(actual).digest() == hashlib.sha256(expected).digest()


def test_observer_cleanup_guards_do_not_alter_entry_policy() -> None:
    source = OBSERVER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_run_observer_epoch"
    ]
    assert len(functions) == 1
    body = ast.get_source_segment(source, functions[0])
    assert body is not None
    assert "if oi_thread.ident is not None:" in body
    assert "if trade_mirror_thread.ident is not None:" in body
