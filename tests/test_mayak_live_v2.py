import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bybit_workbench.mayak.core.live import (
    InstrumentAvailability,
    LiveMayakEngine,
    MarketState,
    SourceQuality,
)


def engine() -> LiveMayakEngine:
    return LiveMayakEngine(("BTCUSDT", "ETHUSDT", "ADAUSDT"))


def test_spot_and_derivatives_never_mix() -> None:
    item = engine()
    now = datetime.now(UTC).timestamp()
    item.on_trade("spot", "BTCUSDT", now, "Buy", 100, 2)
    item.on_trade("linear", "BTCUSDT", now, "Sell", 100, 3)
    snap = item.snapshot(datetime.now(UTC))
    assert snap["coins"]["BTCUSDT"]["spot"]["net_usd"] == 200
    assert snap["coins"]["BTCUSDT"]["linear"]["net_usd"] == -300


def test_missing_data_is_not_zero_or_neutral() -> None:
    snap = engine().snapshot(datetime.now(UTC))
    assert snap["coins"]["BTCUSDT"]["spot"]["net_usd"] is None
    assert snap["coins"]["BTCUSDT"]["quality"]["spot_trades"]["quality"] == SourceQuality.WARMUP


def test_synchronous_drop_state() -> None:
    item = engine()
    now = datetime.now(UTC).timestamp()
    for symbol in item.symbols:
        item.on_trade("linear", symbol, now - 60, "Buy", 100, 1)
        item.on_trade("linear", symbol, now, "Sell", 99, 1)
    assert item.snapshot(datetime.now(UTC))["state"] == MarketState.SYNCHRONOUS_DROP


def test_book_withdrawal_and_recovery_are_measured() -> None:
    item = engine()
    now = datetime.now(UTC).timestamp()
    item.on_book("spot", "BTCUSDT", now - 1, [(100, 10)], [(101, 10)])
    item.on_book("spot", "BTCUSDT", now, [(100, 5)], [(101, 12)])
    book = item.snapshot(datetime.now(UTC))["coins"]["BTCUSDT"]["books"]["spot"]
    assert book["bid_change_pct"] == -50


def test_engine_has_no_trading_mutation_surface() -> None:
    names = set(dir(LiveMayakEngine))
    assert not names.intersection({"place_order", "cancel_order", "set_stop", "close_position"})


def test_mayak_runner_has_no_trading_context_reader() -> None:
    runner = Path(__file__).parents[1] / "operations" / "monitoring" / "mayak_v2.py"
    source = runner.read_text(encoding="utf-8")
    assert "_read_context" not in source
    assert "runtime.hot_positions" not in source
    assert "runtime.executions" not in source
    assert "monitoring.opportunities" not in source
    assert "runtime.entry_decisions" not in source
    assert "pnl" not in source.lower()


def test_open_interest_uses_causal_horizons_not_previous_message() -> None:
    item = engine()
    now = datetime.now(UTC).timestamp()
    item.on_ticker("BTCUSDT", now - 301, open_interest=100)
    item.on_ticker("BTCUSDT", now, open_interest=110)
    ticker = item.snapshot(datetime.now(UTC))["coins"]["BTCUSDT"]["ticker"]
    assert ticker["open_interest_change_5m_pct"] == pytest.approx(10)
    assert ticker["open_interest_change_15m_pct"] is None


def test_open_interest_retains_all_time_horizons_under_high_message_rate() -> None:
    item = engine()
    start = datetime.now(UTC).timestamp()
    for second in range(3602):
        for update in range(5):
            item.on_ticker(
                "BTCUSDT",
                start + second + update / 10,
                open_interest=100 + second / 1000,
            )
    ticker = item.tickers["BTCUSDT"]
    for minutes in (5, 15, 30, 60):
        assert ticker[f"open_interest_change_{minutes}m_pct"] is not None
    assert len(item.ticker_history["BTCUSDT"]) <= 3901


def test_money_breadth_denominator_uses_actual_coverage() -> None:
    item = engine()
    now = datetime.now(UTC).timestamp()
    item.on_trade("spot", "BTCUSDT", now, "Sell", 100, 1)
    item.on_trade("linear", "BTCUSDT", now, "Buy", 100, 1)
    snapshot = item.snapshot(datetime.now(UTC))
    assert snapshot["money_breadth"]["spot_sales_share"] == 1
    assert snapshot["money_breadth"]["spot_coverage"] == {"valid": 1, "total": 3}
    assert "correlation" not in snapshot
    assert "direction_synchronization" in snapshot


