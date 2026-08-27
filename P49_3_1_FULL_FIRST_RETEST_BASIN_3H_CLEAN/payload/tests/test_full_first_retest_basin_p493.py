from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.research.exit_break_even_v13 import CoreSignal, PathSeries
from bybit_workbench.research.full_first_retest_basin_p493 import (
    P493Config,
    _accumulate_tradeoff,
    _load_checkpoint,
    _new_counts,
    _read_p49_v12_cohort,
    _three_hour_row,
    _tradeoff_rows,
    _write_checkpoint,
    analyze_full_retest_basin,
    build_three_hour_depth_summary,
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


def _config(**kwargs: object) -> P493Config:
    values: dict[str, object] = {
        "expected_signals": 0,
        "expected_cohort": 0,
    }
    values.update(kwargs)
    return P493Config(**values)


def test_full_basin_ignores_micro_bounce_until_peak1_reclaim() -> None:
    path = _path(
        [
            -0.20,
            0.10,
            0.40,
            0.33,  # retest starts
            0.05,
            -0.18,
            -0.43,  # true full-basin low
            -0.37,  # old P49 would have called this a confirmation
            0.02,
            0.25,
            0.40,  # causal Peak #1 reclaim ends the basin
            0.80,
            1.20,
        ]
    )

    event, retest_start, reclaim = analyze_full_retest_basin(path, config=_config())

    assert event.status == "reclaimed_peak1"
    assert event.peak1_pct == pytest.approx(0.40)
    assert event.basin_low_pct == pytest.approx(-0.43)
    assert event.hit_minus_0p35_in_basin is True
    assert event.hit_minus_0p50_in_basin is False
    assert event.recovered_plus_1p00_after_retest_start_before_minus_1 is True
    assert retest_start == 3
    assert reclaim == 10


def test_full_basin_fails_at_original_minus_one_before_reclaim() -> None:
    path = _path([0.00, 0.10, 0.35, 0.29, -0.30, -0.62, -1.07, -0.80, 0.40])

    event, _, reclaim = analyze_full_retest_basin(path, config=_config())

    assert event.status == "initial_stop_before_reclaim"
    assert event.basin_low_pct == pytest.approx(-1.07)
    assert event.hit_minus_1p00_in_basin is True
    assert event.peak1_reclaimed_at is None
    assert event.initial_stop_at is not None
    assert reclaim is None


def test_pre_activation_noise_does_not_define_full_retest() -> None:
    path = _path([-0.60, -0.30, 0.00, 0.10, 0.30, 0.22, -0.40, 0.30, 1.00])

    event, _, _ = analyze_full_retest_basin(path, config=_config())

    assert event.peak1_pct == pytest.approx(0.30)
    assert event.basin_low_pct == pytest.approx(-0.40)
    assert event.status == "reclaimed_peak1"


def test_three_hour_row_records_both_adverse_and_favourable_extremes() -> None:
    moves = [0.00, 0.10, 0.35, -0.30, -0.55, 0.20, -1.10, -1.45, -0.20, 0.60]
    path = _path(moves, step_seconds=20 * 60)

    row = _three_hour_row(path, _config())

    assert row["complete_3h"] is True
    assert row["min_3h_pct"] == pytest.approx(-1.45)
    assert row["max_3h_pct"] == pytest.approx(0.60)
    assert row["first_minus_1_within_3h_at"] is not None
    assert row["max_before_first_minus_1_3h_pct"] == pytest.approx(0.35)
    assert row["min_after_first_minus_1_3h_pct"] == pytest.approx(-1.45)
    assert row["raw_max_after_first_minus_1_3h_pct"] == pytest.approx(0.60)


def test_depth_recovery_is_measured_after_depth_hit_not_before_it() -> None:
    # +1 happens before -0.50, then after -0.50 price only recovers to +0.20 before -1.
    path = _path([0.00, 0.10, 1.00, -0.50, 0.20, -1.00], step_seconds=30 * 60)
    row = _three_hour_row(path, _config())
    summary = build_three_hour_depth_summary([row], _config())
    m050 = next(item for item in summary if item["depth_threshold_pct"] == -0.50)

    assert m050["hit_after_plus_0p10_within_3h"] == 1
    assert m050["recover_p0p10_after_depth_before_minus_1"] == 1
    assert m050["recover_p1p00_after_depth_before_minus_1"] == 0


def test_retest_start_tradeoff_counts_saved_loser_and_lost_runner_separately() -> None:
    config = _config()
    counts = _new_counts(config)

    runner = _path([0.00, 0.10, 0.40, 0.33, -0.55, 0.40, 1.00])
    runner_event, runner_start, runner_reclaim = analyze_full_retest_basin(
        runner, config=config
    )
    _accumulate_tradeoff(
        counts,
        runner,
        runner_event,
        runner_start,
        runner_reclaim,
        config,
    )

    loser = _path([0.00, 0.10, 0.40, 0.33, -0.55, -0.80, -1.00])
    loser_event, loser_start, loser_reclaim = analyze_full_retest_basin(loser, config=config)
    _accumulate_tradeoff(
        counts,
        loser,
        loser_event,
        loser_start,
        loser_reclaim,
        config,
    )

    rows = _tradeoff_rows(counts, config, "retest_start")
    row = next(
        item
        for item in rows
        if item["stop_pct"] == -0.50 and item["continuation_target_pct"] == 1.00
    )
    assert row["baseline_runners"] == 1
    assert row["baseline_initial_stop_losers"] == 1
    assert row["runner_lost"] == 1
    assert row["saved_losers"] == 1


def test_horizon_nonrunner_is_not_mislabeled_as_saved_loser() -> None:
    config = _config()
    counts = _new_counts(config)
    path = _path([0.00, 0.10, 0.40, 0.33, -0.55, -0.40, 0.20], hours_complete=72)
    event, start, reclaim = analyze_full_retest_basin(path, config=config)
    _accumulate_tradeoff(counts, path, event, start, reclaim, config)
    rows = _tradeoff_rows(counts, config, "retest_start")
    row = next(
        item
        for item in rows
        if item["stop_pct"] == -0.50 and item["continuation_target_pct"] == 1.00
    )

    assert row["baseline_horizon_nonrunners"] == 1
    assert row["baseline_initial_stop_losers"] == 0
    assert row["saved_losers"] == 0


def test_p49_v12_cohort_loader_requires_exact_plus_010_first_count(tmp_path: Path) -> None:
    summary = {
        "research_version": "P49_FIRST_RETEST_STOP_ANATOMY_V1_2_MEMORY_BOUNDED",
        "signals": 3,
    }
    (tmp_path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    with (tmp_path / "first_retest_events.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["symbol", "direction", "touch_at", "activation_pct", "status"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "symbol": "UNIUSDT",
                "direction": "Long",
                "touch_at": "2026-06-01T00:00:00+00:00",
                "activation_pct": "0.1",
                "status": "reclaimed_peak1",
            }
        )
        writer.writerow(
            {
                "symbol": "LINKUSDT",
                "direction": "Short",
                "touch_at": "2026-06-01T01:00:00+00:00",
                "activation_pct": "0.1",
                "status": "retest_confirmed",
            }
        )
        writer.writerow(
            {
                "symbol": "BTCUSDT",
                "direction": "Long",
                "touch_at": "2026-06-01T02:00:00+00:00",
                "activation_pct": "0.1",
                "status": "no_activation",
            }
        )

    config = _config(expected_signals=3, expected_cohort=2)
    selected, _ = _read_p49_v12_cohort(tmp_path, config)

    assert len(selected) == 2


def test_checkpoint_roundtrip_is_fingerprint_bound(tmp_path: Path) -> None:
    config = _config()
    path = _path([0.00, 0.10, 0.40, 0.33, -0.40, 0.40, 1.00])
    event, start, reclaim = analyze_full_retest_basin(path, config=config)
    row = _three_hour_row(path, config)
    counts = _new_counts(config)
    _accumulate_tradeoff(counts, path, event, start, reclaim, config)
    checkpoint = tmp_path / "checkpoint.json"

    _write_checkpoint(
        checkpoint,
        fingerprint="abc",
        processed=1,
        total=1,
        basin_events=[event],
        three_hour_rows=[row],
        tradeoff_counts=counts,
    )
    processed, events, rows, restored = _load_checkpoint(
        checkpoint,
        fingerprint="abc",
        total=1,
        config=config,
    )

    assert processed == 1
    assert events == [event]
    assert rows == [row]
    assert restored[("retest_start", -0.50, 1.00)] == counts[
        ("retest_start", -0.50, 1.00)
    ]

    with pytest.raises(ValueError, match="fingerprint mismatch"):
        _load_checkpoint(
            checkpoint,
            fingerprint="different",
            total=1,
            config=config,
        )


def test_launcher_attaches_leading_negative_csv_arguments() -> None:
    launcher = Path("scripts/research_full_first_retest_basin_p493_windows.ps1")
    text = launcher.read_text(encoding="utf-8")

    assert '"--stop-candidates-pct=$StopCandidatesPct"' in text
    assert '"--three-hour-depths-pct=$ThreeHourDepthsPct"' in text
