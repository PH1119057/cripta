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
        and node.name == "command_loop"
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
