"""Guard against obsolete R1 Exit decisions before any Bybit mutation."""
import ast
from pathlib import Path


def test_universal_exit_rejects_superseded_decision_before_exchange_mutation():
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_universal_exit_position"
    )
    body = ast.get_source_segment(source, func)
    assert "STRATEGY_EXIT_DECISION_ID_MISSING" in body
    assert "STRATEGY_EXIT_DECISION_NOT_FOUND" in body
    assert "STRATEGY_EXIT_DECISION_SUPERSEDED" in body
    assert "FROM strategy_exit.exit_decisions" in body
    assert "WHERE strategy_position_id=%s" in body
    assert "ORDER BY decided_at DESC, exit_decision_id DESC" in body
    assert body.index("STRATEGY_EXIT_DECISION_SUPERSEDED") < body.index("exchange_position = next(")


def test_exit_order_replacement_remains_reduce_only_post_only():
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    start = source.index("if action_kind is ExitActionKind.SET_TP:")
    end = source.index("if action_kind is ExitActionKind.SET_PROTECTION:", start)
    block = source[start:end]
    assert '"reduceOnly": True' in block
    assert '"timeInForce": "PostOnly"' in block
    assert '"dynamic TP already resting"' in block
