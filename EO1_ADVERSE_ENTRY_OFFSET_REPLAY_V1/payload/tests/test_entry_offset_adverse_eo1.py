from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.research.entry_offset_adverse_eo1 import (
    Config,
    _pending_entry_price,
    analyze_path,
    summarize,
)
from bybit_workbench.research.exit_break_even_v13 import CoreSignal, PathSeries


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


def _pick(rows: list[object], offset: float) -> object:
    for row in rows:
        if abs(float(getattr(row, "adverse_offset_pct")) - offset) < 1e-12:
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
    assert getattr(row, "fill_status") == "filled"
    assert float(getattr(row, "fill_price_ideal")) == pytest.approx(99.9)


def test_minus_020_is_no_fill_when_original_target_happens_first() -> None:
    rows = analyze_path(_path((0.0, 0.4, 1.11, 0.2, -0.21)), Config())
    row = _pick(rows, 0.20)
    assert getattr(row, "fill_status") == "original_target_before_fill"
    assert getattr(row, "offset_touched_anytime_pending_72h") is True


def test_shifted_fill_uses_own_minus_one_stop() -> None:
    rows = analyze_path(_path((0.0, -0.21, -0.8, -1.21)), Config())
    row = _pick(rows, 0.20)
    assert getattr(row, "fill_status") == "filled"
    assert getattr(row, "exit_reason") == "initial_stop"
    assert float(getattr(row, "theoretical_exit_level_pct")) == pytest.approx(-1.0)


def test_activation_then_floor_is_relative_to_shifted_fill() -> None:
    # -0.20 signal fill; 0.00 signal move is about +0.2004% from the shifted fill,
    # so protection is active. A later retrace below shifted +0.10 hits the floor.
    rows = analyze_path(_path((0.0, -0.21, 0.00, -0.11)), Config())
    row = _pick(rows, 0.20)
    assert getattr(row, "protection_activated") is True
    assert getattr(row, "exit_reason") == "positive_floor"
    assert float(getattr(row, "theoretical_exit_level_pct")) == pytest.approx(0.10)


def test_target_is_plus_110_from_shifted_fill() -> None:
    rows = analyze_path(_path((0.0, -0.21, 0.0, 0.91)), Config())
    row = _pick(rows, 0.20)
    assert getattr(row, "exit_reason") == "target"
    assert float(getattr(row, "theoretical_exit_level_pct")) == pytest.approx(1.10)


def test_floor_is_not_claimed_as_profit_after_cost_reserve() -> None:
    rows = analyze_path(_path((0.0, -0.11, 0.1, -0.01)), Config())
    row = _pick(rows, 0.10)
    assert getattr(row, "exit_reason") == "positive_floor"
    assert float(getattr(row, "net_pnl_pct_after_cost_reserve")) == pytest.approx(0.0)


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
    assert float(getattr(row, "fill_price_ideal")) == pytest.approx(100.2)
    assert getattr(row, "exit_reason") == "target"
