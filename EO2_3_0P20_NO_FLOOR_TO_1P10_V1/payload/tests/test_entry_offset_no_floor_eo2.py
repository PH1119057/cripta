from __future__ import annotations

from array import array
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bybit_workbench.research.entry_offset_no_floor_eo2 import (
    INITIAL_STOP_PCT,
    TARGET_PCT,
    SourceFill,
    _duration_buckets,
    _first_hit_in_segment,
    _move_pct,
    _target_stop_prices,
    replay_fill,
    summarize,
)
from bybit_workbench.research.exit_break_even_v13 import TradeDayCache
from bybit_workbench.research.flow_reversal_v1 import TradeDay

BASE = datetime(2026, 5, 18, 10, 0, tzinfo=UTC)


def _fill(direction: str = "Long", price: float = 100.0) -> SourceFill:
    assert direction in {"Long", "Short"}
    return SourceFill(
        symbol="UNIUSDT",
        direction=direction,  # type: ignore[arg-type]
        touch_at=BASE,
        fill_at=BASE,
        fill_price=price,
        original_entry_price=100.2 if direction == "Long" else 99.8,
    )


def _cache(timestamps: list[float], prices: list[float]) -> tuple[TradeDayCache, dict[str, Path]]:
    fake = Path("2026-05-18.csv.gz")
    tape = TradeDay(array("d", timestamps), array("d", prices))  # type: ignore[arg-type]

    def loader(path: Path) -> TradeDay:
        assert path == fake
        return tape

    return TradeDayCache(max_days=2, loader=loader), {"2026-05-18": fake}


def test_target_stop_prices_long_and_short() -> None:
    long_target, long_stop = _target_stop_prices(_fill("Long"))
    short_target, short_stop = _target_stop_prices(_fill("Short"))
    assert long_target == pytest.approx(101.1)
    assert long_stop == pytest.approx(99.0)
    assert short_target == pytest.approx(98.9)
    assert short_stop == pytest.approx(101.0)


def test_directional_move_pct_is_symmetric_definition() -> None:
    assert _move_pct("Long", 100.0, 101.1) == pytest.approx(TARGET_PCT)
    assert _move_pct("Long", 100.0, 99.0) == pytest.approx(-INITIAL_STOP_PCT)
    assert _move_pct("Short", 100.0, 98.9) == pytest.approx(TARGET_PCT)
    assert _move_pct("Short", 100.0, 101.0) == pytest.approx(-INITIAL_STOP_PCT)


def test_first_hit_respects_tick_order() -> None:
    import numpy as np

    prices = np.asarray([100.2, 98.8, 101.2], dtype=np.float64)
    hit = _first_hit_in_segment(
        prices,
        direction="Long",
        target_price=101.1,
        stop_price=99.0,
    )
    assert hit == (1, "initial_stop")


def test_replay_long_target_first() -> None:
    ts = BASE.timestamp()
    cache, archives = _cache([ts, ts + 60, ts + 120], [100.0, 100.5, 101.2])
    result = replay_fill(_fill("Long"), archives, cache=cache, data_end=BASE.replace(hour=11))
    assert result.outcome == "target"
    assert result.duration_seconds == pytest.approx(120.0)
    assert result.pnl_usd_100_margin_10x == pytest.approx(10.0)
    assert result.mfe_until_exit_or_data_end_pct == pytest.approx(1.2)
    assert result.mae_until_exit_or_data_end_pct == pytest.approx(0.0)


def test_replay_long_stop_first() -> None:
    ts = BASE.timestamp()
    cache, archives = _cache([ts, ts + 60, ts + 120], [100.0, 99.4, 98.9])
    result = replay_fill(_fill("Long"), archives, cache=cache, data_end=BASE.replace(hour=11))
    assert result.outcome == "initial_stop"
    assert result.duration_seconds == pytest.approx(120.0)
    assert result.pnl_usd_100_margin_10x == pytest.approx(-11.0)