def test_dispatcher_handoff_is_strategy_independent_and_marks_missing_layers() -> None:
    item = engine()
    now = datetime.now(UTC)
    first = item.snapshot(now)["dispatcher_handoff"]
    second = item.snapshot(now)["dispatcher_handoff"]
    assert first == second
    assert first["data_quality"] == "INSUFFICIENT"
    assert first["dispatcher_features"]["market.direction"]["status"] == "NO_DATA"
    assert first["dispatcher_features"]["liquidation.phase"]["status"] == "WARMUP"
    forbidden = {"signals", "positions", "pnl", "portfolio", "decision"}
    assert not forbidden.intersection(first)


def test_dispatcher_feature_confidence_uses_its_own_source_coverage() -> None:
    item = engine()
    now = datetime.now(UTC)
    item.set_instrument_support("linear", set(item.symbols))
    item.set_instrument_support("spot", {"BTCUSDT"})
    item.on_transport("linear", connected=True, timestamp=now.timestamp())
    item.on_transport("spot", connected=True, timestamp=now.timestamp())
    for symbol in item.symbols:
        item.on_trade("linear", symbol, now.timestamp(), "Buy", 100, 1)
    item.on_trade("spot", "BTCUSDT", now.timestamp(), "Buy", 100, 1)
    features = item.snapshot(now)["dispatcher_handoff"]["dispatcher_features"]
    assert features["money.derivatives_pressure"]["confidence"] == 1
    assert features["money.spot_pressure"]["confidence"] == pytest.approx(1 / 3)
    assert features["money.spot_pressure"]["feature_confidence"] == pytest.approx(1 / 3)
    assert features["money.spot_pressure"]["coverage"] == {"valid": 1, "total": 3}
    assert features["money.spot_pressure"]["transport_confidence"] == 1


