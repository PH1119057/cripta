from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bybit_workbench.mayak.context_records import (
    coin_market_context_records,
    shared_market_context_record,
)
from bybit_workbench.mayak.continuity import MinuteContinuityTracker
from bybit_workbench.mayak.research.objective_replay import MarketEvent
from bybit_workbench.mayak.research.observation_contour_replay import (
    ALERT_POLICY_STATUS,
    REPLAY_SCHEMA_VERSION,
    ObservationContourReplay,
)

SYMBOLS = ("BTCUSDT", "ETHUSDT")
AT = datetime(2026, 9, 6, 3, 0, tzinfo=UTC)


def _replay() -> ObservationContourReplay:
    replay = ObservationContourReplay(SYMBOLS)
    replay.set_supported("linear", set(SYMBOLS))
    replay.feed(
        MarketEvent(
            AT.timestamp() - 60,
            "TRANSPORT",
            market="linear",
            payload={"connected": True},
        )
    )
    replay.feed(
        MarketEvent(
            AT.timestamp() - 30,
            "TRADE",
            "BTCUSDT",
            "linear",
            {"side": "Buy", "price": 100, "size": 1},
        )
    )
    return replay


def test_continuity_tracker_preserves_explicit_gap_semantics() -> None:
    tracker = MinuteContinuityTracker()
    first = tracker.advance(AT)
    later = tracker.advance(AT + timedelta(minutes=3))

    assert first["expected_snapshots"] == 1
    assert first["actual_snapshots"] == 1
    assert later["expected_snapshots"] == 4
    assert later["actual_snapshots"] == 2
    assert later["missing_snapshots"] == 2
    assert later["max_gap_minutes"] == 3
    assert later["coverage_pct"] == 50.0


def test_full_replay_reuses_production_dispatcher_and_keeps_no_policy() -> None:
    replay = _replay()
    result = replay.snapshot(AT.timestamp(), snapshot_id=17)

    assert result["schema_version"] == REPLAY_SCHEMA_VERSION
    assert result["trading_effect"] == "NONE"
    assert result["alerts"] == {
        "policy_status": ALERT_POLICY_STATUS,
        "automatic_generated": [],
        "replayed_facts": [],
    }
    assert result["dispatcher"]["coin_market_rating"] == "NOT_IMPLEMENTED"
    assert result["dispatcher"]["global_context"]["trading_effect"] == "NONE"
    assert set(result["dispatcher"]["coin_contexts"]) == set(SYMBOLS)
    assert result["provenance"]["mayak_engine"] == "LiveMayakEngine"
    package_dir = Path(result["dispatcher"]["production_package_dir"])
    assert package_dir.name == "dispatcher_v2"
    assert "production/src/bybit_workbench/dispatcher_v2" in package_dir.as_posix()


def test_replay_dispatcher_output_matches_direct_production_builders() -> None:
    replay = _replay()
    result = replay.snapshot(AT.timestamp(), snapshot_id=23)

    mayak_snapshot = result["mayak"]
    global_source = shared_market_context_record(23, mayak_snapshot)
    global_context = replay.dispatcher.build_global_market_context(
        global_source,
        now=AT,
    )
    expected_global = replay.dispatcher_serialization.global_context_record(global_context)
    assert result["dispatcher"]["global_context"] == expected_global

    sources = coin_market_context_records(23, mayak_snapshot)
    for source in sources:
        context = replay.dispatcher.build_coin_market_context(
            source,
            global_context_id=global_context.global_context_id,
            now=AT,
        )
        expected = replay.dispatcher_serialization.coin_context_record(context)
        assert result["dispatcher"]["coin_contexts"][source["symbol"]] == expected


def test_replay_exposes_quality_and_continuity_without_neutralizing_missing() -> None:
    replay = _replay()
    first = replay.snapshot(AT.timestamp(), snapshot_id=31)
    second_at = AT + timedelta(minutes=3)
    second = replay.snapshot(second_at.timestamp(), snapshot_id=32)

    continuity = second["quality_continuity"]["collector_continuity"]
    assert continuity["missing_snapshots"] == 2
    assert continuity["coverage_pct"] == 50.0
    assert first["quality_continuity"]["global_data_quality"] == "INSUFFICIENT"
    assert second["quality_continuity"]["global_data_quality"] == "INSUFFICIENT"


def test_explicit_historical_alert_fact_replays_without_generation_policy() -> None:
    replay = _replay()
    alert_at = AT - timedelta(seconds=10)
    alert = replay.feed_alert_fact(
        alert_class="SOURCE_OUTAGE",
        observed_at=alert_at,
        source_component="MAYAK",
        scope_key="source:linear",
        payload={"status": "NO_DATA"},
        provenance={"trading_command": False, "source": "historical-alert"},
    )
    result = replay.snapshot(AT.timestamp(), snapshot_id=41)

    assert result["alerts"]["policy_status"] == "NO_POLICY"
    assert result["alerts"]["automatic_generated"] == []
    assert result["alerts"]["replayed_facts"][0]["alert_id"] == alert.alert_id
    assert result["alerts"]["replayed_facts"][0]["content_hash"] == alert.content_hash
    assert result["alerts"]["replayed_facts"][0]["provenance"]["trading_command"] is False


def test_replay_rejects_future_or_out_of_order_inputs() -> None:
    replay = _replay()
    replay.snapshot(AT.timestamp(), snapshot_id=51)

    with pytest.raises(ValueError, match="INPUT_BEFORE_SNAPSHOT"):
        replay.feed_alert_fact(
            alert_class="SOURCE_OUTAGE",
            observed_at=AT - timedelta(seconds=1),
            source_component="MAYAK",
            scope_key="source:linear",
            payload={},
            provenance={"trading_command": False},
        )

    with pytest.raises(ValueError, match="dispatcher_at"):
        replay.snapshot(
            (AT + timedelta(minutes=1)).timestamp(),
            snapshot_id=52,
            dispatcher_at=AT.timestamp(),
        )
