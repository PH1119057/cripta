from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.research.exit_break_even_v13 import CoreSignal, PathSeries
from bybit_workbench.research.mfe_activated_risk_p51 import (
    ExactBaselineEvent,
    P51Config,
    _accumulate_tradeoff,
    _build_tradeoff_rows,
    _future_outcome,
    _new_giveback,
    _new_room_samples,
    _new_tradeoff,
    _path_exact_outcome,
    _process_signal,
)


def _path(moves: list[float], step_seconds: int = 60, hours_complete: int = 72) -> PathSeries:
    touch = datetime(2026, 6, 1, tzinfo=UTC)
    signal = CoreSignal(
        symbol="UNIUSDT",
        direction="Long",
        touch_at=touch,
        entry_price=10.0,
        source_row={},
    )
    timestamps = tuple(touch.timestamp() + index * step_seconds for index in range(len(moves)))
    return PathSeries(
        signal=signal,
        timestamps=timestamps,
        moves_pct=tuple(moves),
        available_until=touch + timedelta(hours=hours_complete),
        coverage_until=touch + timedelta(hours=hours_complete),
    )


def _config() -> P51Config:
    return P51Config(expected_signals=0, expected_cohort=0)


def test_p51_fixed_matrix_is_frozen() -> None:
    with pytest.raises(ValueError, match="MFE milestones"):
        P51Config(mfe_milestones_pct=(0.25, 0.50), expected_signals=0, expected_cohort=0)
    with pytest.raises(ValueError, match="stop candidates"):
        P51Config(stop_candidates_pct=(-0.60,), expected_signals=0, expected_cohort=0)


def test_future_target_already_reached_is_not_counted_again() -> None:
    path = _path([0.00, 0.10, 1.20, 0.10, -0.50, 1.30])
    outcome, target_index, _ = _future_outcome(path, action_index=3, target=1.10, config=_config())

    assert outcome == "target_already_reached"
    assert target_index == 2


def test_first_touch_exact_outcome_uses_plus110_vs_minus1() -> None:
    winner = _path([0.00, 0.10, 0.60, 1.11, -1.00])
    loser = _path([0.00, 0.10, 0.60, -1.00, 1.20])

    assert _path_exact_outcome(winner, _config())[0] == "reached_plus_1p10"
    assert _path_exact_outcome(loser, _config())[0] == "hit_minus_1p00"


def test_tradeoff_counts_saved_loser_and_killed_runner_after_mfe_recovery() -> None:
    config = _config()
    tradeoff = _new_tradeoff()
    rooms = _new_room_samples()

    runner = _path([0.00, 0.10, 0.60, -0.10, 0.10, -0.65, 1.20])
    _accumulate_tradeoff(
        path=runner,
        action_index=4,
        mode="first_recovery_after_mfe",
        milestone=0.50,
        recovery_no=1,
        config=config,
        tradeoff=tradeoff,
        room_samples=rooms,
    )
    loser = _path([0.00, 0.10, 0.60, -0.10, 0.10, -0.65, -1.00])
    _accumulate_tradeoff(
        path=loser,
        action_index=4,
        mode="first_recovery_after_mfe",
        milestone=0.50,
        recovery_no=1,
        config=config,
        tradeoff=tradeoff,
        room_samples=rooms,
    )

    row = next(
        item
        for item in _build_tradeoff_rows(tradeoff)
        if item["mode"] == "first_recovery_after_mfe"
        and item["mfe_milestone_pct"] == 0.50
        and item["stop_pct"] == -0.60
        and item["target_pct"] == 1.10
    )
    assert row["future_runners"] == 1
    assert row["future_initial_stop_losers"] == 1
    assert row["lost_runners"] == 1
    assert row["saved_losers"] == 1


def test_runner_room_is_recorded_only_for_future_target() -> None:
    config = _config()
    tradeoff = _new_tradeoff()
    rooms = _new_room_samples()
    path = _path([0.00, 0.10, 0.55, -0.10, 0.10, -0.42, 0.20, 1.20])

    _accumulate_tradeoff(
        path=path,
        action_index=4,
        mode="first_recovery_after_mfe",
        milestone=0.50,
        recovery_no=1,
        config=config,
        tradeoff=tradeoff,
        room_samples=rooms,
    )

    assert rooms[(0.50, 1.10)] == pytest.approx([-0.42])



def test_primary_rule_uses_first_recovery_after_mfe_was_already_reached() -> None:
    path = _path([0.00, 0.10, 0.30, -0.10, 0.10, 0.60, -0.20, 0.10, -0.65, 1.20])
    baseline = ExactBaselineEvent(
        symbol="UNIUSDT",
        direction="Long",
        touch_at=path.signal.touch_at,
        outcome="reached_plus_1p10",
        event_at=datetime.fromtimestamp(path.timestamps[9], UTC),
        complete_horizon=True,
    )
    tradeoff = _new_tradeoff()
    rooms = _new_room_samples()
    first_rows = []
    giveback = _new_giveback()
    loser_rows = []
    economics = {
        "illustrative_notional_usd": 1000.0,
        "illustrative_round_trip_cost_pct": 0.1,
        "illustrative_win_net_usd": 10.0,
        "illustrative_loss_net_usd": -11.0,
        "illustrative_aggregate_net_usd": 880.0,
    }

    _process_signal(
        path=path,
        baseline=baseline,
        config=_config(),
        economics=economics,
        tradeoff=tradeoff,
        room_samples=rooms,
        first_rule_rows=first_rows,
        giveback=giveback,
        loser_mfe_rows=loser_rows,
    )

    row = next(
        item
        for item in first_rows
        if item.mfe_milestone_pct == 0.50 and item.stop_pct == -0.60
    )
    assert row.action_visit_no == 2
    assert row.acted_before_baseline_outcome is True
    assert row.killed_baseline_winner is True
    assert row.illustrative_delta_usd == pytest.approx(-17.0)

def test_p51_launcher_uses_equals_form_for_negative_csv_arguments() -> None:
    launcher = Path("scripts/research_mfe_activated_risk_p51_windows.ps1")
    text = launcher.read_text(encoding="utf-8")

    assert '"--stop-candidates-pct=$StopCandidatesPct"' in text
    assert '"--mfe-milestones-pct=$MfeMilestonesPct"' in text
    assert '"--continuation-targets-pct=$ContinuationTargetsPct"' in text
