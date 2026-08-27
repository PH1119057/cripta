from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from statistics import fmean

from bybit_workbench.domain.models import Candle, InstrumentRules
from bybit_workbench.domain.types import PositionSide
from bybit_workbench.strategies.indicators import latest_wilder_atr


@dataclass(frozen=True, slots=True)
class MarketPlanRecommendation:
    symbol: str
    timeframe: str
    direction: PositionSide
    reference_price: Decimal
    entry_price: Decimal
    stop_price: Decimal
    take_profit: Decimal
    atr: Decimal
    reward_to_risk: Decimal
    reason: str


def recommend_market_plan(
    *,
    symbol: str,
    timeframe: str,
    candles: tuple[Candle, ...],
    instrument: InstrumentRules,
    mark_price: Decimal | None,
    last_price: Decimal | None,
) -> MarketPlanRecommendation:
    """Build a deterministic baseline plan from recent trend and ATR.

    This is intentionally a recommendation layer, not an arming decision. Risk sizing
    remains delegated to the existing RiskEngine and must still pass Check before Arm.
    """

    normalized_symbol = symbol.strip().upper()
    normalized_timeframe = timeframe.strip()
    if instrument.symbol != normalized_symbol:
        raise ValueError("instrument rules do not match selected symbol")
    closed = tuple(
        candle
        for candle in candles
        if candle.symbol == normalized_symbol
        and candle.timeframe == normalized_timeframe
        and candle.is_closed
    )
    if len(closed) < 21:
        raise ValueError("at least 21 closed candles are required for an automatic plan")

    atr = latest_wilder_atr(closed, 14)
    if atr is None or atr <= 0:
        raise ValueError("ATR is unavailable for the selected market")
    reference = mark_price or last_price or closed[-1].close
    if reference <= 0:
        raise ValueError("current market price is unavailable")

    closes = [float(candle.close) for candle in closed]
    fast = Decimal(str(fmean(closes[-8:])))
    slow = Decimal(str(fmean(closes[-21:])))
    momentum = closed[-1].close - closed[-6].close
    bullish_votes = int(fast >= slow) + int(momentum >= 0) + int(closed[-1].close >= slow)
    direction = PositionSide.LONG if bullish_votes >= 2 else PositionSide.SHORT

    tick = instrument.tick_size
    pullback = max(tick, min(atr * Decimal("0.15"), reference * Decimal("0.005")))
    stop_distance = max(atr * Decimal("1.25"), reference * Decimal("0.003"))
    stop_distance = min(stop_distance, reference * Decimal("0.03"))
    reward_to_risk = Decimal("1.8")

    if direction is PositionSide.LONG:
        entry = _floor_to_step(reference - pullback, tick)
        stop = _floor_to_step(entry - stop_distance, tick)
        take_profit = _floor_to_step(entry + stop_distance * reward_to_risk, tick)
    else:
        entry = _ceil_to_step(reference + pullback, tick)
        stop = _ceil_to_step(entry + stop_distance, tick)
        take_profit = _ceil_to_step(entry - stop_distance * reward_to_risk, tick)

    if min(entry, stop, take_profit) <= 0:
        raise ValueError("automatic plan produced a non-positive price")

    trend = "восходящий" if direction is PositionSide.LONG else "нисходящий"
    reason = (
        f"Trend+ATR: {trend} уклон; fast(8)={fast:.6f}, slow(21)={slow:.6f}, "
        f"ATR14={atr:.6f}; entry — небольшой откат от Mark, stop ≈1.25 ATR, "
        f"TP={reward_to_risk}:1."
    )
    return MarketPlanRecommendation(
        symbol=normalized_symbol,
        timeframe=normalized_timeframe,
        direction=direction,
        reference_price=reference,
        entry_price=entry,
        stop_price=stop,
        take_profit=take_profit,
        atr=atr,
        reward_to_risk=reward_to_risk,
        reason=reason,
    )


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step
