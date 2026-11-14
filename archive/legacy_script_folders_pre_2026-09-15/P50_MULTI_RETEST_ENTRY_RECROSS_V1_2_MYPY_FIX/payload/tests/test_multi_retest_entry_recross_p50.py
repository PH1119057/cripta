from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.research.exit_break_even_v13 import CoreSignal, PathSeries
from bybit_workbench.research.full_first_retest_basin_p493 import _write_csv as p493_write_csv
from bybit_workbench.research.multi_retest_entry_recross_p50 import (
    P50Config,
    _accumulate_action,
    _new_room_samples,
    _new_tradeoff,
    analyze_entry_visits,
    analyze_retest_cycles,
    build_room_rows,
    build_tradeoff_rows,
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


def _config() -> P50Config:
    return P50Config(expected_signals=0, expected_cohort=0)


def test_multi_retest_cycles_continue_after_first_peak_reclaim() -> None:
    path = _path(
        [
            0.00,
            0.10,
            0.30,
            0.20,
            -0.20,
            0.30,  # reclaim cycle 1
            0.55,
            0.40,
            -0.45,
            0.55,  # reclaim cycle 2
            0.80,
            0.60,
            -0.70,
            0.80,  # reclaim cycle 3
            1.20,
        ]
    )

    cycles = analyze_retest_cycles(path, _config())

    assert [event.cycle_no for event, _ in cycles[:3]] == [1, 2, 3]
    assert [event.low_pct for event, _ in cycles[:3]] == pytest.approx([-0.20, -0.45, -0.70])
    assert all(event.status == "reclaimed_peak" for event, _ in cycles[:3])
    assert cycles[1][0].higher_low_vs_previous is False


def test_entry_visit_is_one_episode_until_plus_010_recovery() -> None:
    path = _path(
        [
            0.00,
            0.10,
            0.30,
            -0.05,  # visit 1 begins
            0.03,   # crosses Entry but has not recovered +0.10
            -0.20,
            0.11,   # visit 1 resolves
            0.35,
            -0.10,  # visit 2 begins
            -0.40,
            0.10,   # visit 2 resolves
            0.60,
        ]
    )

    visits = analyze_entry_visits(path, _config())

    assert len(visits) == 2
    assert visits[0][0].low_pct == pytest.approx(-0.20)
    assert visits[0][0].zero_crossings_in_visit == 4
    assert visits[0][0].status == "recovered_plus_0p10"
    assert visits[1][0].low_pct == pytest.approx(-0.40)
    assert visits[1][0].higher_low_vs_previous is False


def test_entry_visit_stops_at_original_minus_one() -> None:
    path = _path([0.00, 0.10, 0.25, -0.05, -0.50, -1.00, 0.20])

    visits = analyze_entry_visits(path, _config())

    assert len(visits) == 1
    assert visits[0][0].status == "initial_stop"
    assert visits[0][1] is None


def test_tradeoff_after_action_counts_saved_loser_and_lost_runner() -> None:
    config = _config()
    counts = _new_tradeoff()
    rooms = _new_room_samples()

    runner = _path([0.00, 0.10, 0.30, -0.10, 0.10, -0.55, 1.00])
    _accumulate_action(
        path=runner,
        action_type="entry_recovery",
        action_number=1,
        action_index=4,
        config=config,
        tradeoff=counts,
        room_samples=rooms,
    )

    loser = _path([0.00, 0.10, 0.30, -0.10, 0.10, -0.55, -1.00])
    _accumulate_action(
        path=loser,
        action_type="entry_recovery",
        action_number=1,
        action_index=4,
        config=config,
        tradeoff=counts,
        room_samples=rooms,
    )

    rows = build_tradeoff_rows(counts)
    row = next(
        item
        for item in rows
        if item["action"] == "entry_recovery"
        and item["action_no"] == 1
        and item["stop_pct"] == -0.50
        and item["target_pct"] == 1.00
    )
    assert row["future_runners"] == 1
    assert row["future_initial_stop_losers"] == 1
    assert row["lost_runners"] == 1
    assert row["saved_losers"] == 1


def test_room_table_records_minimum_required_space_before_future_target() -> None:
    config = _config()
    counts = _new_tradeoff()
    rooms = _new_room_samples()
    path = _path([0.00, 0.10, 0.30, -0.10, 0.10, -0.42, 0.20, 1.00])

    _accumulate_action(
        path=path,
        action_type="entry_recovery",
        action_number=1,
        action_index=4,
        config=config,
        tradeoff=counts,
        room_samples=rooms,
    )
    rows = build_room_rows(rooms)
    row = next(
        item
        for item in rows
        if item["action"] == "entry_recovery"
        and item["action_no"] == 1
        and item["target_pct"] == 1.00
    )

    assert row["future_runners"] == 1
    assert row["min_move_median"] == pytest.approx(-0.42)
    assert row["survive_stop_m0p50"] == 1
    assert row["survive_stop_m0p35"] == 0


def test_p493_csv_writer_accepts_late_extra_fields(tmp_path: Path) -> None:
    path = tmp_path / "rows.csv"
    p493_write_csv(path, [{"a": 1}, {"a": 2, "b": 3}])

    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [{"a": "1", "b": ""}, {"a": "2", "b": "3"}]


def test_p50_launcher_attaches_negative_stop_csv() -> None:
    launcher = Path("scripts/research_multi_retest_entry_recross_p50_windows.ps1")
    text = launcher.read_text(encoding="utf-8")

    assert '"--stop-candidates-pct=$StopCandidatesPct"' in text
    assert '"--continuation-targets-pct=$ContinuationTargetsPct"' in text
