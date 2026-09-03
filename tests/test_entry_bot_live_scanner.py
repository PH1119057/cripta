from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bybit_workbench.domain.models import Candle
from bybit_workbench.entry_bot.audit import EntryBotAuditStore
from bybit_workbench.entry_bot.calibration import build_calibration_file, load_calibrations
from bybit_workbench.entry_bot.config import EntryBotConfig
from bybit_workbench.entry_bot.engine import (
    EntrySymbolEngine,
    OiPoint,
    TradeFlowBucket,
    _AuditTrackedOutcome,
    compute_latest_zone,
    evaluate_core_gate,
    floor_time,
    flow_features,
    oi_features_at,
)
from bybit_workbench.entry_bot.handoff import PositionHandoffStore
from bybit_workbench.entry_bot.models import (
    REFERENCE_SYMBOLS,
    WORKING_SYMBOLS,
    ArmedCandidate,
    EntryBotAuditEvent,
    EntryBotCalibration,
    EntrySignalEvent,
    OiFeatures,
    PositionHandoff,
)
from bybit_workbench.entry_bot.runtime import EntryBotRuntime, subscription_topics
from bybit_workbench.research.mtf_entry_v3 import _precompute_post_shock_zones


def _candles(symbol: str, timeframe: str, count: int, minutes: int) -> tuple[Candle, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[Candle] = []
    for index in range(count):
        base = Decimal("100") + Decimal(index) / Decimal("20")
        opened = start + timedelta(minutes=index * minutes)
        rows.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                opened_at=opened,
                closed_at=opened + timedelta(minutes=minutes),
                open=base,
                high=base + Decimal("1.0"),
                low=base - Decimal("0.8"),
                close=base + Decimal("0.2"),
                volume=Decimal("10"),
            )
        )
    return tuple(rows)


def test_frozen_bot_universe_is_ten_non_meme_assets_plus_btc_eth_refs() -> None:
    config = EntryBotConfig()
    assert config.working_symbols == WORKING_SYMBOLS
    assert len(WORKING_SYMBOLS) == 10
    assert REFERENCE_SYMBOLS == ("BTCUSDT", "ETHUSDT")
    assert "BTCUSDT" not in WORKING_SYMBOLS
    assert "ETHUSDT" not in WORKING_SYMBOLS
    assert "1000PEPEUSDT" not in WORKING_SYMBOLS
    assert "DOGEUSDT" not in WORKING_SYMBOLS
    assert len(subscription_topics(config)) == 58


def test_production_zone_copy_matches_frozen_p30_zone_on_same_history() -> None:
    rows = _candles("UNIUSDT", "5", 260, 5)
    production = compute_latest_zone(
        rows,
        timeframe="5",
        lookback=130,
        atr_period=200,
        width_atr=Decimal("0.5"),
        shock_atr_period=20,
        shock_atr_multiple=Decimal("3"),
        minimum_regime_bars=12,
    )
    research = _precompute_post_shock_zones(
        rows,
        timeframe="5",
        lookback=130,
        atr_period=200,
        width_atr=Decimal("0.5"),
        shock_atr_period=20,
        shock_atr_multiple=Decimal("3"),
        minimum_regime_bars=12,
    )[-1]
    assert production is not None
    assert research is not None
    assert production.support_top == research.support_top
    assert production.support_bottom == research.support_bottom
    assert production.resistance_top == research.resistance_top
    assert production.resistance_bottom == research.resistance_bottom
    assert production.atr == research.atr
    assert production.regime_reset_at == research.regime_reset_at


