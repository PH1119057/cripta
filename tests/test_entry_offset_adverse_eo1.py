from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.research.entry_offset_adverse_eo1 import (
    Config,
    EventResult,
    _pending_entry_price,
    analyze_path,
    analyze_signal_streaming,
    summarize,
)
from bybit_workbench.research.exit_break_even_v13 import (
    CoreSignal,
    PathSeries,
    TradeDayCache,
    build_path_series,
)
from bybit_workbench.research.flow_reversal_v1 import TradeDay


def _path(moves: tuple[float, ...], *, direction: str = "Long") -> PathSeries:
    touch = datetime(2026, 5, 18, 0, 0, tzinfo=UTC)
    signal = CoreSignal(
        symbol="UNIUSDT",
        direction=direction,  # type: ignore[arg-type]
        touch_at=touch,
        entry_price=100.0,
        source_row={},
    )
    timestamps = tuple(touch.timestamp() + index for index in range(len(moves)))
    return PathSeries(
        signal=signal,
        timestamps=timestamps,
        moves_pct=moves,
        available_until=touch + timedelta(hours=144),
        coverage_until=touch + timedelta(hours=144),
    )


def _pick(rows: list[EventResult], offset: float) -> EventResult:
    for row in rows:
        if abs(row.adverse_offset_pct - offset) < 1e-12:
            return row
    raise AssertionError("row not found")


def test_long_and_short_pending_prices_are_mirrored() -> None:
    assert _pending_entry_price("Long", 100.0, 0.10) == pytest.approx(99.9)
    assert _pending_entry_price("Long", 100.0, 0.20) == pytest.approx(99.8)
    assert _pending_entry_price("Short", 100.0, 0.10) == pytest.approx(100.1)
    assert _pending_entry_price("Short", 100.0, 0.20) == pytest.approx(100.2)


def test_minus_010_fills_when_adverse_price_is_reached_first() -> None:
    rows = analyze_path(_path((0.0, -0.05, -0.11, 0.01, 0.12, 1.02)), Config())
    row = _pick(rows, 0.10)
    assert row.fill_status == "filled"
    assert row.fill_price_ideal is not None
    assert row.fill_price_ideal == pytest.approx(99.9)


def test_minus_020_is_no_fill_when_original_target_happens_first() -> None:
    rows = analyze_path(_path((0.0, 0.4, 1.11, 0.2, -0.21)), Config())
    row = _pick(rows, 0.20)
    assert row.fill_status == "original_target_before_fill"
    assert row.offset_touched_anytime_pending_72h is True


def test_shifted_fill_uses_own_minus_one_stop() -> None:
    rows = analyze_path(_path((0.0, -0.21, -0.8, -1.21)), Config())
    row = _pick(rows, 0.20)
    assert row.fill_status == "filled"
    assert row.exit_reason == "initial_stop"
    assert row.theoretical_exit_level_pct is not None
    assert row.theoretical_exit_level_pct == pytest.approx(-1.0)


def test_activation_then_floor_is_relative_to_shifted_fill() -> None:
    # -0.20 signal fill; 0.00 signal move is about +0.2004% from the shifted fill,
    # so protection is active. A later retrace below shifted +0.10 hits the floor.
    rows = analyze_path(_path((0.0, -0.21, 0.00, -0.11)), Config())
    row = _pick(rows, 0.20)
    assert row.protection_activated is True
    assert row.exit_reason == "positive_floor"
    assert row.theoretical_exit_level_pct is not None
    assert row.theoretical_exit_level_pct == pytest.approx(0.10)


def test_target_is_plus_110_from_shifted_fill() -> None:
    rows = analyze_path(_path((0.0, -0.21, 0.0, 0.91)), Config())
    row = _pick(rows, 0.20)
    assert row.exit_reason == "target"
    assert row.theoretical_exit_level_pct is not None
    assert row.theoretical_exit_level_pct == pytest.approx(1.10)


def test_floor_is_not_claimed_as_profit_after_cost_reserve() -> None:
    rows = analyze_path(_path((0.0, -0.11, 0.1, -0.01)), Config())
    row = _pick(rows, 0.10)
    assert row.exit_reason == "positive_floor"
    assert row.net_pnl_pct_after_cost_reserve is not None
    assert row.net_pnl_pct_after_cost_reserve == pytest.approx(0.0)


def test_summary_counts_fill_and_target_per_original_signal() -> None:
    rows = analyze_path(_path((0.0, -0.21, 0.0, 0.91)), Config())
    summary = summarize(rows, "ALL9")
    record = next(item for item in summary if item["adverse_offset_pct"] == 0.20)
    assert record["signals"] == 1
    assert record["filled"] == 1
    assert record["target_plus_1p10"] == 1
    assert record["target_rate_per_signal_pct"] == pytest.approx(100.0)


def test_one_result_per_offset() -> None:
    rows = analyze_path(_path((0.0, -0.21, 0.0, 0.91)), Config())
    assert len(rows) == 3


def test_short_path_uses_plus_offset_price_and_same_directional_exit_contract() -> None:
    rows = analyze_path(_path((0.0, -0.21, 0.0, 0.91), direction="Short"), Config())
    row = _pick(rows, 0.20)
    assert row.fill_price_ideal is not None
    assert row.fill_price_ideal == pytest.approx(100.2)
    assert row.exit_reason == "target"

