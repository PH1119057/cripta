"""P90 initial attached SL/RAW TP requires a fresh valid Bybit trigger side."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    validate_real_v3_attached_protection_at_last_price,
)


def _ticker(symbol: str, last: object) -> dict[str, object]:
    return {
        "retCode": 0,
        "result": {
            "list": [{"symbol": symbol, "lastPrice": last}],
        },
    }


@pytest.mark.parametrize(("symbol", "side", "sl", "tp", "last"), (
    ("LTCUSDT", "Buy", "90", "102.5", "100"),
    ("INJUSDT", "Sell", "110", "97.5", "100"),
    ("DOTUSDT", "Buy", "0.90", "1.04", "1.00"),
    ("ARBUSDT", "Sell", "1.10", "0.97", "1.00"),
    ("APTUSDT", "Buy", "9.00", "10.5", "10"),
))
def test_exact_attached_exchange_trigger_side(
    symbol: str, side: str, sl: str, tp: str, last: str,
) -> None:
    assert validate_real_v3_attached_protection_at_last_price(
        symbol=symbol,
        side=side,
        instrument_tick=Decimal("0.01"),
        stop_loss=Decimal(sl),
        take_profit=Decimal(tp),
        ticker_response=_ticker(symbol, last),
    ) == Decimal(last)


@pytest.mark.parametrize(("side", "sl", "tp", "last"), (
    ("Buy", "90", "102.5", "102.5"),  # already at target
    ("Buy", "90", "102.5", "102.6"),  # passed target
    ("Buy", "90", "102.5", "89"),  # passed stop
    ("Sell", "110", "97.5", "97.5"),
    ("Sell", "110", "97.5", "96"),
    ("Sell", "110", "97.5", "111"),
))
def test_no_market_order_if_last_already_crossed_protection(
    side: str, sl: str, tp: str, last: str,
) -> None:
    with pytest.raises(ValueError, match="ALREADY_CROSSED"):
        validate_real_v3_attached_protection_at_last_price(
            symbol="LTCUSDT",
            side=side,
            instrument_tick=Decimal("0.1"),
            stop_loss=Decimal(sl),
            take_profit=Decimal(tp),
            ticker_response=_ticker("LTCUSDT", last),
        )


@pytest.mark.parametrize("ticker", (
    {},
    {"retCode": 10001, "result": {"list": []}},
    {"retCode": 0, "result": {"list": []}},
    {"retCode": 0, "result": {"list": [
        {"symbol": "LTCUSDT", "lastPrice": "100"},
        {"symbol": "LTCUSDT", "lastPrice": "100"},
    ]}},
    _ticker("INJUSDT", "100"),
    _ticker("LTCUSDT", "NaN"),
    _ticker("LTCUSDT", "Infinity"),
    _ticker("LTCUSDT", "0"),
))
def test_bad_or_foreign_ticker_cannot_prove_attached_protection(
    ticker: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        validate_real_v3_attached_protection_at_last_price(
            symbol="LTCUSDT",
            side="Buy",
            instrument_tick=Decimal("0.1"),
            stop_loss=Decimal("90"),
            take_profit=Decimal("102.5"),
            ticker_response=ticker,
        )


def test_unaligned_target_or_missing_target_denies() -> None:
    for target in (None, Decimal("102.55")):
        with pytest.raises(ValueError):
            validate_real_v3_attached_protection_at_last_price(
                symbol="LTCUSDT",
                side="Buy",
                instrument_tick=Decimal("0.1"),
                stop_loss=Decimal("90"),
                take_profit=target,
                ticker_response=_ticker("LTCUSDT", "100"),
            )


def test_runtime_preflights_before_first_exchange_mutation() -> None:
    source = Path("operations/connectivity/private_runtime.py").read_text(
        encoding="utf-8"
    )
    preflight = source.index("p90v3.validate_real_v3_attached_protection_at_last_price(")
    leverage = source.index('"/v5/position/set-leverage"', preflight)
    opening = source.index('"/v5/order/create"', leverage)
    assert preflight < leverage < opening
    assert "current_ticker, _ = api_get(" in source[:preflight]