def test_flow_uses_only_completed_minutes_before_exact_touch() -> None:
    touch = datetime(2026, 8, 18, 12, 10, 30, tzinfo=UTC)
    buckets: dict[datetime, TradeFlowBucket] = {}
    for offset in range(2, 6):
        minute = touch.replace(second=0, microsecond=0) - timedelta(minutes=offset)
        buckets[minute] = TradeFlowBucket(
            minute,
            buy_notional=Decimal("20"),
            sell_notional=Decimal("80"),
        )
    reversal_minute = touch.replace(second=0, microsecond=0) - timedelta(minutes=1)
    buckets[reversal_minute] = TradeFlowBucket(
        reversal_minute,
        buy_notional=Decimal("80"),
        sell_notional=Decimal("20"),
    )
    # The current, incomplete minute must not leak into the gate.
    current_minute = touch.replace(second=0, microsecond=0)
    buckets[current_minute] = TradeFlowBucket(
        current_minute,
        buy_notional=Decimal("0"),
        sell_notional=Decimal("1000000"),
    )
    result = flow_features("Long", touch, buckets)
    assert result.pressure_directional_delta_pct == Decimal("-60")
    assert result.reversal_directional_delta_pct == Decimal("60")
    assert result.state == "pressure_then_reversal"


def test_oi_tail_gate_is_fail_closed_and_uses_frozen_thresholds() -> None:
    anchor = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    points = tuple(
        OiPoint(anchor - timedelta(minutes=60 - index * 5), Decimal("100") + index)
        for index in range(13)
    )
    oi = oi_features_at(points, anchor)
    assert oi is not None
    flow = flow_features(
        "Long",
        anchor,
        {
            anchor - timedelta(minutes=5): TradeFlowBucket(
                anchor - timedelta(minutes=5), Decimal("1"), Decimal("4")
            ),
            anchor - timedelta(minutes=4): TradeFlowBucket(
                anchor - timedelta(minutes=4), Decimal("1"), Decimal("4")
            ),
            anchor - timedelta(minutes=3): TradeFlowBucket(
                anchor - timedelta(minutes=3), Decimal("1"), Decimal("4")
            ),
            anchor - timedelta(minutes=2): TradeFlowBucket(
                anchor - timedelta(minutes=2), Decimal("1"), Decimal("4")
            ),
            anchor - timedelta(minutes=1): TradeFlowBucket(
                anchor - timedelta(minutes=1), Decimal("4"), Decimal("1")
            ),
        },
    )
    missing = evaluate_core_gate(
        flow=flow,
        oi=oi,
        calibration=None,
        accepted_after_failure_embargo=True,
    )
    assert not missing.allowed
    assert "calibration" in missing.reason

    observation_only = evaluate_core_gate(
        flow=flow,
        oi=None,
        calibration=None,
        accepted_after_failure_embargo=True,
        require_oi_calibration=False,
    )
    assert observation_only.allowed
    assert observation_only.oi_tail_danger is None

    calibration = EntryBotCalibration(
        symbol="UNIUSDT",
        high_oi_change_60m_pct=Decimal("100"),
        low_oi_acceleration_5_vs_60=Decimal("-100"),
        source_period="20260518_20260816",
        source_summary_sha256="a" * 64,
    )
    calibrated_but_temporarily_missing_oi = evaluate_core_gate(
        flow=flow,
        oi=None,
        calibration=calibration,
        accepted_after_failure_embargo=True,
        require_oi_calibration=False,
    )
    assert calibrated_but_temporarily_missing_oi.allowed
    assert calibrated_but_temporarily_missing_oi.oi_tail_danger is None
    accepted = evaluate_core_gate(
        flow=flow,
        oi=oi,
        calibration=calibration,
        accepted_after_failure_embargo=True,
    )
    assert accepted.allowed
    assert accepted.oi_tail_danger is False


