from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.mayak.research.feature_engine import Bar, ClosedBarSeries


def test_closed_bar_series_never_uses_unclosed_bar() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars = tuple(
        Bar(
            opened_at=start + timedelta(minutes=5 * index),
            closed_at=start + timedelta(minutes=5 * (index + 1)),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0 + index,
            volume=1.0,
        )
        for index in range(3)
    )
    series = ClosedBarSeries(bars)
    cutoff = start + timedelta(minutes=12)
    assert series.slice_ending(cutoff, 1)[0] == bars[1]


def test_closed_bar_series_fails_without_warmup() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    series = ClosedBarSeries((Bar(now, now + timedelta(minutes=5), 1, 1, 1, 1, 0),))
    with pytest.raises(ValueError, match="warmup"):
        series.slice_ending(now + timedelta(minutes=5), 2)
