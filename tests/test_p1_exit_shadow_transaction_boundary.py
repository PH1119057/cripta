"""Exit Shadow must not retain ownership locks between ticks."""

import ast
from pathlib import Path


def test_exit_shadow_explicit_transaction_boundary():
    source = Path(
        "operations/monitoring/universal_exit_shadow_runtime.py"
    ).read_text()
    tree = ast.parse(source)
    assert "autocommit=True" in source
    assert "idle_in_transaction_session_timeout=120000" in source
    main = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    loop = next(node for node in ast.walk(main) if isinstance(node, ast.While))
    eval_calls = [
        node for node in ast.walk(loop)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_evaluate_claimed_positions"
    ]
    assert len(eval_calls) == 1
    contexts = [
        block for block in ast.walk(loop)
        if isinstance(block, ast.With) and eval_calls[0] in ast.walk(block)
    ]
    assert any(
        isinstance(item.context_expr, ast.Call)
        and isinstance(item.context_expr.func, ast.Attribute)
        and item.context_expr.func.attr == "transaction"
        for block in contexts for item in block.items
    )
