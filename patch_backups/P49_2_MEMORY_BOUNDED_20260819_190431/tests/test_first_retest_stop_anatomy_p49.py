from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.research.exit_break_even_v13 import CoreSignal, PathSeries
from bybit_workbench.research.first_retest_stop_anatomy_p49 import (
    P49Config,
    _simulate_stop_after_confirm,
    analyze_first_retest,
)


def _path(moves: list[float], step_seconds: int = 10) -> PathSeries:
    touch = datetime(2026, 6, 1, tzinfo=UTC)
    signal = CoreSignal(
        symbol="UNIUSDT",
        direction="Long",
        touch_at=touch,
        entry_price=10.0,
        source_row={},
    )
    timestamps = tuple(touch.timestamp() + i * step_seconds for i in range(len(moves)))
    return PathSeries(
        signal=signal,
        timestamps=timestamps,
        moves_pct=tuple(moves),
        available_until=touch + timedelta(hours=72),
        coverage_until=touch + timedelta(hours=72),
    )


def _config() -> P49Config:
    return P49Config(
        activation_levels_pct=(0.10,),
        retest_start_drawdown_pct=0.05,
        rebound_confirm_pct=0.05,
        stop_candidates_pct=(-0.75, -0.50, -0.25, 0.10),
        continuation_targets_pct=(0.50, 1.00, 2.00, 3.00),
        expected_signals=0,
    )


def test_pre_activation_adverse_move_is_not_retest() -> None:
    path = _path([-0.40, -0.20, 0.00, 0.10, 0.30, 0.24, -0.35, -0.30, 1.00])
    event = analyze_first_retest(path, activation_pct=0.10, config=_config())

    assert event.status == "retest_confirmed"
    assert event.retest_low_pct == -0.35
    assert event.retest_depth_from_peak_pct == pytest.approx(0.65)
    assert event.crossed_entry_on_retest is True
    assert event.hit_minus_0p25_on_retest is True
    assert event.hit_minus_0p50_on_retest is False


def test_initial_stop_before_activation_has_no_first_retest() -> None:
    path = _path([-0.20, -0.70, -1.00, 0.20])
    event = analyze_first_retest(path, activation_pct=0.10, config=_config())

    assert event.status == "no_activation"
    assert event.initial_stop_before_activation is True
    assert event.retest_low_pct is None


def test_retest_can_end_at_initial_stop_before_rebound() -> None:
    path = _path([0.00, 0.10, 0.40, 0.33, -0.20, -0.80, -1.00, -0.90])
    event = analyze_first_retest(path, activation_pct=0.10, config=_config())

    assert event.status == "initial_stop_during_retest"
    assert event.retest_low_pct == -1.00
    assert event.hit_minus_1p00_on_retest is True
    assert event.retest_confirmed_at is None


def test_retest_confirmation_is_causal_rebound_from_observed_low() -> None:
    path = _path([0.00, 0.10, 0.40, 0.34, 0.05, -0.40, -0.37, -0.34, 0.10, 0.40, 1.00])
    event = analyze_first_retest(path, activation_pct=0.10, config=_config())

    assert event.status == "retest_confirmed"
    assert event.retest_low_pct == -0.40
    assert event.confirmation_move_pct == -0.34
    assert event.peak1_pct == 0.40
    assert event.peak1_reclaimed_before_minus_1 is True


def test_stop_tightening_is_simulated_only_after_retest_confirmation() -> None:
    path = _path([0.00, 0.10, 0.40, 0.34, -0.40, -0.34, -0.20, -0.55, 0.20, 1.00])
    event = analyze_first_retest(path, activation_pct=0.10, config=_config())

    outcome, preserved = _simulate_stop_after_confirm(path, event, -0.50, 1.00)

    assert event.status == "retest_confirmed"
    assert outcome == "stop"
    assert preserved is False


def test_future_runner_can_survive_shallow_first_retest() -> None:
    path = _path([0.00, 0.10, 0.35, 0.29, -0.20, -0.14, 0.35, 0.60, 1.00, 2.00])
    event = analyze_first_retest(path, activation_pct=0.10, config=_config())

    assert event.retest_low_pct == -0.20
    assert event.baseline_target_1p00_before_minus_1 is True
    assert event.baseline_target_2p00_before_minus_1 is True
    assert event.post_confirm_target_1p00_before_minus_1 is True


def test_windows_launcher_attaches_negative_stop_candidate_csv() -> None:
    launcher = Path("scripts/research_first_retest_stop_anatomy_p49_windows.ps1")
    text = launcher.read_text(encoding="utf-8")

    assert '"--stop-candidates-pct=$StopCandidatesPct"' in text
    assert '"--stop-candidates-pct", $StopCandidatesPct' not in text
