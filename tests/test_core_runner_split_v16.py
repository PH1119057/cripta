from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.research.core_runner_split_v16 import (
    SplitConfig,
    SplitPolicySpec,
    _episode_move,
    _runner_base_floor,
    simulate_split_policy,
)
from bybit_workbench.research.exit_break_even_v13 import CoreSignal, PathSeries


def _path(moves: list[float], *, complete: bool = True) -> PathSeries:
    start = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
    signal = CoreSignal(
        symbol="UNIUSDT",
        direction="Long",
        touch_at=start,
        entry_price=100.0,
        source_row={},
    )
    timestamps = tuple(
        (start + timedelta(minutes=index)).timestamp() for index in range(len(moves))
    )
    coverage = start + timedelta(hours=72) if complete else start + timedelta(minutes=5)
    available = datetime.fromtimestamp(timestamps[-1], UTC) if timestamps else start
    return PathSeries(
        signal=signal,
        timestamps=timestamps,
        moves_pct=tuple(moves),
        available_until=available,
        coverage_until=coverage,
    )


def _spec(
    core: float,
    *,
    family: str = "hold",
    floor_mode: str = "be",
    giveback: float = 0.0,
) -> SplitPolicySpec:
    return SplitPolicySpec(
        policy_id="TEST",
        family=family,  # type: ignore[arg-type]
        core_fraction=core,
        floor_mode=floor_mode,  # type: ignore[arg-type]
        giveback_pct=giveback,
    )


def test_initial_stop_before_point_one_remains_full_loss() -> None:
    result = simulate_split_policy(
        _path([0.0, 0.05, -0.3, -1.02]),
        _spec(0.8),
        SplitConfig(),
    )

    assert result.exit_reason == "initial_stop"
    assert result.exit_move_pct == pytest.approx(-1.0)
    assert result.split_activated is False


def test_early_be_before_split_is_zero() -> None:
    result = simulate_split_policy(
        _path([0.0, 0.11, 0.5, -0.01]),
        _spec(0.8),
        SplitConfig(),
    )

    assert result.exit_reason == "early_be"
    assert result.exit_move_pct == pytest.approx(0.0)
    assert result.split_activated is False


def test_core_only_realizes_conservative_one_percent() -> None:
    spec = SplitPolicySpec("CORE", "core_only", 1.0)
    result = simulate_split_policy(
        _path([0.0, 0.11, 0.7, 1.11]),
        spec,
        SplitConfig(),
    )

    assert result.exit_reason == "core_take"
    assert result.exit_move_pct == pytest.approx(1.0)
    assert result.core_component_pct == pytest.approx(1.0)


def test_80_20_be_runner_return_to_entry_locks_point_eight() -> None:
    result = simulate_split_policy(
        _path([0.0, 0.11, 1.11, 1.4, -0.01]),
        _spec(0.8, floor_mode="be"),
        SplitConfig(),
    )

    assert result.exit_reason == "runner_stop"
    assert result.runner_exit_move_pct == pytest.approx(0.0)
    assert result.exit_move_pct == pytest.approx(0.8)


def test_80_20_funded_runner_full_stop_still_locks_point_six() -> None:
    spec = _spec(0.8, floor_mode="funded")
    config = SplitConfig()

    assert _runner_base_floor(spec, config) == pytest.approx(-1.0)
    total, core, runner = _episode_move(spec, config, -1.0)
    assert core == pytest.approx(0.8)
    assert runner == pytest.approx(-0.2)
    assert total == pytest.approx(0.6)

    result = simulate_split_policy(
        _path([0.0, 0.11, 1.11, 0.5, -1.01]),
        spec,
        config,
    )
    assert result.exit_move_pct == pytest.approx(0.6)


def test_50_50_funded_runner_cannot_make_episode_negative_at_minus_one() -> None:
    spec = _spec(0.5, floor_mode="funded")
    total, _, _ = _episode_move(spec, SplitConfig(), -1.0)
    assert total == pytest.approx(0.0)


def test_mfe_runner_can_lock_more_than_core_floor() -> None:
    result = simulate_split_policy(
        _path([0.0, 0.11, 1.11, 4.0, 3.2, 2.49]),
        _spec(0.8, family="mfe", floor_mode="be", giveback=1.5),
        SplitConfig(),
    )

    assert result.exit_reason == "runner_stop"
    assert result.runner_exit_move_pct == pytest.approx(2.5)
    assert result.exit_move_pct == pytest.approx(1.3)


def test_funded_mfe_can_start_below_be_without_negative_episode() -> None:
    result = simulate_split_policy(
        _path([0.0, 0.11, 1.11, 1.2, -0.41]),
        _spec(0.75, family="mfe", floor_mode="funded", giveback=1.5),
        SplitConfig(),
    )

    assert result.exit_reason == "runner_stop"
    assert result.runner_exit_move_pct == pytest.approx(-0.3)
    assert result.exit_move_pct == pytest.approx(0.675)


def test_complete_runner_marks_weighted_value_at_horizon() -> None:
    result = simulate_split_policy(
        _path([0.0, 0.11, 1.11, 2.0, 3.0]),
        _spec(0.8, floor_mode="be"),
        SplitConfig(),
    )

    assert result.exit_reason == "horizon"
    assert result.exit_move_pct == pytest.approx(1.4)
    assert result.runner_exit_move_pct == pytest.approx(3.0)


def test_incomplete_unstopped_runner_is_censored() -> None:
    result = simulate_split_policy(
        _path([0.0, 0.11, 1.11, 2.0], complete=False),
        _spec(0.8, floor_mode="be"),
        SplitConfig(),
    )

    assert result.exit_reason == "data_end"
    assert result.completed_horizon is False
