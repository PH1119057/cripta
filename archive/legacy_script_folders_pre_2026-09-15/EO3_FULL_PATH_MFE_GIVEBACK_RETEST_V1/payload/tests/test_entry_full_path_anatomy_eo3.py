from __future__ import annotations

from array import array
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from bybit_workbench.research.entry_full_path_anatomy_eo3 import (
    MinuteSeries,
    SourceSignal,
    _aggregate_minute_day,
    analyse_trade,
    summarize,
)
from bybit_workbench.research.entry_offset_no_floor_eo2 import SourceFill
from bybit_workbench.research.exit_break_even_v13 import TradeDayCache
from bybit_workbench.research.flow_reversal_v1 import TradeDay

BASE = datetime(2026, 5, 18, 10, 0, tzinfo=UTC)


def _fill(direction: str = "Long") -> SourceFill:
    assert direction in {"Long", "Short"}
    return SourceFill(
        symbol="UNIUSDT",
        direction=direction,  # type: ignore[arg-type]
        touch_at=BASE,
        fill_at=BASE,
        fill_price=100.0,
        original_entry_price=100.2 if direction == "Long" else 99.8,
    )


def _raw_cache(path: Path, timestamps: list[float], prices: list[float]) -> TradeDayCache:
    tape = TradeDay(  # type: ignore[arg-type]
        array("d", timestamps),
        array("d", prices),
    )

    def loader(requested: Path) -> TradeDay:
        assert requested == path
        return tape

    return TradeDayCache(max_days=2, loader=loader)


def _signals(extra: list[tuple[int, bool]] | None = None) -> tuple[SourceSignal, ...]:
    rows = [SourceSignal("UNIUSDT", "Long", BASE, True, BASE)]
    for minutes, filled in extra or []:
        at = BASE + timedelta(minutes=minutes)
        rows.append(SourceSignal("UNIUSDT", "Long", at, filled, at if filled else None))
    return tuple(rows)


def test_aggregate_minute_day_exact_ohlc() -> None:
    ts = BASE.timestamp()
    tape = TradeDay(  # type: ignore[arg-type]
        array("d", [ts, ts + 10, ts + 50, ts + 60, ts + 80]),
        array("d", [100.0, 101.0, 99.0, 100.5, 102.0]),
    )
    day = _aggregate_minute_day(tape)
    assert day.minute_ts.tolist() == [ts, ts + 60]
    assert day.high.tolist() == [101.0, 102.0]
    assert day.low.tolist() == [99.0, 100.5]
    assert day.close.tolist() == [99.0, 102.0]


def test_long_path_is_not_truncated_at_plus_1p10_and_can_later_stop() -> None:
    ts = BASE.timestamp()
    minute_ts = np.asarray([ts + 60 * i for i in range(6)], dtype=np.float64)
    series = MinuteSeries(
        minute_ts,
        np.asarray([100.4, 101.2, 105.2, 103.0, 100.2, 99.4]),
        np.asarray([99.8, 100.1, 101.0, 99.9, 99.3, 98.8]),
        np.asarray([100.3, 100.8, 104.0, 100.0, 99.5, 98.8]),
    )
    archive = Path("2026-05-18.csv.gz")
    raw_ts = [ts, ts + 60 * 5 + 5, ts + 60 * 5 + 20]
    raw_prices = [100.0, 99.4, 98.8]
    cache = _raw_cache(archive, raw_ts, raw_prices)
    result = analyse_trade(
        _fill("Long"),
        series,
        {"2026-05-18": archive},
        cache,
        _signals(),
    )
    anatomy, milestones = result[0], result[1]
    assert anatomy.end_reason == "initial_stop"
    assert anatomy.mfe_pct == pytest.approx(5.2)
    assert anatomy.max_milestone_pct == pytest.approx(5.0)
    plus_1p10 = next(row for row in milestones if row.milestone_pct == 1.10)
    assert plus_1p10.reached is True
    assert plus_1p10.hard_stop_after is True


def test_return_to_entry_and_giveback_are_observed_after_activation() -> None:
    ts = BASE.timestamp()
    minute_ts = np.asarray([ts + 60 * i for i in range(5)], dtype=np.float64)
    series = MinuteSeries(
        minute_ts,
        np.asarray([100.2, 100.8, 101.4, 101.0, 100.5]),
        np.asarray([99.9, 100.1, 100.5, 99.9, 99.8]),
        np.asarray([100.1, 100.6, 101.2, 100.0, 100.2]),
    )
    archive = Path("2026-05-18.csv.gz")
    cache = _raw_cache(archive, [ts], [100.0])
    anatomy, _, activations, givebacks, _, _ = analyse_trade(
        _fill("Long"),
        series,
        {"2026-05-18": archive},
        cache,
        _signals(),
    )
    assert anatomy.end_reason == "data_end_open"
    assert anatomy.returned_to_entry_after_plus_0p50 is True
    row = next(item for item in activations if item.activation_pct == 0.50)
    assert row.returned_to_entry_after_activation is True
    assert row.max_close_giveback_pct >= 1.0
    assert any(item.activation_pct == 0.50 and item.giveback_pct == 0.50 for item in givebacks)


