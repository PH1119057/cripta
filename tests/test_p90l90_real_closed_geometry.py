"""Synthetic 36×5m immutable RAW boundary tests; never Exchange authorization."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.universal_entry.contracts import TradeDirection
from bybit_workbench.universal_exit.p90l90_real_closed_geometry import (
    P90RealClosedGeometryUnverified,
    opposite_raw_l53_from_closed_fives,
)

AT = datetime(2026, 10, 29, 2, 0, tzinfo=UTC)
D = Decimal


def _candles() -> tuple[Candle, ...]:
    result: list[Candle] = []
    for i in range(36):
        at = AT - timedelta(minutes=5 * (35 - i))
        result.append(Candle(
            symbol="LTCUSDT", timeframe="5",
            opened_at=at - timedelta(minutes=5),
            closed_at=at,
            open=D("100"), high=D("101"), low=D("99"), close=D("100"),
            volume=D("50"), is_closed=True,
        ))
    return tuple(result)


def _target(
    candles: tuple[Candle, ...],
    side: TradeDirection = TradeDirection.LONG,
) -> Decimal:
    return opposite_raw_l53_from_closed_fives(
        symbol="LTCUSDT", direction=side,
        closed_at=AT, candles=candles,
    )


def test_long_high36_and_short_low36_use_same_immutable_closed_bars() -> None:
    bars = _candles()
    bars = (
        *bars[:8], replace(bars[8], high=D("109")),
        *bars[9:22], replace(bars[22], low=D("92")),
        *bars[23:],
    )
    assert _target(bars, TradeDirection.LONG) == D("109")
    assert _target(bars, TradeDirection.SHORT) == D("92")


@pytest.mark.parametrize("bad", ("missing", "duplicate", "future", "open", "wrong_symbol"))
def test_missing_or_future_closed_candle_never_produces_real_exit(
    bad: str,
) -> None:
    bars = _candles()
    if bad == "missing":
        bars = bars[:-1]
    elif bad == "duplicate":
        bars = (*bars[:10], bars[9], *bars[11:])
    elif bad == "future":
        bars = (*bars[:-1], replace(
            bars[-1], opened_at=AT, closed_at=AT + timedelta(minutes=5),
        ))
    elif bad == "open":
        bars = (*bars[:-1], replace(bars[-1], is_closed=False))
    else:
        bars = (*bars[:-1], replace(bars[-1], symbol="INJUSDT"))
    with pytest.raises(P90RealClosedGeometryUnverified):
        _target(bars)


def test_non_five_minute_boundary_or_unordered_history_fails_closed() -> None:
    bars = _candles()
    with pytest.raises(P90RealClosedGeometryUnverified):
        opposite_raw_l53_from_closed_fives(
            symbol="LTCUSDT", direction=TradeDirection.LONG,
            closed_at=AT + timedelta(seconds=1), candles=bars,
        )
    reversed_bars = tuple(reversed(bars))
    with pytest.raises(P90RealClosedGeometryUnverified):
        _target(reversed_bars)
