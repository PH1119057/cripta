from datetime import UTC, datetime
from pathlib import Path

import pytest

from bybit_workbench.research.early_protection_differential_v22 import (
    BASELINE_RUNNER,
    BASELINE_SIMPLE,
    BaselineRow,
    QuickConfig,
    _corrected_exit,
    scan_corrected_floor,
)
from bybit_workbench.research.exit_break_even_v13 import CoreSignal, TradeDay, TradeDayCache


def _signal() -> CoreSignal:
    return CoreSignal(
        symbol="TESTUSDT",
        direction="Long",
        touch_at=datetime(2026, 8, 1, tzinfo=UTC),
        entry_price=100.0,
        source_row={},
    )


def _cache(timestamps: tuple[float, ...], prices: tuple[float, ...]) -> TradeDayCache:
    day = TradeDay(timestamps=timestamps, prices=prices)
    return TradeDayCache(max_days=1, loader=lambda _path: day)


def _archive() -> dict[str, Path]:
    return {"2026-08-01": Path("synthetic.csv.gz")}


def test_corrected_floor_stops_old_runner_candidate_before_1p10() -> None:
    signal = _signal()
    start = signal.touch_at.timestamp()
    timestamps = (start, start + 1, start + 2, start + 3)
    prices = (100.0, 100.12, 100.08, 101.20)
    outcome, activation_at, event_at, delay, same_timestamp = scan_corrected_floor(
        signal,
        _archive(),
        _cache(timestamps, prices),
        QuickConfig(),
    )
    assert outcome == "new_floor_stop"
    assert activation_at == datetime.fromtimestamp(start + 1, UTC)
    assert event_at == datetime.fromtimestamp(start + 2, UTC)
    assert delay == pytest.approx(1.0)
    assert same_timestamp is False


def test_corrected_floor_retains_candidate_when_1p10_arrives_first() -> None:
    signal = _signal()
    start = signal.touch_at.timestamp()
    timestamps = (start, start + 1, start + 2, start + 3)
    prices = (100.0, 100.12, 100.20, 101.20)
    outcome, _activation_at, _event_at, delay, same_timestamp = scan_corrected_floor(
        signal,
        _archive(),
        _cache(timestamps, prices),
        QuickConfig(),
    )
    assert outcome == "reached_1p10"
    assert delay == pytest.approx(2.0)
    assert same_timestamp is False


def test_same_timestamp_following_trade_is_flagged() -> None:
    signal = _signal()
    start = signal.touch_at.timestamp()
    timestamps = (start, start + 1, start + 1)
    prices = (100.0, 100.12, 100.09)
    outcome, _activation_at, _event_at, delay, same_timestamp = scan_corrected_floor(
        signal,
        _archive(),
        _cache(timestamps, prices),
        QuickConfig(),
    )
    assert outcome == "new_floor_stop"
    assert delay == pytest.approx(0.0)
    assert same_timestamp is True


def test_data_end_is_preserved_when_neither_event_occurs() -> None:
    signal = _signal()
    start = signal.touch_at.timestamp()
    timestamps = (start, start + 1, start + 2)
    prices = (100.0, 100.12, 100.20)
    outcome, activation_at, event_at, delay, same_timestamp = scan_corrected_floor(
        signal,
        _archive(),
        _cache(timestamps, prices),
        QuickConfig(),
    )
    assert outcome == "data_end"
    assert activation_at == datetime.fromtimestamp(start + 1, UTC)
    assert event_at is None
    assert delay is None
    assert same_timestamp is False


def test_corrected_exit_reuses_downstream_runner_only_for_survivor() -> None:
    touch_at = datetime(2026, 8, 1, tzinfo=UTC)
    simple = BaselineRow("TESTUSDT", touch_at, BASELINE_SIMPLE, "core_take", 1.0)
    runner = BaselineRow("TESTUSDT", touch_at, BASELINE_RUNNER, "runner_stop", 2.5)
    stopped = _corrected_exit(simple, runner, "new_floor_stop", QuickConfig())
    survived = _corrected_exit(simple, runner, "reached_1p10", QuickConfig())
    assert stopped == pytest.approx((0.10, 0.10))
    assert survived == pytest.approx((1.0, 2.5))


def test_old_early_be_is_repriced_to_corrected_floor_without_rescan() -> None:
    touch_at = datetime(2026, 8, 1, tzinfo=UTC)
    simple = BaselineRow("TESTUSDT", touch_at, BASELINE_SIMPLE, "early_be", 0.0)
    runner = BaselineRow("TESTUSDT", touch_at, BASELINE_RUNNER, "early_be", 0.0)
    corrected = _corrected_exit(simple, runner, None, QuickConfig())
    assert corrected == pytest.approx((0.10, 0.10))