def test_short_direction_is_symmetric() -> None:
    ts = BASE.timestamp()
    minute_ts = np.asarray([ts + 60 * i for i in range(4)], dtype=np.float64)
    series = MinuteSeries(
        minute_ts,
        np.asarray([100.1, 99.8, 99.5, 101.2]),
        np.asarray([99.8, 98.8, 97.0, 100.5]),
        np.asarray([99.9, 99.0, 97.5, 101.2]),
    )
    archive = Path("2026-05-18.csv.gz")
    cache = _raw_cache(
        archive,
        [ts, ts + 60 * 3 + 5, ts + 60 * 3 + 20],
        [100.0, 100.5, 101.2],
    )
    anatomy, _, _, _, _, _ = analyse_trade(
        _fill("Short"),
        series,
        {"2026-05-18": archive},
        cache,
        _signals(),
    )
    assert anatomy.end_reason == "initial_stop"
    assert anatomy.mfe_pct == pytest.approx(3.0)
    assert anatomy.max_milestone_pct == pytest.approx(3.0)


def test_overlap_counts_all_source_signals_and_filled_subset() -> None:
    ts = BASE.timestamp()
    minute_ts = np.asarray([ts + 60 * i for i in range(6)], dtype=np.float64)
    series = MinuteSeries(
        minute_ts,
        np.asarray([100.1, 100.2, 100.2, 100.2, 100.2, 98.8]),
        np.asarray([99.9, 99.8, 99.8, 99.8, 99.8, 98.8]),
        np.asarray([100.0, 100.0, 100.0, 100.0, 100.0, 98.8]),
    )
    archive = Path("2026-05-18.csv.gz")
    cache = _raw_cache(
        archive,
        [ts, ts + 60 * 5 + 10],
        [100.0, 98.8],
    )
    anatomy, _, _, _, _, overlaps = analyse_trade(
        _fill("Long"),
        series,
        {"2026-05-18": archive},
        cache,
        _signals([(2, True), (3, False), (10, True)]),
    )
    assert anatomy.overlapping_signals == 2
    assert anatomy.overlapping_0p20_fills == 1
    assert len(overlaps) == 2


def test_summary_uses_all_trades_and_milestones() -> None:
    ts = BASE.timestamp()
    minute_ts = np.asarray([ts, ts + 60, ts + 120], dtype=np.float64)
    series = MinuteSeries(
        minute_ts,
        np.asarray([100.2, 101.2, 98.8]),
        np.asarray([99.9, 100.0, 98.8]),
        np.asarray([100.1, 101.0, 98.8]),
    )
    archive = Path("2026-05-18.csv.gz")
    cache = _raw_cache(archive, [ts, ts + 125], [100.0, 98.8])
    anatomy, milestones, _, _, _, _ = analyse_trade(
        _fill("Long"),
        series,
        {"2026-05-18": archive},
        cache,
        _signals(),
    )
    summary = summarize([anatomy], milestones)
    assert summary["filled_trades"] == 1
    assert summary["hard_stop_minus_1p00"] == 1
    assert summary["milestones"]["1p10"]["reached"] == 1


def test_post_stop_research_does_not_change_trade_end() -> None:
    ts = BASE.timestamp()
    minute_ts = np.asarray([ts + 60 * i for i in range(6)], dtype=np.float64)
    series = MinuteSeries(
        minute_ts,
        np.asarray([100.1, 98.8, 100.5, 101.2, 101.5, 101.0]),
        np.asarray([99.9, 98.8, 99.5, 100.0, 100.5, 100.4]),
        np.asarray([100.0, 98.8, 100.2, 101.1, 101.2, 100.8]),
    )
    archive = Path("2026-05-18.csv.gz")
    cache = _raw_cache(archive, [ts, ts + 65], [100.0, 98.8])
    anatomy, _, _, _, _, _ = analyse_trade(
        _fill("Long"),
        series,
        {"2026-05-18": archive},
        cache,
        _signals(),
    )
    assert anatomy.end_reason == "initial_stop"
    assert anatomy.post_stop_72h_reached_plus_1p10 is True
