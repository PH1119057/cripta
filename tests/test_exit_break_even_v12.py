from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.research.exit_break_even_v12 import (
    CoreSignal,
    PathSeries,
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