def _stream_fixture(
    tmp_path: Path,
    moves: tuple[float, ...],
    *,
    direction: str,
    missing_from_day: str | None = None,
) -> tuple[CoreSignal, dict[str, Path], dict[Path, TradeDay]]:
    touch = datetime(2026, 5, 18, 0, 0, tzinfo=UTC)
    signal = CoreSignal(
        symbol="UNIUSDT",
        direction=direction,  # type: ignore[arg-type]
        touch_at=touch,
        entry_price=100.0,
        source_row={},
    )
    timestamps = tuple(touch.timestamp() + index for index in range(len(moves)))
    prices = tuple(
        100.0 * (1.0 + move / 100.0)
        if direction == "Long"
        else 100.0 * (1.0 - move / 100.0)
        for move in moves
    )
    archive_by_day: dict[str, Path] = {}
    tapes: dict[Path, TradeDay] = {}
    for offset in range(6):
        day = (touch + timedelta(days=offset)).date().isoformat()
        if missing_from_day is not None and day >= missing_from_day:
            continue
        path = tmp_path / f"{day}.csv.gz"
        archive_by_day[day] = path
        tapes[path] = TradeDay(
            timestamps=timestamps if offset == 0 else (),
            prices=prices if offset == 0 else (),
        )
    return signal, archive_by_day, tapes


def _assert_event_rows_equivalent(
    expected: list[EventResult],
    actual: list[EventResult],
) -> None:
    assert len(actual) == len(expected)
    for expected_row, actual_row in zip(expected, actual, strict=True):
        expected_values = asdict(expected_row)
        actual_values = asdict(actual_row)
        assert actual_values.keys() == expected_values.keys()
        for key, expected_value in expected_values.items():
            actual_value = actual_values[key]
            if isinstance(expected_value, float):
                assert actual_value == pytest.approx(expected_value), key
            else:
                assert actual_value == expected_value, key


@pytest.mark.parametrize(
    ("direction", "moves"),
    [
        ("Long", (0.0, -0.21, 0.0, -0.11, 0.91, 1.30)),
        ("Long", (0.0, 0.40, 1.11, 0.20, -0.21)),
        ("Long", (0.0, -0.21, -0.80, -1.21)),
        ("Long", (0.0, -0.21, 0.0, 0.91)),
        ("Long", (0.0, 0.02, 0.04, 0.03)),
        ("Short", (0.0, -0.21, 0.0, -0.11, 0.91, 1.30)),
        ("Short", (0.0, 0.40, 1.11, 0.20, -0.21)),
    ],
)
def test_streaming_replay_matches_materialized_contract(
    tmp_path: Path,
    direction: str,
    moves: tuple[float, ...],
) -> None:
    config = Config()
    signal, archive_by_day, tapes = _stream_fixture(
        tmp_path,
        moves,
        direction=direction,
    )

    def loader(path: Path) -> TradeDay:
        return tapes[path]

    materialized = build_path_series(
        signal,
        archive_by_day,
        horizon_hours=config.max_path_hours,
        cache=TradeDayCache(max_days=10, loader=loader),
    )
    expected = analyze_path(materialized, config)
    actual, missing_days = analyze_signal_streaming(
        signal,
        archive_by_day,
        config,
        cache=TradeDayCache(max_days=10, loader=loader),
    )

    assert missing_days == materialized.missing_archive_days
    _assert_event_rows_equivalent(expected, actual)


def test_streaming_replay_matches_materialized_incomplete_coverage(tmp_path: Path) -> None:
    config = Config()
    signal, archive_by_day, tapes = _stream_fixture(
        tmp_path,
        (0.0, -0.21, 0.02, 0.03),
        direction="Long",
        missing_from_day="2026-05-20",
    )

    def loader(path: Path) -> TradeDay:
        return tapes[path]

    materialized = build_path_series(
        signal,
        archive_by_day,
        horizon_hours=config.max_path_hours,
        cache=TradeDayCache(max_days=10, loader=loader),
    )
    expected = analyze_path(materialized, config)
    actual, missing_days = analyze_signal_streaming(
        signal,
        archive_by_day,
        config,
        cache=TradeDayCache(max_days=10, loader=loader),
    )

    assert missing_days == materialized.missing_archive_days
    _assert_event_rows_equivalent(expected, actual)


def test_streaming_replay_matches_materialized_when_first_trade_is_after_pending_window(
    tmp_path: Path,
) -> None:
    config = Config()
    touch = datetime(2026, 5, 18, 0, 0, tzinfo=UTC)
    signal = CoreSignal(
        symbol="UNIUSDT",
        direction="Long",
        touch_at=touch,
        entry_price=100.0,
        source_row={},
    )
    late_timestamp = (touch + timedelta(hours=73)).timestamp()
    archive_by_day: dict[str, Path] = {}
    tapes: dict[Path, TradeDay] = {}
    for offset in range(6):
        day_at = touch + timedelta(days=offset)
        day = day_at.date().isoformat()
        path = tmp_path / f"{day}.csv.gz"
        archive_by_day[day] = path
        if day == (touch + timedelta(hours=73)).date().isoformat():
            tapes[path] = TradeDay((late_timestamp,), (100.0,))
        else:
            tapes[path] = TradeDay((), ())

    def loader(path: Path) -> TradeDay:
        return tapes[path]

    materialized = build_path_series(
        signal,
        archive_by_day,
        horizon_hours=config.max_path_hours,
        cache=TradeDayCache(max_days=10, loader=loader),
    )
    expected = analyze_path(materialized, config)
    actual, missing_days = analyze_signal_streaming(
        signal,
        archive_by_day,
        config,
        cache=TradeDayCache(max_days=10, loader=loader),
    )

    assert missing_days == materialized.missing_archive_days
    _assert_event_rows_equivalent(expected, actual)
