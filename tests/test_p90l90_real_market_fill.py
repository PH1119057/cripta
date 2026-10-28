"""Never rebase attached REAL TP/SL against an unrelated Bybit position."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bybit_workbench.universal_entry.p90l90_real_market_fill import (
    P90RealMarketPositionUnverified,
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
