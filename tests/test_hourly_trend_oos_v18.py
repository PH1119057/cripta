from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from bybit_workbench.research.core_runner_split_v16 import SplitPolicyResult
from bybit_workbench.research.hourly_trend_correlation_v17 import HourlyTrendFeature
from bybit_workbench.research.hourly_trend_oos_v18 import (
    FROZEN_POLICY_ID,
    HOLDOUT_SYMBOLS,
    _policy_outcome,
    frozen_config,
    frozen_policy,
    summarise_features,
    validation_p40,
)


def _feature(
    *,
    relation: str,
    runner: bool,
    split: bool = True,
    exit_reason: str = "runner_stop",
    exit_move_pct: float = 1.0,
) -> HourlyTrendFeature:
    touch = datetime(2026, 6, 1, 12, tzinfo=UTC)
    runner_component = 0.5 if runner else 0.0
    return HourlyTrendFeature(
        symbol="BTCUSDT",
        direction="Long",
        touch_at=touch,
        last_closed_hour_start=datetime(2026, 6, 1, 11, tzinfo=UTC),
        hour_open=100.0,
        hour_high=101.0,
        hour_low=99.0,
        hour_close=100.0,
        hour_trade_count=100,
        previous_hour_high=102.0,
        previous_hour_low=100.0,
        structure_1h="bearish",
        structure_relation=relation,  # type: ignore[arg-type]
        ema20=101.0,
        ema20_position="below",
        ema20_relation=relation,  # type: ignore[arg-type]
        ema20_slope="falling",
        combined_trend_1h="bearish",
        combined_relation=relation,  # type: ignore[arg-type]
        strict_trend_1h="bearish",
        strict_relation=relation,  # type: ignore[arg-type]
        exit_reason=exit_reason,
        exit_move_pct=exit_move_pct,
        split_activated=split,
        runner_added=runner,
        core_component_pct=0.5 if split else 0.0,
        runner_component_pct=runner_component,
        max_favorable_pct=5.0 if runner else 1.2,
    )


def test_frozen_policy_is_exact_p47c_winner() -> None:
    config = frozen_config()
    policy = frozen_policy()
    assert policy.policy_id == FROZEN_POLICY_ID
    assert policy.core_fraction == 0.5
    assert policy.runner_fraction == 0.5
    assert policy.floor_mode == "be"
    assert policy.giveback_pct == 4.0
    assert config.early_activation_pct == 0.10
    assert config.split_activation_pct == 1.10
    assert config.core_exit_pct == 1.00
    assert config.horizon_hours == 72


def test_holdout_symbols_exclude_development_assets() -> None:
    assert "UNIUSDT" not in HOLDOUT_SYMBOLS
    assert "LINKUSDT" not in HOLDOUT_SYMBOLS
    assert HOLDOUT_SYMBOLS == (
        "BTCUSDT",
        "ETHUSDT",
        "XRPUSDT",
        "1000PEPEUSDT",
        "SOLUSDT",
        "DOGEUSDT",
        "ADAUSDT",
    )


def test_validation_p40_uses_frozen_cross_asset_path() -> None:
    path = validation_p40(Path("C:/cripta"), "BTCUSDT")
    assert path.as_posix().endswith(
        "reports/cross_asset_validation/BTCUSDT_20260518_20260816/p40"
    )


def test_summary_tests_h1_without_retuning() -> None:
    features = (
        _feature(relation="against", runner=True, exit_move_pct=3.0),
        _feature(relation="against", runner=True, exit_move_pct=2.0),
        _feature(relation="against", runner=False, exit_move_pct=0.5),
        _feature(relation="with", runner=False, exit_move_pct=0.0),
        _feature(
            relation="mixed",
            runner=False,
            split=False,
            exit_reason="initial_stop",
            exit_move_pct=-1.0,
        ),
    )
    summary = summarise_features(features, scope="TEST")
    assert summary["signals"] == 5
    assert summary["runner_added"] == 2
    assert summary["runner_relation_counts"] == {"with": 0, "against": 2, "mixed": 0}
    assert summary["runner_against_share_pct"] == 100.0
    assert summary["h1_primary_check"]["runner_majority_strict_against"] is True
    assert summary["strict_buckets"]["against"]["runner_added_rate_pct"] == 66.666667


def test_summary_is_inconclusive_when_no_runners() -> None:
    summary = summarise_features(
        (_feature(relation="with", runner=False),),
        scope="TEST",
    )
    assert summary["runner_added"] == 0
    assert summary["runner_against_share_pct"] is None
    assert summary["h1_primary_check"]["runner_majority_strict_against"] is None


def test_policy_result_conversion_uses_runner_component() -> None:
    touch = datetime(2026, 6, 1, tzinfo=UTC)
    result = SplitPolicyResult(
        symbol="BTCUSDT",
        touch_at=touch,
        policy_id=FROZEN_POLICY_ID,
        family="mfe",
        core_fraction=0.5,
        runner_fraction=0.5,
        floor_mode="be",
        exit_reason="runner_stop",
        exit_at=touch,
        exit_move_pct=2.0,
        completed_horizon=True,
        early_activated=True,
        split_activated=True,
        split_activation_at=touch,
        core_component_pct=0.5,
        runner_component_pct=1.5,
        runner_exit_move_pct=3.0,
        runner_base_floor_pct=0.0,
        max_favorable_pct=7.0,
        max_episode_locked_floor_pct=0.5,
        target_hits_pct=(1.5, 2.0, 3.0),
    )
    outcome = _policy_outcome(result)
    assert outcome.runner_added is True
    assert outcome.exit_move_pct == 2.0
    assert outcome.max_favorable_pct == 7.0
