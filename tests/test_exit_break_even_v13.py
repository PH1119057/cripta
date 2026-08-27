from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.research.exit_break_even_v13 import (
    CoreSignal,
    PathSeries,
    TradeDay,
    TradeDayCache,
    _is_horizon_complete,
    build_path_series,
    directional_move_pct,
    simulate_be_policy,
    target_before_policy_exit,
    target_before_stop,
)


def _path(direction: str, moves: list[float]) -> PathSeries:
    start = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    signal = CoreSignal(
        symbol="TESTUSDT",
        direction=direction,  # type: ignore[arg-type]
        touch_at=start,
        entry_price=100.0,
        source_row={},
    )
    timestamps = tuple(
        (start + timedelta(minutes=index)).timestamp() for index in range(len(moves))
    )
    return PathSeries(
        signal=signal,
        timestamps=timestamps,
        moves_pct=tuple(moves),
        available_until=start + timedelta(hours=72),
    )


def test_directional_move_is_mirrored_for_short() -> None:
    assert directional_move_pct("Long", 100.0, 101.0) == pytest.approx(1.0)
    assert directional_move_pct("Short", 100.0, 99.0) == pytest.approx(1.0)


def test_break_even_arms_then_exits_on_retrace() -> None:
    path = _path("Long", [0.0, 0.2, 0.55, 0.42, 0.08, 1.2])
    result = simulate_be_policy(
        path,
        initial_stop_pct=1.0,
        activation_r=0.5,
        be_buffer_bps=10.0,
        horizon_hours=72,
    )
    assert result.exit_reason == "break_even"
    assert result.activated_at is not None
    assert result.exit_move_pct == pytest.approx(0.10)


def test_initial_stop_wins_before_activation() -> None:
    path = _path("Long", [0.0, -0.4, -1.1, 0.8])
    result = simulate_be_policy(
        path,
        initial_stop_pct=1.0,
        activation_r=0.5,
        be_buffer_bps=10.0,
        horizon_hours=72,
    )
    assert result.exit_reason == "initial_stop"
    assert result.activated_at is None


def test_policy_can_kill_later_runner() -> None:
    path = _path("Long", [0.0, 0.6, 0.05, 5.2])
    result = simulate_be_policy(
        path,
        initial_stop_pct=1.0,
        activation_r=0.5,
        be_buffer_bps=10.0,
        horizon_hours=72,
    )
    assert target_before_stop(path, 5.0, 1.0) is True
    assert target_before_policy_exit(path, 5.0, result) is False


def test_runner_survives_if_target_arrives_before_retrace() -> None:
    path = _path("Long", [0.0, 0.6, 2.2, 5.1, 0.05])
    result = simulate_be_policy(
        path,
        initial_stop_pct=1.0,
        activation_r=0.5,
        be_buffer_bps=10.0,
        horizon_hours=72,
    )
    assert target_before_policy_exit(path, 5.0, result) is True


def test_be_floor_must_be_below_activation() -> None:
    path = _path("Long", [0.0, 0.1])
    with pytest.raises(ValueError, match="break-even floor"):
        simulate_be_policy(
            path,
            initial_stop_pct=1.0,
            activation_r=0.1,
            be_buffer_bps=10.0,
            horizon_hours=72,
        )


def test_complete_horizon_uses_archive_coverage_not_last_tick_proximity(tmp_path) -> None:
    start = datetime(2026, 5, 18, 6, 10, tzinfo=UTC)
    signal = CoreSignal(
        symbol="TESTUSDT",
        direction="Long",
        touch_at=start,
        entry_price=100.0,
        source_row={},
    )
    archive_by_day = {
        day: tmp_path / f"TESTUSDT{day}.csv.gz"
        for day in ("2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21")
    }
    tapes = {
        archive_by_day["2026-05-18"]: TradeDay((start.timestamp(),), (100.0,)),
        archive_by_day["2026-05-19"]: TradeDay(
            ((start + timedelta(hours=24)).timestamp(),),
            (101.0,),
        ),
        archive_by_day["2026-05-20"]: TradeDay(
            ((start + timedelta(hours=48)).timestamp(),),
            (102.0,),
        ),
        # The last trade is ten seconds before 72h. The archive day itself is
        # complete, so absence of a trade in those ten seconds is not censoring.
        archive_by_day["2026-05-21"]: TradeDay(
            ((start + timedelta(hours=72, seconds=-10)).timestamp(),),
            (103.0,),
        ),
    }
    cache = TradeDayCache(max_days=4, loader=lambda path: tapes[path])
    path = build_path_series(signal, archive_by_day, horizon_hours=72, cache=cache)

    assert path.available_until == start + timedelta(hours=72, seconds=-10)
    assert path.coverage_until == start + timedelta(hours=72)
    assert _is_horizon_complete(path, 72) is True


def test_missing_archive_day_stops_path_before_gap(tmp_path) -> None:
    start = datetime(2026, 5, 18, 6, 10, tzinfo=UTC)
    signal = CoreSignal(
        symbol="TESTUSDT",
        direction="Long",
        touch_at=start,
        entry_price=100.0,
        source_row={},
    )
    day18 = tmp_path / "TESTUSDT2026-05-18.csv.gz"
    day19 = tmp_path / "TESTUSDT2026-05-19.csv.gz"
    day21 = tmp_path / "TESTUSDT2026-05-21.csv.gz"
    archive_by_day = {
        "2026-05-18": day18,
        "2026-05-19": day19,
        # 2026-05-20 is intentionally missing.
        "2026-05-21": day21,
    }
    tapes = {
        day18: TradeDay((start.timestamp(),), (100.0,)),
        day19: TradeDay(((start + timedelta(hours=24)).timestamp(),), (101.0,)),
        # This later favorable tick must never be stitched across the missing day.
        day21: TradeDay(((start + timedelta(hours=71)).timestamp(),), (110.0,)),
    }
    cache = TradeDayCache(max_days=4, loader=lambda path: tapes[path])
    path = build_path_series(signal, archive_by_day, horizon_hours=72, cache=cache)

    assert path.missing_archive_days == ("2026-05-20",)
    assert path.coverage_until == datetime(2026, 5, 20, tzinfo=UTC)
    assert _is_horizon_complete(path, 72) is False
    assert max(path.moves_pct) < 10.0


def test_trade_day_cache_reuses_overlapping_days(tmp_path) -> None:
    start = datetime(2026, 5, 18, 6, 0, tzinfo=UTC)
    paths = {
        day: tmp_path / f"TESTUSDT{day}.csv.gz"
        for day in ("2026-05-18", "2026-05-19", "2026-05-20", "2026-05-21")
    }
    load_count: dict[object, int] = {}

    def loader(path):
        load_count[path] = load_count.get(path, 0) + 1
        day_offset = list(paths.values()).index(path)
        tick = start + timedelta(days=day_offset, hours=1)
        return TradeDay((tick.timestamp(),), (100.0 + day_offset,))

    cache = TradeDayCache(max_days=6, loader=loader)
    archive_by_day = {day: path for day, path in paths.items()}
    for minute in (0, 30):
        signal = CoreSignal(
            symbol="TESTUSDT",
            direction="Long",
            touch_at=start + timedelta(minutes=minute),
            entry_price=100.0,
            source_row={},
        )
        build_path_series(signal, archive_by_day, horizon_hours=72, cache=cache)

    assert cache.misses == 4
    assert cache.hits == 4
    assert all(count == 1 for count in load_count.values())
