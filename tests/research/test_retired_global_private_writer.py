"""Historical unscoped private writer must be unstartable after account-key migration."""

import ast
from pathlib import Path

LEGACY = (
    Path(__file__).resolve().parents[2]
    / "research"
    / "server"
    / "connectivity"
    / "private_runtime.py"
)


def test_retired_global_private_runtime_main_fails_before_side_effects() -> None:
    source = LEGACY.read_text(encoding="utf-8")
    module = ast.parse(source, filename=str(LEGACY))
    mains = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    ]
    assert len(mains) == 1
    actionable = [node for node in mains[0].body if not isinstance(node, ast.Global)]
    assert actionable
    assert isinstance(actionable[0], ast.Raise)
    raised = ast.get_source_segment(source, actionable[0]) or ""
    assert "RETIRED_GLOBAL_PRIVATE_RUNTIME" in raised

    # Historical source remains present for forensic provenance, never execution.
    assert "DELETE FROM runtime.hot_positions WHERE symbol=%s" in source
    assert "ON CONFLICT(exec_id)" in source
