from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bybit_workbench.domain.models import Candle
from bybit_workbench.entry_bot.calibration import build_calibration_file, load_calibrations
from bybit_workbench.entry_bot.config import EntryBotConfig
from bybit_workbench.entry_bot.engine import (
    OiPoint,
    TradeFlowBucket,
    compute_latest_zone,
    evaluate_core_gate,
    flow_features,
    oi_features_at,
)
from bybit_workbench.entry_bot.handoff import PositionHandoffStore
from bybit_workbench.entry_bot.models import (
    REFERENCE_SYMBOLS,
    WORKING_SYMBOLS,
    EntryBotCalibration,
    EntrySignalEvent,
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

    calibration = EntryBotCalibration(
        symbol="UNIUSDT",
        high_oi_change_60m_pct=Decimal("100"),
        low_oi_acceleration_5_vs_60=Decimal("-100"),
        source_period="20260518_20260816",
        source_summary_sha256="a" * 64,
    )
    accepted = evaluate_core_gate(
        flow=flow,
        oi=oi,
        calibration=calibration,
        accepted_after_failure_embargo=True,
    )
    assert accepted.allowed
    assert accepted.oi_tail_danger is False


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
