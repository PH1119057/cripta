from types import SimpleNamespace

from bybit_workbench.research.trailing_ladder_v21 import (
    LadderConfig,
    _ladder_stop_from_mfe,
    simulate_ladder,
)


def _path(moves):  # type: ignore[no-untyped-def]
    signal = SimpleNamespace(
        symbol="TESTUSDT",
        touch_at=SimpleNamespace(
            isoformat=lambda: "2026-01-01T00:00:00+00:00",
            timestamp=lambda: 0.0,
        ),
    )
    return SimpleNamespace(
        moves_pct=list(moves),
        timestamps=[float(index) for index in range(len(moves))],
        signal=signal,
        complete_through=SimpleNamespace(timestamp=lambda: 72 * 3600.0),
    )


def test_pre_one_ladder_levels() -> None:
    config = LadderConfig()
    assert _ladder_stop_from_mfe(0.10, 0.20, config) == 0.0
    assert _ladder_stop_from_mfe(0.20, 0.20, config) == 0.1
    assert abs(_ladder_stop_from_mfe(0.50, 0.20, config) - 0.4) < 1e-9
    assert abs(_ladder_stop_from_mfe(1.00, 0.20, config) - 0.9) < 1e-9


def test_post_one_020_never_loosens() -> None:
    config = LadderConfig()
    assert abs(_ladder_stop_from_mfe(1.01, 0.20, config) - 0.9) < 1e-9
    assert abs(_ladder_stop_from_mfe(1.19, 0.20, config) - 0.9) < 1e-9
    assert abs(_ladder_stop_from_mfe(1.20, 0.20, config) - 1.0) < 1e-9
    assert abs(_ladder_stop_from_mfe(1.40, 0.20, config) - 1.2) < 1e-9


def test_post_one_030_never_loosens() -> None:
    config = LadderConfig()
    assert abs(_ladder_stop_from_mfe(1.20, 0.30, config) - 0.9) < 1e-9
    assert abs(_ladder_stop_from_mfe(1.50, 0.30, config) - 1.2) < 1e-9


def test_initial_stop_before_activation() -> None:
    config = LadderConfig()
    result = simulate_ladder(_path([0.0, 0.05, -1.10]), 0.20, config)
    assert result.exit_reason == "initial_stop"
    assert result.exit_move_pct == -1.0


def test_ladder_exits_at_protected_level() -> None:
    config = LadderConfig()
    result = simulate_ladder(_path([0.0, 0.11, 0.22, 0.35, 0.19]), 0.20, config)
    assert result.exit_reason == "trailing_stop"
    assert abs(result.exit_move_pct - 0.2) < 1e-9


def test_reaching_one_locks_point_nine() -> None:
    config = LadderConfig()
    result = simulate_ladder(_path([0.0, 0.11, 0.50, 1.02, 0.89]), 0.30, config)
    assert result.exit_reason == "trailing_stop"
    assert abs(result.exit_move_pct - 0.9) < 1e-9
