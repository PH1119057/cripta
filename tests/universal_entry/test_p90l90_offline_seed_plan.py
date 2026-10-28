"""Offline P90 PAPER warmup must use 7 completed UTC days and ATR200."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.domain.models import Candle
from bybit_workbench.universal_entry.p90l90_offline_seed_plan import (
    plan_p90l90_offline_seed,
)

AT = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def bars(tf: str, n: int, *, symbol: str = "LTCUSDT") -> tuple[Candle, ...]:
    minutes = int(tf)
    return tuple(
        Candle(
            symbol=symbol, timeframe=tf,
            opened_at=AT-timedelta(minutes=minutes*(n-i)),
            closed_at=AT-timedelta(minutes=minutes*(n-i-1)),
            open=Decimal("100"), high=Decimal("101"),
            low=Decimal("99"), close=Decimal("100"),
            volume=Decimal("2"), is_closed=True,
        )
        for i in range(n)
    )


def test_full_seven_day_and_wilder_history_seeds_asof_closed_boundary():
    fives, fifteens = bars("5", 2600), bars("15", 1000)
    plan = plan_p90l90_offline_seed(
        "LTCUSDT", {"5": fives, "15": fifteens},
    )
    assert plan.closed_5m_at == AT
    assert plan.closed_15m_at == AT
    assert plan.initial_geometry.effective_at == AT
    assert plan.initial_geometry.previous_seven_day_median.is_finite()
    assert plan.initial_geometry.raw_h9_union_span == Decimal("2")
    assert plan.fives is fives and plan.fifteens is fifteens


def test_missing_prevd7_or_15m_history_never_seeds():
    with pytest.raises(RuntimeError, match="SOURCE_WARMUP_NOT_READY"):
        plan_p90l90_offline_seed(
            "LTCUSDT", {"5": bars("5", 2000), "15": bars("15", 1000)},
        )
    with pytest.raises(RuntimeError, match="SOURCE_WARMUP_NOT_READY"):
        plan_p90l90_offline_seed(
            "LTCUSDT", {"5": bars("5", 2600), "15": bars("15", 150)},
        )


def test_discontinuous_or_future_source_cannot_be_interpolated():
    five = list(bars("5", 2600))
    five[50] = replace(
        five[50],
        opened_at=five[50].opened_at+timedelta(minutes=5),
        closed_at=five[50].closed_at+timedelta(minutes=5),
    )
    with pytest.raises(RuntimeError, match="CAUSAL_GEOMETRY_NOT_READY"):
        plan_p90l90_offline_seed(
            "LTCUSDT", {"5": tuple(five), "15": bars("15", 1000)},
        )


def test_stale_15m_watermark_or_wrong_symbol_fails_closed():
    stale = bars("15", 1000)[:-1]
    with pytest.raises(RuntimeError, match="WATERMARK_OR_SYMBOL_MISMATCH"):
        plan_p90l90_offline_seed(
            "LTCUSDT", {"5": bars("5", 2600), "15": stale},
        )
    with pytest.raises(RuntimeError, match="WATERMARK_OR_SYMBOL_MISMATCH"):
        plan_p90l90_offline_seed(
            "LTCUSDT", {"5": bars("5", 2600, symbol="INJUSDT"),
                         "15": bars("15", 1000)},
        )
    with pytest.raises(ValueError, match="UNKNOWN_SYMBOL"):
        plan_p90l90_offline_seed(
            "BTCUSDT", {"5": bars("5", 2600), "15": bars("15", 1000)},
        )