def test_floor_time_for_sixty_minutes_stays_in_current_hour() -> None:
    timestamp = datetime(2026, 8, 27, 12, 59, 59, tzinfo=UTC)
    assert floor_time(timestamp, 60) == datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def test_hourly_swing_pause_clears_after_shock_leaves_rolling_window() -> None:
    config = EntryBotConfig(hourly_swing_pause_percent=Decimal("10"))
    engine = EntrySymbolEngine("UNIUSDT", config, None)
    five = list(_candles("UNIUSDT", "5", 260, 5))
    five[-12] = replace(five[-12], low=Decimal("70"))
    observed = five[-1].closed_at
    engine.load_history(
        {
            "5": tuple(five),
            "15": _candles("UNIUSDT", "15", 260, 15),
            "60": _candles("UNIUSDT", "60", 260, 60),
        },
        (),
        observed_at=observed,
    )
    assert engine._hourly_swing_blocked  # noqa: SLF001

    last = five[-1]
    for index in range(12):
        opened = last.closed_at + timedelta(minutes=index * 5)
        engine.on_closed_candle(
            replace(
                last,
                opened_at=opened,
                closed_at=opened + timedelta(minutes=5),
                open=Decimal("110"),
                high=Decimal("110.2"),
                low=Decimal("109.8"),
                close=Decimal("110"),
            )
        )
    assert not engine._hourly_swing_blocked  # noqa: SLF001


