from __future__ import annotations

from array import array
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.research.exit_break_even_v13 import (
    CoreSignal,
    PathSeries,
    TradeDayCache,
    build_path_series,
)
from bybit_workbench.research.first_retest_stop_anatomy_p49 import (
    P49Config,
    _accumulate_policy_counts,
    _build_compact_path_series,
    _load_checkpoint,
    _new_policy_counts,
    _policy_outcomes_after_confirm,
    _simulate_stop_after_confirm,
    _write_checkpoint,
    analyze_first_retest,
)
from bybit_workbench.research.flow_reversal_v1 import TradeDay


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


def test_compact_path_builder_matches_common_builder_without_python_float_tuples(
    tmp_path: Path,
) -> None:
    touch = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    signal = CoreSignal(
        symbol="UNIUSDT",
        direction="Long",
        touch_at=touch,
        entry_price=10.0,
        source_row={},
    )
    archive = tmp_path / "2026-06-01.csv.gz"
    tape = TradeDay(
        timestamps=(
            touch.timestamp() - 1.0,
            touch.timestamp(),
            touch.timestamp() + 10.0,
            touch.timestamp() + 20.0,
            touch.timestamp() + 3700.0,
        ),
        prices=(10.0, 10.0, 10.05, 9.95, 11.0),
    )

    def loader(path: Path) -> TradeDay:
        assert path == archive
        return tape

    archive_by_day = {"2026-06-01": archive}
    common = build_path_series(
        signal,
        archive_by_day,
        horizon_hours=1,
        cache=TradeDayCache(max_days=1, loader=loader),
    )
    compact = _build_compact_path_series(
        signal,
        archive_by_day,
        horizon_hours=1,
        cache=TradeDayCache(max_days=1, loader=loader),
    )

    assert tuple(compact.timestamps) == common.timestamps
    assert tuple(compact.moves_pct) == pytest.approx(common.moves_pct)
    assert isinstance(compact.timestamps, array)
    assert isinstance(compact.moves_pct, array)
    assert compact.complete_through == common.complete_through


def test_memory_bounded_policy_grid_matches_single_policy_simulator() -> None:
    path = _path([0.00, 0.10, 0.40, 0.34, -0.40, -0.34, -0.20, -0.55, 0.20, 1.00])
    config = _config()
    event = analyze_first_retest(path, activation_pct=0.10, config=config)
    outcomes = _policy_outcomes_after_confirm(path, event, config)

    for stop in config.stop_candidates_pct:
        for target in config.continuation_targets_pct:
            assert outcomes[(stop, target)] == _simulate_stop_after_confirm(
                path,
                event,
                stop,
                target,
            )


def test_windows_launcher_defaults_to_four_day_cache_for_72h_path() -> None:
    launcher = Path("scripts/research_first_retest_stop_anatomy_p49_windows.ps1")
    text = launcher.read_text(encoding="utf-8")

    assert "[int]$DayCacheSize = 4" in text


def test_checkpoint_roundtrip_restores_events_and_policy_counts(tmp_path: Path) -> None:
    path = _path([0.00, 0.10, 0.40, 0.34, -0.40, -0.34, -0.20, -0.55, 1.00])
    config = _config()
    event = analyze_first_retest(path, activation_pct=0.10, config=config)
    counts = _new_policy_counts(config)
    _accumulate_policy_counts(counts, path, (event,), config)
    checkpoint = tmp_path / "checkpoint.json"

    _write_checkpoint(
        checkpoint,
        input_fingerprint="abc123",
        processed=1,
        total=1,
        events=[event],
        policy_counts=counts,
    )
    processed, events, restored = _load_checkpoint(
        checkpoint,
        input_fingerprint="abc123",
        total=1,
        config=config,
    )

    assert processed == 1
    assert events == [event]
    assert restored[(0.10, -0.50, 1.00)] == counts[(0.10, -0.50, 1.00)]


def test_checkpoint_refuses_incompatible_input_fingerprint(tmp_path: Path) -> None:
    config = _config()
    checkpoint = tmp_path / "checkpoint.json"
    _write_checkpoint(
        checkpoint,
        input_fingerprint="original",
        processed=0,
        total=1,
        events=[],
        policy_counts=_new_policy_counts(config),
    )

    with pytest.raises(ValueError, match="input fingerprint mismatch"):
        _load_checkpoint(
            checkpoint,
            input_fingerprint="different",
            total=1,
            config=config,
        )
