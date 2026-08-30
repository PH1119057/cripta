from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
RUNTIME = ROOT / "operations" / "strategy_dispatcher" / "passive_runtime.py"
UNIT = ROOT / "operations" / "strategy_dispatcher" / "cripta-strategy-dispatcher.service"


def test_passive_runtime_has_no_trading_dependency_or_mutation_surface() -> None:
    source = RUNTIME.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    forbidden = {"execution", "risk", "position_supervisor", "strategies", "private"}
    assert not any(any(part in module for part in forbidden) for module in imports)
    assert "place_order" not in source
    assert "cancel_order" not in source
    assert "set_stop" not in source
    assert "close_position" not in source


def test_passive_runtime_persists_only_none_trading_effect() -> None:
    source = RUNTIME.read_text(encoding="utf-8")
    assert "CHECK(trading_effect='NONE')" in source
    assert "strategy_dispatcher.runs" in source
    assert "strategy_dispatcher.assessments" in source
    unit = UNIT.read_text(encoding="utf-8")
    assert "strategy_dispatcher_passive.py" in unit
    assert "--poll-seconds 60" in unit
