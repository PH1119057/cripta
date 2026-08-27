from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bybit_workbench.research.multi_touch_sr_p45 import (
    Candle,
    CoreSignal,
    FeatureThreshold,
    Zone,
    ZoneDetector,
    _distance_to_band,
    _outcome_metrics,
    build_feature_row,
    classify_quartile,
    resolve_frozen_dataset_dir,
    segment_for,
    wilder_atr,
)


def _candles(values: list[tuple[float, float, float, float]]) -> tuple[Candle, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[Candle] = []
    for index, (open_, high, low, close) in enumerate(values):
        opened = start + timedelta(minutes=15 * index)
        rows.append(
            Candle(
                opened_at=opened,
                closed_at=opened + timedelta(minutes=15),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=1.0,
            )
        )
    return tuple(rows)


def test_wilder_atr_is_causal_and_positive() -> None:
    candles = _candles([(10, 11, 9, 10)] * 8)
    atr = wilder_atr(candles, period=3)
    assert atr[:2] == (None, None)
    assert atr[2] == 2.0
    assert all(value == 2.0 for value in atr[2:])


def test_pivot_zone_not_visible_before_confirmation() -> None:
    values = [
        (10, 11, 9, 10),
        (10, 11, 9, 10),
        (10, 11, 8, 10),
        (10, 11, 9, 10),
        (10, 11, 9, 10),
        (10, 11, 9, 10),
    ]
    detector = ZoneDetector("X", _candles(values), pivot_span=2, atr_period=2)
    detector.process_one(0)
    detector.process_one(1)
    detector.process_one(2)
    detector.process_one(3)
    assert detector.zones == []
    detector.process_one(4)
    assert len(detector.zones) == 1
    assert detector.zones[0].origin_role == "support"


def test_close_two_bars_through_zone_flips_role() -> None:
    candles = _candles([(10, 11, 9, 10)] * 4)
    detector = ZoneDetector("X", candles, pivot_span=1, atr_period=2)
    zone = Zone(
        zone_id=1,
        center=10.0,
        half_width=0.5,
        origin_at=candles[0].closed_at,
        confirmed_at=candles[0].closed_at,
        origin_role="support",
        role="support",
        source_pivots=1,
        support_pivots=1,
        resistance_pivots=0,
    )
    detector.zones.append(zone)
    below1 = Candle(candles[1].opened_at, candles[1].closed_at, 9.4, 9.5, 9.0, 9.3, 1)
    below2 = Candle(candles[2].opened_at, candles[2].closed_at, 9.3, 9.4, 8.9, 9.2, 1)
    detector._update_zone(zone, below1, 1.0)
    assert zone.role == "support"
    detector._update_zone(zone, below2, 1.0)
    assert zone.role == "resistance"
    assert zone.role_flips == 1


def test_single_close_beyond_then_reclaim_counts_false_break() -> None:
    candles = _candles([(10, 11, 9, 10)] * 3)
    detector = ZoneDetector("X", candles, pivot_span=1, atr_period=2)
    zone = Zone(
        zone_id=1,
        center=10.0,
        half_width=0.5,
        origin_at=candles[0].closed_at,
        confirmed_at=candles[0].closed_at,
        origin_role="support",
        role="support",
        source_pivots=1,
        support_pivots=1,
        resistance_pivots=0,
    )
    detector.zones.append(zone)
    below = Candle(candles[1].opened_at, candles[1].closed_at, 9.5, 9.6, 9.0, 9.3, 1)
    reclaim = Candle(candles[2].opened_at, candles[2].closed_at, 9.4, 10.2, 9.3, 10.0, 1)
    detector._update_zone(zone, below, 1.0)
    detector._update_zone(zone, reclaim, 1.0)
    assert zone.role == "support"
    assert zone.false_breaks == 1


def test_retest_requires_rearm_excursion() -> None:
    candles = _candles([(10, 11, 9, 10)] * 4)
    detector = ZoneDetector("X", candles, pivot_span=1, atr_period=2)
    zone = Zone(
        zone_id=1,
        center=10.0,
        half_width=0.5,
        origin_at=candles[0].closed_at,
        confirmed_at=candles[0].closed_at,
        origin_role="support",
        role="support",
        source_pivots=1,
        support_pivots=1,
        resistance_pivots=0,
        armed_for_retest=True,
    )
    detector.zones.append(zone)
    touch = Candle(candles[0].opened_at, candles[0].closed_at, 10.5, 10.6, 9.9, 10.4, 1)
    still_near = Candle(candles[1].opened_at, candles[1].closed_at, 10.4, 10.7, 10.0, 10.5, 1)
    away = Candle(candles[2].opened_at, candles[2].closed_at, 11.2, 11.7, 11.0, 11.6, 1)
    touch_again = Candle(candles[3].opened_at, candles[3].closed_at, 10.8, 10.9, 9.9, 10.4, 1)
    detector._update_zone(zone, touch, 1.0)
    detector._update_zone(zone, still_near, 1.0)
    assert zone.retest_count == 1
    detector._update_zone(zone, away, 1.0)
    assert zone.armed_for_retest is True
    detector._update_zone(zone, touch_again, 1.0)
    assert zone.retest_count == 2


def test_nearest_aligned_zone_respects_direction() -> None:
    candles = _candles([(10, 11, 9, 10)] * 3)
    detector = ZoneDetector("X", candles, pivot_span=1, atr_period=2)
    support = Zone(
        1, 9.0, 0.2, candles[0].closed_at, candles[0].closed_at,
        "support", "support", 1, 1, 0,
    )
    resistance = Zone(
        2, 11.0, 0.2, candles[0].closed_at, candles[0].closed_at,
        "resistance", "resistance", 1, 0, 1,
    )
    detector.zones.extend([support, resistance])
    assert detector.nearest_aligned_zone("Long", 10.8) is support
    assert detector.nearest_aligned_zone("Short", 9.2) is resistance


def test_distance_to_band_zero_inside() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    zone = Zone(1, 10.0, 0.5, now, now, "support", "support", 1, 1, 0)
    assert _distance_to_band(10.0, zone) == 0.0
    assert _distance_to_band(11.0, zone) == 0.5


def test_segment_boundaries() -> None:
    start = datetime(2026, 5, 18, tzinfo=UTC)
    assert segment_for(start + timedelta(days=29), start, 30) == "S1"
    assert segment_for(start + timedelta(days=30), start, 30) == "S2"
    assert segment_for(start + timedelta(days=60), start, 30) == "S3"


def test_quartile_classification() -> None:
    threshold = FeatureThreshold("X", "f", 10, 1.0, 2.0, 3.0)
    assert classify_quartile(0.5, threshold) == "Q1"
    assert classify_quartile(1.5, threshold) == "Q2"
    assert classify_quartile(2.5, threshold) == "Q3"
    assert classify_quartile(3.5, threshold) == "Q4"


def test_outcome_metrics_count_neither_in_all_denominator() -> None:
    start = datetime(2026, 5, 18, tzinfo=UTC)
    candles = _candles([(10, 11, 9, 10)] * 20)
    detector = ZoneDetector("X", candles, pivot_span=2, atr_period=3)
    signals = [
        CoreSignal("X", "Long", start, 10, "favorable_first", "favorable_first"),
        CoreSignal("X", "Long", start, 10, "adverse_first", "adverse_first"),
        CoreSignal("X", "Long", start, 10, "neither", "neither"),
    ]
    rows = [
        build_feature_row(
            signal,
            detector=detector,
            start=start,
            calibration_days=30,
            p44_values={},
            p44_q25={},
        )
        for signal in signals
    ]
    metrics = _outcome_metrics(rows)
    assert metrics["sample"] == 3
    assert metrics["win_05_all_pct"] == 100 / 3
    assert metrics["win_05_decisive_pct"] == 50.0


def test_dataset_resolver_follows_comparison_dataset_pointer(tmp_path: Path) -> None:
    start = datetime(2026, 5, 18, tzinfo=UTC)
    end = datetime(2026, 8, 16, tzinfo=UTC)
    asset = tmp_path / "reports" / "cross_asset_validation" / "UNIUSDT_20260518_20260816"
    p30 = asset / "p30"
    p30.mkdir(parents=True)
    dataset = tmp_path / "reports" / "entry_research_v3" / "UNIUSDT_OLD" / "dataset"
    dataset.mkdir(parents=True)
    (dataset / "trade_15m.csv").write_text("x\n", encoding="utf-8")
    (p30 / "comparison.json").write_text(
        json.dumps(
            {
                "evaluation_start": start.isoformat(),
                "evaluation_end": end.isoformat(),
                "dataset_dir": str(dataset),
            }
        ),
        encoding="utf-8",
    )
    resolved, source = resolve_frozen_dataset_dir(
        tmp_path,
        symbol="UNIUSDT",
        start=start,
        end=end,
    )
    assert resolved == dataset
    assert source == "p30_comparison_dataset_dir"


def test_candle_csv_schema_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "trade_15m.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("opened_at", "closed_at", "open", "high", "low", "close", "volume"))
        start = datetime(2026, 1, 1, tzinfo=UTC)
        for index in range(220):
            opened = start + timedelta(minutes=15 * index)
            writer.writerow(
                (
                    opened.isoformat(),
                    (opened + timedelta(minutes=15)).isoformat(),
                    10, 11, 9, 10, 1,
                )
            )
    from bybit_workbench.research.multi_touch_sr_p45 import load_candles

    rows = load_candles(path)
    assert len(rows) == 220
    assert rows[0].close == 10.0
