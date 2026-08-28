from __future__ import annotations

import ast
from pathlib import Path


def test_command_loop_does_not_overwrite_exchange_credentials() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    command_loop = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "command_worker_loop"
    )
    assigned_names = {
        target.id
        for node in ast.walk(command_loop)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
        for target in (
            node.targets if isinstance(node, ast.Assign) else [node.target]
        )
        if isinstance(target, ast.Name)
    }
    assert "key" not in assigned_names
    assert "secret" not in assigned_names


def test_command_worker_has_fail_closed_portfolio_and_market_gates() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    assert 'MAX_PORTFOLIO_ENTRIES = int(' in source
    assert 'ENTRY_COOLDOWN_MS = int(' in source
    assert 'mayak_v2.snapshots' in source
    assert 'up_share < Decimal("0.55")' in source
    assert 'down_share < Decimal("0.55")' in source
    assert '"теневой допуск"' in source
    assert 'runtime.entry_decisions' in source
    for symbol in ("1000PEPEUSDT", "DOGEUSDT", "NEARUSDT", "XLMUSDT"):
        assert symbol in source


def test_command_loop_restarts_after_internal_failure() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    command_loop = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "command_loop"
    )
    assert any(isinstance(node, ast.Try) for node in ast.walk(command_loop))
    assert "command_worker_loop(key, secret)" in source
