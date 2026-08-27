from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.research.exit_break_even_v13 import CoreSignal, PathSeries
from bybit_workbench.research.retest_anatomy_v14 import (
    RetestConfig,
    _be_event_row,
    _runner_rows,
    analyze_path,
    summarise_resolution_matrix,
    summarise_runner_retests,
)


def _path(moves: list[float]) -> PathSeries:
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
    return PathSeries(
        signal=signal,
        timestamps=timestamps,
        moves_pct=tuple(moves),
        available_until=start + timedelta(hours=72),
        coverage_until=start + timedelta(hours=72),
    )


def test_be_exit_can_kill_future_five_r_runner() -> None:
    config = RetestConfig()
    path = _path([0.0, 1.05, 0.6, -0.02, 0.3, 1.2, 5.1])
    anatomy = analyze_path(path, config)
    rows = _runner_rows(path, anatomy, config)
    five_r = next(row for row in rows if row.target_r == 5.0)

    assert anatomy.be_exit_index == 3
    assert five_r.be_exit_before_target is True
    assert five_r.crossed_entry_before_target is True
    assert five_r.retest_floor_r == pytest.approx(-0.02)


def test_target_after_original_minus_one_r_is_not_same_episode_runner() -> None:
    config = RetestConfig()
    path = _path([0.0, 1.05, -0.02, -1.1, 0.5, 5.2])
    anatomy = analyze_path(path, config)
    rows = _runner_rows(path, anatomy, config)

    assert anatomy.be_exit_index == 2
    assert anatomy.initial_stop_index == 3
    assert not any(row.target_r == 5.0 for row in rows)


def test_target_reached_before_be_is_preserved_not_killed() -> None:
    config = RetestConfig()
    path = _path([0.0, 1.05, 2.1, 5.2, -0.01])
    anatomy = analyze_path(path, config)
    rows = _runner_rows(path, anatomy, config)
    five_r = next(row for row in rows if row.target_r == 5.0)

    assert anatomy.be_exit_index == 4
    assert five_r.be_exit_before_target is False
    assert five_r.crossed_entry_before_target is False


def test_be_event_records_recovery_and_invalidation_timing() -> None:
    config = RetestConfig()
    path = _path([0.0, 1.1, 1.3, -0.01, 0.3, 0.6, 1.35, -0.6, -1.1])
    anatomy = analyze_path(path, config)
    event = _be_event_row(path, anatomy, config)

    assert event is not None
    assert event.pre_be_peak_r == pytest.approx(1.3)
    assert event.be_to_prior_peak_reclaim_seconds == pytest.approx(180.0)
    assert event.be_to_invalidation_seconds == pytest.approx(300.0)
    assert '"0.5R": 120.0' in event.recovery_hit_seconds_json
    assert '"1R": 180.0' in event.recovery_hit_seconds_json
    assert '"0.5R": 240.0' in event.adverse_hit_seconds_json
    assert '"1R": 300.0' in event.adverse_hit_seconds_json


def test_prior_peak_reclaim_after_invalidation_does_not_count() -> None:
    config = RetestConfig()
    path = _path([0.0, 1.2, -0.01, -1.1, 1.3])
    anatomy = analyze_path(path, config)

    assert anatomy.initial_stop_index == 3
    assert anatomy.prior_peak_reclaim_index is None


def test_runner_summary_reproduces_target_specific_be_kill_rate() -> None:
    config = RetestConfig()
    killed_path = _path([0.0, 1.1, -0.01, 5.1])
    preserved_path = _path([0.0, 1.1, 5.1, -0.01])
    rows = []
    for path in (killed_path, preserved_path):
        anatomy = analyze_path(path, config)
        rows.extend(_runner_rows(path, anatomy, config))

    summary = summarise_runner_retests(
        rows,
        scopes=("UNIUSDT", "POOLED_UNI_LINK"),
        config=config,
    )
    five_r = next(
        row
        for row in summary
        if row["scope"] == "POOLED_UNI_LINK" and row["target_r"] == 5.0
    )

    assert five_r["baseline_runner_count"] == 2
    assert five_r["be_exit_before_target_count"] == 1
    assert five_r["runner_preservation_percent"] == pytest.approx(50.0)


def test_resolution_matrix_distinguishes_recovery_first_from_adverse_first() -> None:
    config = RetestConfig()
    paths = (
        _path([0.0, 1.1, -0.01, 0.6, -0.6]),
        _path([0.0, 1.1, -0.01, -0.6, 0.6]),
    )
    events = []
    for path in paths:
        event = _be_event_row(path, analyze_path(path, config), config)
        assert event is not None
        events.append(event)

    matrix = summarise_resolution_matrix(
        events,
        scopes=("POOLED_UNI_LINK",),
        config=config,
    )
    row = next(
        item
        for item in matrix
        if item["up_level_r"] == 0.5 and item["down_level_r"] == -0.5
    )

    assert row["up_first"] == 1
    assert row["down_first"] == 1
    assert row["up_first_percent_decisive"] == pytest.approx(50.0)
