from __future__ import annotations

import ast
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "operations/dashboard/app.py"
OBSERVER = ROOT / "operations/monitoring/universal_entry_shadow.py"


def test_attested_observer_sha_matches_current_source() -> None:
    tree = ast.parse(DASHBOARD.read_text(encoding="utf-8"))
    assignments = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "R1_PARITY_ATTESTED_MODULE_SHA256"
            for target in node.targets
        )
    ]
    assert len(assignments) == 1
    hashes = ast.literal_eval(assignments[0].value)
    actual_hash = hashlib.sha256(OBSERVER.read_bytes()).hexdigest()
    assert hashes["operations/monitoring/universal_entry_shadow.py"] == actual_hash
    assert len(hashes) == 6


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
