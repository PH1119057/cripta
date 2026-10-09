"""R1 Exit: extrema gate, ATR independence, and Bybit tick normalization."""
import ast
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path


def test_shadow_decision_requires_opposite_range_change():
    source = Path("operations/monitoring/universal_exit_shadow_runtime.py").read_text()
    assert 'structural_field = (' in source
    assert '"range_high" if position.direction is TradeDirection.LONG' in source
    assert 'else "range_low"' in source
    assert "strategy_exit.exit_observations" in source
    assert 'Decimal(str(previous["boundary"])) == Decimal(str(geometry[structural_field]))' in source
    assert "continue" in source
    assert 'target_inner' in source
    assert '"atr": str(zone.atr)' in source
    assert '"limit": "240"' in source


def _tp_helper():
    source = Path("operations/connectivity/private_runtime.py").read_text()
    root = ast.parse(source)
    func = next(
        n for n in root.body
        if isinstance(n, ast.FunctionDef) and n.name == "_normalize_dynamic_tp"
    )
    namespace = {
        "Decimal": Decimal,
        "ROUND_CEILING": ROUND_CEILING,
        "ROUND_FLOOR": ROUND_FLOOR,
    }
    exec(compile(ast.Module(body=[func], type_ignores=[]), "<tested-helper>", "exec"), namespace)
    return namespace["_normalize_dynamic_tp"]


def test_dynamic_tp_rounds_toward_market_and_is_idempotent_at_tick():
    normalize = _tp_helper()
    tick = Decimal("0.00001")
    a = Decimal("0.1786181574785753566284955564")
    b = Decimal("0.1786199999999")
    assert normalize(a, tick, position_side="Sell") == Decimal("0.17862")
    assert normalize(b, tick, position_side="Sell") == Decimal("0.17862")
    assert normalize(a, tick, position_side="Buy") == Decimal("0.17861")
    assert normalize(Decimal("0.17738"), tick, position_side="Sell") == Decimal("0.17738")
    assert normalize(Decimal("250.071"), Decimal("0.1"), position_side="Sell") == Decimal("250.1")
    assert normalize(Decimal("250.071"), Decimal("0.1"), position_side="Buy") == Decimal("250.0")


def test_dynamic_tp_rejects_invalid_price_and_side():
    import pytest

    normalize = _tp_helper()
    for value, step, side in [
        (Decimal("0"), Decimal("0.00001"), "Sell"),
        (Decimal("1"), Decimal("0"), "Sell"),
        (Decimal("NaN"), Decimal("0.1"), "Buy"),
        (Decimal("1"), Decimal("0.1"), "UNKNOWN"),
    ]:
        with pytest.raises(RuntimeError):
            normalize(value, step, position_side=side)


def test_tp_compared_to_live_exchange_order_after_normalization():
    source = Path("operations/connectivity/private_runtime.py").read_text()
    start = source.index("if action_kind is ExitActionKind.SET_TP:")
    end = source.index("if action_kind is ExitActionKind.SET_PROTECTION:", start)
    block = source[start:end]
    assert "_normalize_dynamic_tp(" in block
    assert '"/v5/order/realtime"' in block
    assert 'Decimal(str(item.get("price") or 0)) == target' in block
    assert '"dynamic TP already resting"' in block
    assert "STRATEGY_EXIT_TP_ORDER_TYPE_UNSUPPORTED" in block