def test_replay_short_target_first() -> None:
    ts = BASE.timestamp()
    cache, archives = _cache([ts, ts + 30, ts + 90], [100.0, 99.5, 98.8])
    result = replay_fill(_fill("Short"), archives, cache=cache, data_end=BASE.replace(hour=11))
    assert result.outcome == "target"
    assert result.duration_seconds == pytest.approx(90.0)
    assert result.pnl_usd_100_margin_10x == pytest.approx(10.0)
    assert result.mfe_until_exit_or_data_end_pct == pytest.approx(1.2)
    assert result.mae_until_exit_or_data_end_pct == pytest.approx(0.0)


def test_replay_short_stop_first() -> None:
    ts = BASE.timestamp()
    cache, archives = _cache([ts, ts + 30, ts + 90], [100.0, 100.5, 101.1])
    result = replay_fill(_fill("Short"), archives, cache=cache, data_end=BASE.replace(hour=11))
    assert result.outcome == "initial_stop"
    assert result.pnl_usd_100_margin_10x == pytest.approx(-11.0)
    assert result.mae_until_exit_or_data_end_pct == pytest.approx(-1.1)


def test_fill_tick_can_exit_immediately() -> None:
    ts = BASE.timestamp()
    cache, archives = _cache([ts], [98.8])
    result = replay_fill(_fill("Long"), archives, cache=cache, data_end=BASE.replace(hour=11))
    assert result.outcome == "initial_stop"
    assert result.duration_seconds == 0.0


def test_summary_economics_and_break_even() -> None:
    from dataclasses import replace

    ts = BASE.timestamp()
    cache_t, archives_t = _cache([ts, ts + 60], [100.0, 101.2])
    target = replay_fill(_fill("Long"), archives_t, cache=cache_t, data_end=BASE.replace(hour=11))
    cache_s, archives_s = _cache([ts, ts + 60], [100.0, 98.8])
    stop = replay_fill(_fill("Long"), archives_s, cache=cache_s, data_end=BASE.replace(hour=11))
    second_target = replace(target, touch_at=BASE.replace(minute=1))
    summary = summarize([target, second_target, stop])
    assert summary["target_plus_1p10"] == 2
    assert summary["initial_stop_minus_1p00"] == 1
    assert summary["aggregate_net_usd_fixed_100_margin_10x_resolved"] == pytest.approx(9.0)
    assert summary["ev_usd_per_resolved_trade"] == pytest.approx(3.0)
    assert summary["profit_factor"] == pytest.approx(20.0 / 11.0)
    assert summary["break_even_win_rate_pct_at_10_win_11_loss"] == pytest.approx(52.38095238)
    expected_hours = 60.0 / 3600.0
    assert summary["target_duration_p75_hours"] == pytest.approx(expected_hours)
    assert summary["target_duration_p90_hours"] == pytest.approx(expected_hours)
    assert summary["target_duration_p95_hours"] == pytest.approx(expected_hours)
    assert summary["stop_duration_p90_hours"] == pytest.approx(expected_hours)


def test_duration_buckets_are_cumulative() -> None:
    ts = BASE.timestamp()
    cache, archives = _cache([ts, ts + 4 * 60], [100.0, 101.2])
    target = replay_fill(_fill("Long"), archives, cache=cache, data_end=BASE.replace(hour=11))
    buckets = _duration_buckets([target], "target")
    assert buckets["le_5m"] == 1
    assert buckets["le_72h"] == 1
    assert buckets["gt_72h"] == 0


def test_replay_open_at_data_end_has_no_realized_pnl() -> None:
    ts = BASE.timestamp()
    cache, archives = _cache([ts, ts + 60, ts + 120], [100.0, 100.2, 100.1])
    result = replay_fill(
        _fill("Long"),
        archives,
        cache=cache,
        data_end=BASE.replace(hour=11),
    )
    assert result.outcome == "data_end_open"
    assert result.pnl_usd_100_margin_10x is None
    assert result.exit_at is None
    assert result.last_move_from_fill_pct == pytest.approx(0.1)


def test_archive_gap_before_resolution_fails_closed() -> None:
    ts = BASE.timestamp()
    cache, archives = _cache([ts, ts + 60], [100.0, 100.2])
    with pytest.raises(FileNotFoundError, match="archive gap before resolution"):
        replay_fill(
            _fill("Long"),
            archives,
            cache=cache,
            data_end=BASE.replace(day=20),
        )
