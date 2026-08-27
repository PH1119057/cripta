from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from bybit_workbench.research.exit_break_even_v13 import CoreSignal, TradeDayCache
from bybit_workbench.research.flow_reversal_v1 import TradeDay
from bybit_workbench.research.mtf_entry import Direction
from bybit_workbench.research.untouched_minus1_plus110_v26 import (
    Config,
    Result,
    scan_signal,
    summarize,
)


def _signal(direction: Direction = "Long") -> CoreSignal:
    return CoreSignal(
        symbol="UNIUSDT",
        direction=direction,
        touch_at=datetime(2026, 5, 18, 0, 0, tzinfo=UTC),
        entry_price=100.0,
        source_row={},
    )


def _cache(timestamps: tuple[float, ...], prices: tuple[float, ...]) -> TradeDayCache:
    tape = TradeDay(timestamps=timestamps, prices=prices)
    return TradeDayCache(max_days=2, loader=lambda _: tape)


def test_long_target_first() -> None:
    start = _signal().touch_at.timestamp()
    archives = {"2026-05-18": Path("day.zip")}
    result = scan_signal(
        _signal(),
        archives,
        _cache((start + 1, start + 2), (100.2, 101.11)),
        Config(horizon_hours=1),
    )
    assert result.outcome == "reached_plus_1p10"
    assert result.seconds_to_event == pytest.approx(2.0)


def test_long_stop_first() -> None:
    start = _signal().touch_at.timestamp()
    archives = {"2026-05-18": Path("day.zip")}
    result = scan_signal(
        _signal(),
        archives,
        _cache((start + 1, start + 2), (99.5, 98.99)),
        Config(horizon_hours=1),
    )
    assert result.outcome == "hit_minus_1p00"


def test_short_target_first() -> None:
    signal = _signal("Short")
    start = signal.touch_at.timestamp()
    archives = {"2026-05-18": Path("day.zip")}
    result = scan_signal(
        signal,
        archives,
        _cache((start + 1, start + 2), (99.8, 98.89)),
        Config(horizon_hours=1),
    )
    assert result.outcome == "reached_plus_1p10"


def test_internal_missing_day_fails_closed() -> None:
    signal = _signal()
    archives = {
        "2026-05-18": Path("day1.zip"),
        "2026-05-20": Path("day3.zip"),
    }
    start = signal.touch_at.timestamp()
    with pytest.raises(FileNotFoundError, match="internal trade-day gap"):
        scan_signal(
            signal,
            archives,
            _cache((start + 1,), (100.1,)),
            Config(horizon_hours=72),
        )


def test_summary_100_margin_10x_economics() -> None:
    now = datetime(2026, 5, 18, tzinfo=UTC)
    results = [
        Result("UNIUSDT", "Long", now, 100.0, "reached_plus_1p10", now, 1.1, 1.0, True),
        Result("UNIUSDT", "Long", now, 100.0, "hit_minus_1p00", now, -1.0, 1.0, True),
    ]
    summary = summarize(results, "UNIUSDT", Config())
    assert summary["illustrative_notional_usd"] == pytest.approx(1000.0)
    assert summary["illustrative_win_net_usd"] == pytest.approx(10.0)
    assert summary["illustrative_loss_net_usd"] == pytest.approx(-11.0)
    assert summary["illustrative_aggregate_net_usd"] == pytest.approx(-1.0)
    assert summary["resolved_win_rate_pct"] == pytest.approx(50.0)
