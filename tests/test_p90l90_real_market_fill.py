"""Never rebase attached REAL TP/SL against an unrelated Bybit position."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bybit_workbench.universal_entry.p90l90_real_market_fill import (
    P90RealMarketPositionUnverified,
    exact_market_order_execution_ids,
    exact_open_market_position,
)


def _position(
    symbol: str = "LTCUSDT",
    side: str = "Buy",
    position_idx: int = 0,
    size: str = "0.2",
    avg_price: str = "100",
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "side": side,
        "positionIdx": position_idx,
        "size": size,
        "avgPrice": avg_price,
    }


def test_single_exact_one_way_fill_is_provisionally_recognized() -> None:
    row = _position()
    assert exact_open_market_position(
        [_position(size="0"), row], symbol="LTCUSDT", side="Buy",
    ) is row


@pytest.mark.parametrize("rows", (
    [_position(side="Sell")],
    [_position(symbol="INJUSDT")],
    [_position(position_idx=1)],
    [_position(), _position()],
    [_position(size="-1")],
    [_position(size="NaN")],
    [_position(size="Infinity")],
    [_position(size="bogus")],
    [_position(avg_price="NaN")],
    [_position(avg_price="0")],
    ["corrupt"],
))
def test_ambiguous_foreign_hedge_and_malformed_live_state_is_never_adopted(
    rows: list[object],
) -> None:
    with pytest.raises(P90RealMarketPositionUnverified):
        exact_open_market_position(rows, symbol="LTCUSDT", side="Buy")


def test_flat_is_unknown_not_an_instruction_to_reenter_or_force_close() -> None:
    assert exact_open_market_position(
        [_position(size="0")], symbol="LTCUSDT", side="Buy",
    ) is None
    assert exact_open_market_position([], symbol="LTCUSDT", side="Buy") is None


def test_main_and_sub_are_not_interchangeable() -> None:
    row = _position(symbol="DOTUSDT", side="Sell", avg_price="1.5")
    assert exact_open_market_position(
        [row], symbol="DOTUSDT", side="Sell",
    ) is row
    with pytest.raises(P90RealMarketPositionUnverified):
        exact_open_market_position([row], symbol="LTCUSDT", side="Sell")


def test_runtime_uses_strict_real_readback_after_order_ack() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    execute = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "execute_command"
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "exact_open_market_position"
        for node in ast.walk(execute)
    )
    assert "REAL_MARKET_POSITION_READBACK_UNVERIFIED" in source
    assert 'api_post("/v5/order/create", order, key, secret)' in source
    assert '"takeProfit": str(target)' in source
    assert '"stopLoss": str(stop)' in source


def _fill(
    *, execution_id: str = "e1", side: str = "Buy",
    qty: str = "0.1", price: str = "100",
    order_id: str = "bybit-order-1",
) -> dict[str, object]:
    return {
        "orderId": order_id,
        "orderLinkId": "entry-link",
        "symbol": "LTCUSDT",
        "side": side,
        "orderType": "Market",
        "execType": "Trade",
        "execId": execution_id,
        "execQty": qty,
        "execPrice": price,
        "execTime": "1790000000000",
        "closedSize": "",
    }


def _verified_executions(rows: object, *, average: str = "100"):
    return exact_market_order_execution_ids(
        rows,
        exchange_order_id="bybit-order-1",
        entry_order_link_id="entry-link",
        symbol="LTCUSDT",
        side="Buy",
        position_size=__import__("decimal").Decimal("0.1"),
        position_avg=__import__("decimal").Decimal(average),
        tick_size=__import__("decimal").Decimal("0.01"),
    )


def test_true_signed_market_execution_ids_prove_position_fill():
    assert _verified_executions([_fill()]) == ("e1",)
    assert _verified_executions([
        _fill(execution_id="e1", qty="0.05", price="100"),
        _fill(execution_id="e2", qty="0.05", price="101"),
    ], average="100.5") == ("e1", "e2")


@pytest.mark.parametrize("rows", (
    [],
    [_fill(order_id="other-order")],
    [_fill(side="Sell")],
    [_fill(qty="0.09")],
    [_fill(price="103")],
    [_fill(execution_id="")],
    [_fill(execution_id="e1"), _fill(execution_id="e1", qty="0.01")],
    [_fill(qty="NaN")],
    [_fill(price="-1")],
    [_fill(), {"execId": "e2"}],
))
def test_market_fill_must_be_proven_by_exact_exchange_order_and_execution(
    rows: list[dict[str, object]],
):
    with pytest.raises(P90RealMarketPositionUnverified):
        _verified_executions(rows)


def test_runtime_checks_signed_executions_before_correcting_initial_protection():
    source = Path("operations/connectivity/private_runtime.py").read_text(
        encoding="utf-8"
    )
    ack = source.index('result=api_post("/v5/order/create", order, key, secret)')
    signed = source.index('"/v5/execution/list"', ack)
    proof = source.index("real_fill_execution_ids = exact_market_order_execution_ids(", signed)
    correction = source.index('"/v5/position/trading-stop"', proof)
    assert ack < signed < proof < correction
    assert "REAL_MARKET_SIGNED_EXECUTION_LINEAGE_UNVERIFIED" in source
