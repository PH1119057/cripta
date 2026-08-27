from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.research.exit_break_even_v13 import CoreSignal, PathSeries
from bybit_workbench.research.runner_management_v15 import (
    PolicySpec,
    RunnerConfig,
    StructuralState,
    _step_floor,
    _update_structural_floor,
    simulate_runner_policy,
)


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


def test_initial_stop_before_point_one_is_full_loss() -> None:
    config = RunnerConfig()
    result = simulate_runner_policy(
        _path([0.0, 0.05, -0.3, -1.02]),
        PolicySpec("CONTROL", "control"),
        config,
    )

    assert result.exit_reason == "initial_stop"
    assert result.exit_move_pct == pytest.approx(-1.0)
    assert result.early_activated is False


def test_point_one_then_entry_return_is_early_be() -> None:
    config = RunnerConfig()
    result = simulate_runner_policy(
        _path([0.0, 0.11, 0.06, -0.01]),
        PolicySpec("CONTROL", "control"),
        config,
    )

    assert result.exit_reason == "early_be"
    assert result.exit_move_pct == pytest.approx(0.0)
    assert result.runner_activated is False


def test_runner_activation_locks_one_percent_floor() -> None:
    config = RunnerConfig()
    result = simulate_runner_policy(
        _path([0.0, 0.11, 0.5, 1.11, 1.3, 0.95]),
        PolicySpec("CONTROL", "control"),
        config,
    )

    assert result.exit_reason == "runner_stop"
    assert result.exit_move_pct == pytest.approx(1.0)
    assert result.runner_activated is True


def test_step_policy_raises_floor_only_after_milestone() -> None:
    config = RunnerConfig()
    spec = PolicySpec("STEP", "step", giveback_pct=0.50)

    assert _step_floor(1.49, spec, config) == pytest.approx(1.0)
    assert _step_floor(1.51, spec, config) == pytest.approx(1.0)
    assert _step_floor(2.01, spec, config) == pytest.approx(1.5)
    assert _step_floor(3.01, spec, config) == pytest.approx(2.5)


def test_step_policy_exits_at_raised_floor() -> None:
    config = RunnerConfig()
    result = simulate_runner_policy(
        _path([0.0, 0.11, 1.11, 2.02, 1.8, 1.49]),
        PolicySpec("STEP", "step", giveback_pct=0.50),
        config,
    )

    assert result.exit_reason == "runner_stop"
    assert result.exit_move_pct == pytest.approx(1.5)
    assert 2.0 in result.target_hits_pct


def test_mfe_giveback_tracks_running_peak_with_hard_one_percent_floor() -> None:
    config = RunnerConfig()
    result = simulate_runner_policy(
        _path([0.0, 0.11, 1.11, 1.4, 1.8, 1.29]),
        PolicySpec("MFE", "mfe", giveback_pct=0.50),
        config,
    )

    assert result.exit_reason == "runner_stop"
    assert result.exit_move_pct == pytest.approx(1.3)
    assert result.max_locked_floor_pct == pytest.approx(1.3)


def test_structural_floor_is_causal_and_waits_for_rebound_confirmation() -> None:
    spec = PolicySpec(
        "STRUCT",
        "structural",
        pullback_pct=0.50,
        rebound_pct=0.25,
        buffer_pct=0.05,
    )
    state = StructuralState(peak_pct=2.0)
    floor = 1.0

    floor = _update_structural_floor(1.45, floor, state, spec)
    assert state.in_pullback is True
    assert floor == pytest.approx(1.0)

    floor = _update_structural_floor(1.30, floor, state, spec)
    assert floor == pytest.approx(1.0)

    floor = _update_structural_floor(1.56, floor, state, spec)
    assert state.in_pullback is False
    assert floor == pytest.approx(1.25)


def test_structural_policy_uses_confirmed_swing_floor() -> None:
    config = RunnerConfig()
    result = simulate_runner_policy(
        _path([0.0, 0.11, 1.11, 2.0, 1.45, 1.30, 1.56, 1.24]),
        PolicySpec(
            "STRUCT",
            "structural",
            pullback_pct=0.50,
            rebound_pct=0.25,
            buffer_pct=0.05,
        ),
        config,
    )

    assert result.exit_reason == "runner_stop"
    assert result.exit_move_pct == pytest.approx(1.25)


def test_complete_unstopped_path_marks_to_horizon() -> None:
    config = RunnerConfig()
    result = simulate_runner_policy(
        _path([0.0, 0.11, 1.11, 2.0, 2.4]),
        PolicySpec("CONTROL", "control"),
        config,
    )

    assert result.exit_reason == "horizon"
    assert result.exit_move_pct == pytest.approx(2.4)
    assert 2.0 in result.target_hits_pct


def test_incomplete_unstopped_path_is_censored() -> None:
    config = RunnerConfig()
    result = simulate_runner_policy(
        _path([0.0, 0.11, 1.11, 2.0], complete=False),
        PolicySpec("CONTROL", "control"),
        config,
    )

    assert result.exit_reason == "data_end"
    assert result.completed_horizon is False
