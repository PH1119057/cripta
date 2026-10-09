"""Regression for P1: Exit Shadow must not retain ownership locks between ticks."""
import ast
from pathlib import Path

def test_exit_shadow_explicit_transaction_boundary():
    path=Path("operations/monitoring/universal_exit_shadow_runtime.py")
    source=path.read_text()
    tree=ast.parse(source)
    assert 'autocommit=True' in source
    assert 'idle_in_transaction_session_timeout=120000' in source
    main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="main")
    loop=next(n for n in ast.walk(main) if isinstance(n,ast.While))
    calls=[n for n in ast.walk(loop) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=="_evaluate_claimed_positions"]
    assert len(calls)==1
    evaluation=calls[0]
    with_blocks=[n for n in ast.walk(loop) if isinstance(n,ast.With)]
    assert any(evaluation in list(ast.walk(block)) and any(isinstance(item.context_expr,ast.Call) and isinstance(item.context_expr.func,ast.Attribute) and item.context_expr.func.attr=="transaction" for item in block.items) for block in with_blocks)