def test_dispatcher_handoff_exposes_available_market_layers_and_matches_schema() -> None:
    item = engine()
    now = datetime.now(UTC)
    for market in ("spot", "linear"):
        item.set_instrument_support(market, set(item.symbols))
        item.on_transport(market, connected=True, timestamp=now.timestamp())
        for symbol in item.symbols:
            item.on_trade(market, symbol, now.timestamp() - 3600, "Buy", 100, 1)
            item.on_trade(market, symbol, now.timestamp(), "Buy", 101, 2)
            item.on_book(market, symbol, now.timestamp() - 901, [(100, 10)], [(101, 10)])
            item.on_book(market, symbol, now.timestamp(), [(100, 5)], [(101, 10)])
    for symbol in item.symbols:
        item.on_ticker(symbol, now.timestamp() - 301, open_interest=100)
        item.on_ticker(symbol, now.timestamp(), open_interest=101)

    handoff = item.snapshot(now)["dispatcher_handoff"]
    features = handoff["dispatcher_features"]
    for feature_id in (
        "money.pressure",
        "money.spot_derivatives_alignment",
        "positioning.oi_regime",
        "positioning.price_oi_state",
        "liquidity.trend",
        "market.timeframe_alignment",
        "btc.state",
        "eth.state",
    ):
        assert features[feature_id]["status"] == "VALID"
        assert features[feature_id]["observed_at"] == now.isoformat()

    schema_path = (
        Path(__file__).parents[1]
        / "config"
        / "strategy_dispatcher"
        / "MAYAK_HANDOFF_SCHEMA_V1.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert set(handoff) == set(schema["properties"])
    assert set(schema["required"]).issubset(handoff)
    feature_schemas = schema["properties"]["dispatcher_features"]["properties"]
    assert set(features).issubset(feature_schemas)
    for feature_id, payload in features.items():
        feature_schema = feature_schemas[feature_id]
        assert payload["status"] in feature_schema["properties"]["status"]["enum"]
        if payload["status"] == "VALID":
            assert payload["value"] in feature_schema["properties"]["value"]["enum"]


def test_transport_health_is_independent_from_market_trade_activity() -> None:
    item = engine()
    now = datetime.now(UTC)
    item.set_instrument_support("spot", {"BTCUSDT"})
    item.on_transport("spot", connected=True, timestamp=now.timestamp())
    snapshot = item.snapshot(now)
    coin = snapshot["coins"]["BTCUSDT"]
    assert snapshot["transport"]["spot"]["quality"] == SourceQuality.FRESH
    assert coin["quality"]["spot_trades"]["quality"] == SourceQuality.FRESH
    assert coin["quality"]["spot_trades"]["activity_quality"] == SourceQuality.WARMUP


def test_spot_availability_distinguishes_unsupported_and_temporary_outage() -> None:
    item = engine()
    now = datetime.now(UTC)
    item.set_instrument_support("spot", {"BTCUSDT"})
    unavailable = item.snapshot(now)["coins"]
    assert (
        unavailable["BTCUSDT"]["availability"]["spot"]
        == InstrumentAvailability.TEMPORARILY_UNAVAILABLE
    )
    assert (
        unavailable["ADAUSDT"]["availability"]["spot"]
        == InstrumentAvailability.UNSUPPORTED
    )
    item.on_transport("spot", connected=True, timestamp=now.timestamp())
    available = item.snapshot(now)["coins"]
    assert available["BTCUSDT"]["availability"]["spot"] == InstrumentAvailability.SUPPORTED


def test_book_history_retains_time_horizons_independently_of_message_rate() -> None:
    item = engine()
    start = datetime.now(UTC).timestamp()
    for second in range(902):
        for update in range(5):
            timestamp = start + second + update / 10
            item.on_book(
                "linear",
                "BTCUSDT",
                timestamp,
                [(100, 10 + second / 1000)],
                [(101, 10)],
            )
    book = item.books[("linear", "BTCUSDT")]
    assert book["bid_change_15m_pct"] is not None
    assert len(item.book_history[("linear", "BTCUSDT")]) <= 1001


def test_liquidations_stay_in_warmup_then_confirm_real_none() -> None:
    item = engine()
    now = datetime.now(UTC)
    item.set_instrument_support("linear", set(item.symbols))
    item.on_transport("linear", connected=True, timestamp=now.timestamp() - 901)
    item.on_transport("linear", connected=True, timestamp=now.timestamp())
    handoff = item.snapshot(now)["dispatcher_handoff"]["dispatcher_features"]
    assert handoff["liquidation.intensity"]["status"] == "VALID"
    assert handoff["liquidation.intensity"]["value"] == "NONE"
    assert handoff["liquidation.intensity"]["observed_at"] == now.isoformat()


def test_first_liquidation_without_nonzero_baseline_does_not_invent_cascade() -> None:
    item = engine()
    now = datetime.now(UTC)
    item.set_instrument_support("linear", set(item.symbols))
    item.on_transport("linear", connected=True, timestamp=now.timestamp() - 901)
    item.on_transport("linear", connected=True, timestamp=now.timestamp())
    item.on_liquidation("BTCUSDT", now.timestamp() - 10, "Buy", 100, 2)
    snapshot = item.snapshot(now)
    metrics = snapshot["liquidations"]
    assert metrics["current_1m_usd"] == 200
    assert metrics["baseline_nonzero_minutes"] == 0
    assert metrics["status"] == "WARMUP"
    features = snapshot["dispatcher_handoff"]["dispatcher_features"]
    assert features["liquidation.phase"]["status"] == "WARMUP"
    assert features["liquidation.phase"]["observed_at"] is None


def test_liquidation_calibration_uses_only_causal_nonzero_prior_minutes() -> None:
    item = engine()
    now = datetime.now(UTC)
    item.set_instrument_support("linear", set(item.symbols))
    item.on_transport("linear", connected=True, timestamp=now.timestamp() - 4000)
    item.on_transport("linear", connected=True, timestamp=now.timestamp())
    for minute in range(2, 7):
        item.on_liquidation("BTCUSDT", now.timestamp() - minute * 60, "Buy", 100, 2)
    item.on_liquidation("BTCUSDT", now.timestamp() - 10, "Buy", 100, 2)
    metrics = item.snapshot(now)["liquidations"]
    assert metrics["status"] == "VALID"
    assert metrics["baseline_nonzero_minutes"] == 5
    assert metrics["intensity"] == "LOW"
    assert metrics["phase"] != "CASCADE"


def test_mayak_runner_subscribes_and_persists_all_liquidations() -> None:
    runner = Path(__file__).parents[1] / "operations" / "monitoring" / "mayak_v2.py"
    source = runner.read_text(encoding="utf-8")
    assert "allLiquidation.{symbol}" in source
    assert "mayak_v2.liquidations" in source
