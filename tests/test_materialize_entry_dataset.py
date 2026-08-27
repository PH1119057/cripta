from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.research.materialize_entry_dataset import _jsonable, aggregate_candles


def candle(index: int, price: str) -> Candle:
    opened = datetime(2026, 5, 18, tzinfo=UTC) + timedelta(minutes=index)
    value = Decimal(price)
    return Candle(
        symbol="TESTUSDT",
        timeframe="1",
        opened_at=opened,
        closed_at=opened + timedelta(minutes=1),
        open=value,
        high=value + Decimal("1"),
        low=value - Decimal("1"),
        close=value + Decimal("0.5"),
        volume=Decimal("2"),
        is_closed=True,
    )


def test_aggregate_five_minute_ohlcv() -> None:
    source = tuple(candle(index, str(100 + index)) for index in range(5))
    result = aggregate_candles(source, timeframe_minutes=5)
    assert len(result) == 1
    assert result[0].open == Decimal("100")
    assert result[0].high == Decimal("105")
    assert result[0].low == Decimal("99")
    assert result[0].close == Decimal("104.5")
    assert result[0].volume == Decimal("10")


def test_aggregate_rejects_gap() -> None:
    source = (candle(0, "100"), candle(2, "101"))
    with pytest.raises(ValueError, match="contiguous"):
        aggregate_candles(source, timeframe_minutes=2)


def test_jsonable_preserves_parameter_shapes() -> None:
    assert _jsonable((30, 60, Decimal("0.25"))) == [30, 60, "0.25"]
