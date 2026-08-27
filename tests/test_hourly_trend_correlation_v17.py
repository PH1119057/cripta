from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bybit_workbench.research.exit_break_even_v13 import CoreSignal
from bybit_workbench.research.hourly_trend_correlation_v17 import (
    HourCandle,
    PolicyOutcome,
    build_feature,
    combined_trend,
    ema_position,
    ema_relation_to_direction,
    ema_slope,
    enrich_ema,
    relation_to_direction,
    strict_trend,
    structure_label,
)


def _candle(hour: int, *, open_: float, high: float, low: float, close: float) -> HourCandle:
    return HourCandle(
        start_at=datetime(2026, 1, 1, hour, tzinfo=UTC),
        open=open_,
        high=high,
        low=low,
        close=close,
        trade_count=10,
    )


def test_structure_labels_higher_and_lower_ranges() -> None:
    previous = _candle(1, open_=100, high=101, low=99, close=100)
    bullish = _candle(2, open_=100, high=102, low=100, close=101)
    bearish = _candle(2, open_=100, high=100, low=98, close=99)
    mixed = _candle(2, open_=100, high=102, low=98, close=100)
    assert structure_label(bullish, previous) == "bullish"
    assert structure_label(bearish, previous) == "bearish"
    assert structure_label(mixed, previous) == "mixed"


def test_direction_relations() -> None:
    assert relation_to_direction("Long", "bullish") == "with"
    assert relation_to_direction("Long", "bearish") == "against"
    assert relation_to_direction("Short", "bearish") == "with"
    assert relation_to_direction("Short", "bullish") == "against"
    assert relation_to_direction("Long", "mixed") == "mixed"


def test_ema_relations() -> None:
    assert ema_position(101.0, 100.0) == "above"
    assert ema_position(99.0, 100.0) == "below"
    assert ema_relation_to_direction("Long", "above") == "with"
    assert ema_relation_to_direction("Short", "above") == "against"
    assert ema_slope(101.0, 100.0) == "rising"
    assert ema_slope(99.0, 100.0) == "falling"


def test_combined_and_strict_trend_require_agreement() -> None:
    assert combined_trend("bullish", "above") == "bullish"
    assert combined_trend("bullish", "below") == "mixed"
    assert strict_trend("bullish", "above", "rising") == "bullish"
    assert strict_trend("bullish", "above", "falling") == "mixed"


def test_ema_warmup_is_explicit() -> None:
    candles = tuple(
        HourCandle(
            start_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index),
            open=100 + index,
            high=101 + index,
            low=99 + index,
            close=100 + index,
            trade_count=1,
        )
        for index in range(22)
    )
    enriched = enrich_ema(candles, period=20)
    assert enriched[18].ema20 is None
    assert enriched[19].ema20 is not None
    assert enriched[19].ema20_previous is None
    assert enriched[20].ema20_previous is not None


def test_build_feature_excludes_current_partial_hour() -> None:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    candles = tuple(
        HourCandle(
            start_at=base + timedelta(hours=index),
            open=100 + index,
            high=101 + index,
            low=99 + index,
            close=100 + index,
            trade_count=10,
        )
        for index in range(24)
    )
    hours = enrich_ema(candles, period=20)
    touch = base + timedelta(hours=23, minutes=30)
    signal = CoreSignal(
        symbol="UNIUSDT",
        direction="Long",
        touch_at=touch,
        entry_price=120.0,
        source_row={},
    )
    outcome = PolicyOutcome(
        symbol="UNIUSDT",
        touch_at=touch,
        exit_reason="runner_stop",
        exit_move_pct=2.0,
        split_activated=True,
        core_component_pct=0.5,
        runner_component_pct=1.5,
        max_favorable_pct=5.0,
    )
    feature = build_feature(signal, outcome, hours)
    assert feature.last_closed_hour_start == base + timedelta(hours=22)
    assert feature.runner_added is True


def test_policy_outcome_runner_added_requires_positive_runner_component() -> None:
    touch = datetime(2026, 1, 1, tzinfo=UTC)
    flat = PolicyOutcome("UNIUSDT", touch, "runner_stop", 0.5, True, 0.5, 0.0, 2.0)
    added = PolicyOutcome("UNIUSDT", touch, "runner_stop", 0.6, True, 0.5, 0.1, 2.0)
    assert flat.runner_added is False
    assert added.runner_added is True
