from collections.abc import Sequence
from decimal import Decimal

from bybit_workbench.domain.models import Candle
from bybit_workbench.domain.types import PositionSide
from bybit_workbench.risk import ceil_to_step, floor_to_step


def true_ranges(candles: Sequence[Candle]) -> tuple[Decimal, ...]:
    if not candles:
        return ()
    ranges: list[Decimal] = []
    previous_close: Decimal | None = None
    for candle in candles:
        if previous_close is None:
            value = candle.high - candle.low
        else:
            value = max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        ranges.append(value)
        previous_close = candle.close
    return tuple(ranges)


def wilder_atr(candles: Sequence[Candle], period: int) -> tuple[Decimal | None, ...]:
    if period <= 0:
        raise ValueError("ATR period must be positive")
    ranges = true_ranges(candles)
    values: list[Decimal | None] = [None] * len(ranges)
    if len(ranges) < period:
        return tuple(values)
    current = sum(ranges[:period], Decimal("0")) / Decimal(period)
    values[period - 1] = current
    for index in range(period, len(ranges)):
        current = (current * Decimal(period - 1) + ranges[index]) / Decimal(period)
        values[index] = current
    return tuple(values)


def latest_wilder_atr(candles: Sequence[Candle], period: int) -> Decimal | None:
    values = wilder_atr(candles, period)
    return None if not values else values[-1]


def causal_channel(
    candles: Sequence[Candle],
    lookback: int,
) -> tuple[Decimal, Decimal]:
    if lookback <= 0 or len(candles) < lookback:
        raise ValueError("insufficient candles for causal channel")
    window = candles[-lookback:]
    return max(item.high for item in window), min(item.low for item in window)


def normalize_stop(value: Decimal, side: PositionSide, tick_size: Decimal | None) -> Decimal:
    if tick_size is None:
        return value
    if side is PositionSide.LONG:
        return floor_to_step(value, tick_size)
    if side is PositionSide.SHORT:
        return ceil_to_step(value, tick_size)
    raise ValueError("flat position cannot normalize a stop")
