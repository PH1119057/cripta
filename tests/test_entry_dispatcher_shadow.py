from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from operations.monitoring.entry_dispatcher_shadow import build_shadow_decision


def test_consumed_context_preserves_causal_identity_without_trading_effect() -> None:
    signal_at = datetime(2026, 8, 31, 1, 0, tzinfo=UTC)
    assessment_at = signal_at - timedelta(seconds=10)
    result = build_shadow_decision(
        signal_id="m3-1",
        symbol="BTCUSDT",
        direction="long",
        signal_at=signal_at,
        strategy_decision_at=signal_at + timedelta(milliseconds=50),
        profile_id="M3_V1_LONG_ENTRY",
        assessment={
            "assessment_id": "assessment-1",
            "mayak_snapshot_id": "mayak-1",
            "observed_at": assessment_at,
            "profile_version": "shadow-v1",
            "status": "RESEARCH_REQUIRED",
            "data_quality": "MEDIUM",
            "coverage": {"market.direction": {"valid": 20, "total": 20}},
        },
    )
    assert result["context_type"] == "CONSUMED_CONTEXT"
    assert result["consumed_dispatcher_assessment_id"] == "assessment-1"
    assert result["consumed_mayak_snapshot_id"] == "mayak-1"
    assert result["shadow_dispatcher_decision"] == "RESEARCH_REQUIRED"
    assert result["baseline_decision"] == "ALLOW_BASELINE"
    assert result["trading_effect"] == "NONE"
    assert assessment_at <= signal_at


def test_future_assessment_is_rejected() -> None:
    signal_at = datetime(2026, 8, 31, 1, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="future Dispatcher"):
        build_shadow_decision(
            signal_id="m3-2",
            symbol="BTCUSDT",
            direction="short",
            signal_at=signal_at,
            strategy_decision_at=signal_at + timedelta(seconds=1),
            profile_id="M3_V1_SHORT_ENTRY",
            assessment={
                "assessment_id": "future",
                "mayak_snapshot_id": "mayak-future",
                "observed_at": signal_at + timedelta(seconds=1),
                "profile_version": "shadow-v1",
                "status": "RESEARCH_REQUIRED",
            },
        )


def test_missing_assessment_is_recorded_not_backfilled() -> None:
    signal_at = datetime(2026, 8, 31, 1, 0, tzinfo=UTC)
    result = build_shadow_decision(
        signal_id="m3-3",
        symbol="ETHUSDT",
        direction="short",
        signal_at=signal_at,
        strategy_decision_at=signal_at,
        profile_id="M3_V1_SHORT_ENTRY",
        assessment=None,
    )
    assert result["shadow_dispatcher_decision"] == "NO_CONTEXT"
    assert result["consumed_dispatcher_assessment_id"] is None
    assert result["consumed_mayak_snapshot_id"] is None