def test_calibration_builder_extracts_small_runtime_file(tmp_path: Path) -> None:
    summary = (
        tmp_path
        / "reports"
        / "cross_asset_validation"
        / "UNIUSDT_20260518_20260816"
        / "p35"
        / "summary.json"
    )
    summary.parent.mkdir(parents=True)
    summary.write_text(
        json.dumps(
            {
                "p34_oi_tail_recheck": {
                    "thresholds": {
                        "high_oi_change_60m_pct": "4.25",
                        "low_oi_acceleration_5_vs_60": "-0.75",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "var" / "entry_bot_calibration.json"
    built, missing = build_calibration_file(
        tmp_path,
        output,
        period="20260518_20260816",
        symbols=("UNIUSDT", "LINKUSDT"),
    )
    assert built == ("UNIUSDT",)
    assert missing == ("LINKUSDT",)
    loaded = load_calibrations(output)
    assert loaded["UNIUSDT"].high_oi_change_60m_pct == Decimal("4.25")
    assert loaded["UNIUSDT"].low_oi_acceleration_5_vs_60 == Decimal("-0.75")


def test_position_handoff_is_durable_and_claimed_once(tmp_path: Path) -> None:
    store = PositionHandoffStore(tmp_path / "workbench.db")
    try:
        touch = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
        flow = flow_features(
            "Long",
            touch,
            {
                touch - timedelta(minutes=5): TradeFlowBucket(
                    touch - timedelta(minutes=5), Decimal("1"), Decimal("2")
                ),
                touch - timedelta(minutes=4): TradeFlowBucket(
                    touch - timedelta(minutes=4), Decimal("1"), Decimal("2")
                ),
                touch - timedelta(minutes=3): TradeFlowBucket(
                    touch - timedelta(minutes=3), Decimal("1"), Decimal("2")
                ),
                touch - timedelta(minutes=2): TradeFlowBucket(
                    touch - timedelta(minutes=2), Decimal("1"), Decimal("2")
                ),
                touch - timedelta(minutes=1): TradeFlowBucket(
                    touch - timedelta(minutes=1), Decimal("2"), Decimal("1")
                ),
            },
        )
        oi = oi_features_at(
            (
                OiPoint(touch - timedelta(minutes=60), Decimal("100")),
                OiPoint(touch - timedelta(minutes=5), Decimal("101")),
                OiPoint(touch, Decimal("102")),
            ),
            touch,
        )
        assert oi is not None
        signal = EntrySignalEvent(
            signal_id="signal-1",
            strategy_id="entry_v1_core",
            strategy_version="1.0-live-first-touch",
            symbol="UNIUSDT",
            direction="Long",
            candidate_bar_at=touch,
            touch_at=touch,
            entry_price=Decimal("10"),
            flow=flow,
            oi=oi,
            zone_gap_pct=Decimal("0.1"),
        )
        assert store.record_signal(signal)
        handoff = PositionHandoff(
            handoff_id="handoff-1",
            source_signal_id=signal.signal_id,
            strategy_id=signal.strategy_id,
            strategy_version=signal.strategy_version,
            symbol="UNIUSDT",
            side="Long",
            quantity=Decimal("2"),
            average_entry=Decimal("10"),
            initial_stop=Decimal("9.9"),
            entry_order_id="order-1",
            client_order_id="client-1",
            protection_order_id="stop-1",
            filled_at=touch,
        )
        assert store.publish_position(handoff)
        claimed = store.claim_next("exit-runtime-v1")
        assert claimed is not None
        assert claimed.handoff.handoff_id == handoff.handoff_id
        assert claimed.claimed_by == "exit-runtime-v1"
        assert store.claim_next("another-consumer") is None
        assert store.close_handoff(handoff.handoff_id, consumer_id="exit-runtime-v1")
    finally:
        store.close()


def test_live_tape_warmup_requires_actual_completed_minute_buckets() -> None:
    config = EntryBotConfig()
    calibration = EntryBotCalibration(
        symbol="UNIUSDT",
        high_oi_change_60m_pct=Decimal("100"),
        low_oi_acceleration_5_vs_60=Decimal("-100"),
        source_period="20260518_20260816",
        source_summary_sha256="b" * 64,
    )
    engine = EntrySymbolEngine("UNIUSDT", config, calibration)
    observed = datetime(2026, 8, 19, 0, 0, 10, tzinfo=UTC)
    history = {
        "5": _candles("UNIUSDT", "5", 260, 5),
        "15": _candles("UNIUSDT", "15", 260, 15),
        "60": _candles("UNIUSDT", "60", 260, 60),
    }
    oi = (
        OiPoint(observed - timedelta(minutes=60), Decimal("100")),
        OiPoint(observed - timedelta(minutes=5), Decimal("101")),
        OiPoint(observed, Decimal("102")),
    )
    engine.load_history(history, oi, observed_at=observed)

    # Wall-clock time alone must never arm flow after startup.
    after_ten_minutes = engine.snapshot(observed + timedelta(minutes=10))
    assert after_ten_minutes.status.value == "WARMUP"
    assert after_ten_minutes.flow_state == "TAPE 0/5"

    # Five actual completed public-trade minute buckets make the 4+1 window causal-ready.
    for offset in range(5):
        traded_at = observed.replace(second=20) + timedelta(minutes=offset)
        engine.on_trade(
            price=Decimal("100"),
            size=Decimal("1"),
            taker_side="Buy",
            traded_at=traded_at,
        )
    partial = engine.snapshot(observed + timedelta(minutes=4, seconds=40))
    assert partial.status.value == "WARMUP"
    assert partial.flow_state == "TAPE 4/5"

    ready = engine.snapshot(observed + timedelta(minutes=5, seconds=10))
    assert ready.status.value != "WARMUP"
    assert not ready.flow_state.startswith("TAPE ")


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_runtime_rest_retries_transient_timeout(tmp_path: Path) -> None:
    from bybit_workbench.app.config import MAINNET_KZ_REST_URL, AppSettings
    from bybit_workbench.domain.types import AppMode

    settings = AppSettings(
        mode=AppMode.LIVE,
        database_path=tmp_path / "workbench.db",
        rest_url_override=MAINNET_KZ_REST_URL,
    )
    runtime = EntryBotRuntime(settings, calibration_path=tmp_path / "missing.json")
    ok = {"retCode": 0, "retMsg": "OK", "result": {"list": []}}
    try:
        with patch(
            "bybit_workbench.entry_bot.runtime.urllib.request.urlopen",
            side_effect=[TimeoutError("temporary"), _FakeResponse(ok)],
        ) as mocked:
            payload = runtime._get_json(
                MAINNET_KZ_REST_URL,
                "/v5/market/kline",
                {"category": "linear", "symbol": "UNIUSDT", "interval": "5"},
            )
        assert payload["retCode"] == 0
        assert mocked.call_count == 2
    finally:
        runtime.close()


def test_runtime_no_calibration_skips_network_warmup(tmp_path: Path) -> None:
    from bybit_workbench.app.config import MAINNET_KZ_REST_URL, AppSettings
    from bybit_workbench.domain.types import AppMode

    settings = AppSettings(
        mode=AppMode.LIVE,
        database_path=tmp_path / "workbench.db",
        rest_url_override=MAINNET_KZ_REST_URL,
    )
    runtime = EntryBotRuntime(settings, calibration_path=tmp_path / "missing.json")
    try:
        with patch.object(runtime, "_get_json") as mocked:
            assert runtime._warmup() is False
            mocked.assert_not_called()
        snapshot = runtime.snapshot()
        assert snapshot.state.value == "STOPPED"
        assert all(asset.status.value == "NO CALIBRATION" for asset in snapshot.assets)
    finally:
        runtime.close()


def test_entry_bot_audit_store_is_append_only_and_exportable(tmp_path: Path) -> None:
    database = tmp_path / "workbench.db"
    store = EntryBotAuditStore(database)
    output = tmp_path / "history.csv"
    try:
        event = EntryBotAuditEvent(
            event_id="audit-1",
            occurred_at=datetime(2026, 8, 19, 1, 0, tzinfo=UTC),
            symbol="UNIUSDT",
            event_type="DISTANCE_BAND",
            status="APPROACH",
            candidate_id="candidate-1",
            direction="Long",
            entry_price=Decimal("10"),
            last_price=Decimal("10.01"),
            distance_pct=Decimal("0.10"),
            flow_state="neutral_or_mixed",
            oi_state="OK",
            reason="distance band entered: APPROACH",
        )
        assert store.record_events((event,)) == 1
        assert store.record_events((event,)) == 0
        assert store.count == 1
        assert store.export_csv(output) == 1
        text = output.read_text(encoding="utf-8-sig")
        assert "DISTANCE_BAND" in text
        assert "candidate-1" in text
    finally:
        store.close()


def test_shadow_prelimit_and_early_failure_recovery_are_audited() -> None:
    config = EntryBotConfig()
    calibration = EntryBotCalibration(
        symbol="UNIUSDT",
        high_oi_change_60m_pct=Decimal("100"),
        low_oi_acceleration_5_vs_60=Decimal("-100"),
        source_period="20260518_20260816",
        source_summary_sha256="c" * 64,
    )
    engine = EntrySymbolEngine("UNIUSDT", config, calibration)
    touch = datetime(2026, 8, 19, 1, 30, 30, tzinfo=UTC)
    bar_open = touch.replace(minute=30, second=0, microsecond=0)
    engine._history_ready = True
    engine._current_bar_open = bar_open
    engine._candidate = ArmedCandidate(
        symbol="UNIUSDT",
        bar_opened_at=bar_open,
        bar_reference_price=Decimal("100.20"),
        long_entry=Decimal("100"),
        short_entry=None,
        long_gap_pct=Decimal("0.10"),
        short_gap_pct=None,
        oi_features=OiFeatures(
            change_5m_pct=Decimal("0"),
            change_60m_pct=Decimal("0"),
            acceleration_5_vs_60=Decimal("0"),
            anchor_at=bar_open,
        ),
    )
    minute = touch.replace(second=0, microsecond=0)
    for offset in range(5, 1, -1):
        opened = minute - timedelta(minutes=offset)
        engine._flow[opened] = TradeFlowBucket(
            opened, buy_notional=Decimal("20"), sell_notional=Decimal("80")
        )
    reversal = minute - timedelta(minutes=1)
    engine._flow[reversal] = TradeFlowBucket(
        reversal, buy_notional=Decimal("80"), sell_notional=Decimal("20")
    )

    assert (
        engine.on_trade(
            price=Decimal("100.20"),
            size=Decimal("1"),
            taker_side="Buy",
            traded_at=touch - timedelta(seconds=10),
        )
        is None
    )
    signal = engine.on_trade(
        price=Decimal("100"),
        size=Decimal("1"),
        taker_side="Sell",
        traded_at=touch,
    )
    assert signal is not None
    engine.on_trade(
        price=Decimal("98.90"),
        size=Decimal("1"),
        taker_side="Sell",
        traded_at=touch + timedelta(minutes=1),
    )
    engine.on_trade(
        price=Decimal("100.00"),
        size=Decimal("1"),
        taker_side="Buy",
        traded_at=touch + timedelta(minutes=2),
    )
    engine.on_trade(
        price=Decimal("100.20"),
        size=Decimal("1"),
        taker_side="Buy",
        traded_at=touch + timedelta(minutes=3),
    )
    event_types = {event.event_type for event in engine.drain_audit_events()}
    assert "PRELIMIT_ARM_SHADOW" in event_types
    assert "PRELIMIT_TOUCH_SHADOW" in event_types
    assert "CORE_SIGNAL" in event_types
    assert "EARLY_FAILURE" in event_types
    assert "MILESTONE_MINUS_1_00" in event_types
    assert "RECOVERED_ENTRY_AFTER_MINUS_1" in event_types
    assert "RECOVERED_PLUS_0_10_AFTER_MINUS_1" in event_types
    assert engine._failure_embargo_until == touch + timedelta(
        minutes=1 + config.failure_embargo_minutes
    )


def test_core_rejected_research_outcome_cannot_mutate_production_embargo() -> None:
    config = EntryBotConfig()
    calibration = EntryBotCalibration(
        symbol="UNIUSDT",
        high_oi_change_60m_pct=Decimal("100"),
        low_oi_acceleration_5_vs_60=Decimal("-100"),
        source_period="20260518_20260816",
        source_summary_sha256="c" * 64,
    )
    engine = EntrySymbolEngine("UNIUSDT", config, calibration)
    touch = datetime(2026, 8, 19, 1, 30, 30, tzinfo=UTC)
    bar_open = touch.replace(second=0, microsecond=0)
    engine._history_ready = True
    engine._current_bar_open = bar_open
    engine._candidate = ArmedCandidate(
        symbol="UNIUSDT",
        bar_opened_at=bar_open,
        bar_reference_price=Decimal("100.20"),
        long_entry=Decimal("100"),
        short_entry=None,
        long_gap_pct=Decimal("0.10"),
        short_gap_pct=None,
        oi_features=None,
    )
    # The exact touch is retained by the independent research tracker, but the
    # incomplete 4+1 flow window rejects it before a Core Signal exists.
    assert engine.on_trade(
        price=Decimal("100"), size=Decimal("1"), taker_side="Sell", traded_at=touch
    ) is None
    assert len(engine._audit_outcomes) == 1
    assert engine._outcomes == []
    engine.on_trade(
        price=Decimal("98.90"),
        size=Decimal("1"),
        taker_side="Sell",
        traded_at=touch + timedelta(minutes=1),
    )
    assert engine._failure_embargo_until is None
    assert any(
        event.event_type == "MILESTONE_MINUS_1_00"
        for event in engine.drain_audit_events()
    )


def test_restart_state_contains_only_allowed_production_embargo() -> None:
    config = EntryBotConfig()
    original = EntrySymbolEngine("UNIUSDT", config, None)
    now = datetime(2026, 8, 19, 2, 0, tzinfo=UTC)
    original._failure_embargo_until = now + timedelta(minutes=30)
    original._audit_outcomes.append(
        _AuditTrackedOutcome(
            candidate_id="research-only",
            direction="Long",
            entry_price=Decimal("100"),
            touch_at=now,
            expires_at=now + timedelta(hours=1),
        )
    )
    state = original.export_production_state(now)
    assert set(state) == {"failure_embargo_until"}
    restored = EntrySymbolEngine("UNIUSDT", config, None)
    restored.restore_production_state(state, now)
    assert restored._failure_embargo_until == now + timedelta(minutes=30)
    assert restored._audit_outcomes == []
