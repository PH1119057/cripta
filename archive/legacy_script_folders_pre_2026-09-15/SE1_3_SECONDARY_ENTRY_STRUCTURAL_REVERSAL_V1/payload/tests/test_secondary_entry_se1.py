from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.research import secondary_entry_se1
from bybit_workbench.research.exit_break_even_v13 import CoreSignal, PathSeries
from bybit_workbench.research.secondary_entry_se1 import Config, analyze_signal


def _path(moves: tuple[float, ...], *, direction: str = "Long") -> PathSeries:
    touch = datetime(2026, 5, 18, tzinfo=UTC)
    signal = CoreSignal(
        symbol="TESTUSDT",
        direction=direction,  # type: ignore[arg-type]
        touch_at=touch,
        entry_price=100.0,
        source_row={},
    )
    timestamps = tuple(touch.timestamp() + index for index in range(len(moves)))
    return PathSeries(
        signal=signal,
        timestamps=timestamps,
        moves_pct=moves,
        available_until=datetime.fromtimestamp(timestamps[-1], UTC),
        coverage_until=touch + timedelta(hours=72),
    )


def _single_config() -> Config:
    return Config(
        min_adverse_depths_pct=(0.50,),
        rebound_confirmations_pct=(0.20,),
        targets_pct=(0.10, 0.50, 1.00),
        protection_activations_pct=(0.20,),
    )


def test_causal_rebound_triggers_from_running_low_and_structural_buffer() -> None:
    path = _path((0.0, -0.20, -0.55, -0.52, -0.40, -0.34, 0.20, 1.00))
    row = analyze_signal(path, _single_config())[0]

    assert row.trigger_status == "triggered"
    assert row.launch_move_vs_main_pct == pytest.approx(-0.55)
    assert row.scale_entry_move_vs_main_pct == pytest.approx(-0.34)
    assert row.structural_stop_move_vs_main_pct == pytest.approx(-0.65)
    assert row.scale_entry_rebound_from_launch_pct == pytest.approx(0.21)
    assert row.structural_stop_distance_from_scale_pct == pytest.approx(0.311058, rel=1e-4)


def test_main_minus_one_stops_probe_before_secondary_trigger() -> None:
    path = _path((0.0, -0.50, -0.80, -1.00, -0.70, -0.40, 0.50))
    row = analyze_signal(path, _single_config())[0]

    assert row.trigger_status == "main_stop_before_trigger"
    assert row.scale_entry_at is None


def test_false_confirmation_hits_structural_stop_after_scale() -> None:
    path = _path((0.0, -0.55, -0.34, -0.45, -0.66, -0.70))
    row = analyze_signal(path, _single_config())[0]

    assert row.trigger_status == "triggered"
    assert row.secondary_exit_reason == "structural_stop"
    assert row.secondary_exit_move_pct is not None
    assert row.secondary_exit_move_pct < 0


def test_secondary_targets_are_measured_from_secondary_fill_not_main_entry() -> None:
    path = _path((0.0, -0.55, -0.34, 0.17, 0.70))
    row = analyze_signal(path, _single_config())[0]

    assert row.trigger_status == "triggered"
    assert '"0.50"' in row.target_hits_json
    assert '"1.00"' in row.target_hits_json


def test_structural_rule_is_direction_symmetric_for_short() -> None:
    path = _path((0.0, -0.55, -0.34, 0.20, 0.80), direction="Short")
    row = analyze_signal(path, _single_config())[0]

    assert row.trigger_status == "triggered"
    assert row.launch_move_vs_main_pct == pytest.approx(-0.55)
    assert row.structural_stop_move_vs_main_pct == pytest.approx(-0.65)
    assert row.structural_stop_price is not None
    assert row.scale_entry_price is not None
    assert row.structural_stop_price > row.scale_entry_price


def test_config_rejects_nonpositive_buffer() -> None:
    with pytest.raises(ValueError, match="structural_buffer_pct"):
        Config(structural_buffer_pct=0.0)


def test_targets_after_structural_stop_are_not_counted_as_secondary_wins() -> None:
    path = _path((0.0, -0.55, -0.34, -0.66, 0.80, 1.20))
    row = analyze_signal(path, _single_config())[0]

    assert row.secondary_exit_reason == "structural_stop"
    assert '"0.50": null' in row.target_hits_json
    assert '"1.00": null' in row.target_hits_json


def test_summary_markdown_handles_decimal_target_keys() -> None:
    summaries = [
        {
            "scope": "ALL9",
            "min_adverse_depth_pct": 0.50,
            "rebound_confirmation_pct": 0.20,
            "triggered": 10,
            "trigger_rate_pct": 50.0,
            "secondary_structural_stop_rate_pct": 20.0,
            "reached_plus_0.50_pct": 70.0,
            "reached_plus_1.00_pct": 60.0,
            "reached_plus_2.00_pct": 40.0,
            "reached_plus_3.00_pct": 30.0,
        }
    ]

    markdown = secondary_entry_se1._summary_markdown(summaries, "abc123")

    assert "| 0.50% | 0.20% | 10 | 50.0 | 20.0 | 70.0 | 60.0 | 40.0 | 30.0 |" in markdown
