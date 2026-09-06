import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bybit_workbench.mayak.core.live import LiveMayakEngine, SourceQuality


def fixed_now() -> datetime:
    return datetime(2026, 9, 6, 2, 30, tzinfo=UTC)


def prepared_engine() -> LiveMayakEngine:
    item = LiveMayakEngine(("BTCUSDT", "ETHUSDT", "ADAUSDT"))
    now = fixed_now().timestamp()
    for market in ("spot", "linear"):
        item.set_instrument_support(market, set(item.symbols))
        item.on_transport(market, connected=True, timestamp=now - 3600)
        item.on_transport(market, connected=True, timestamp=now)
    return item


def test_connected_transport_does_not_invent_fresh_activity() -> None:
    item = prepared_engine()
    snapshot = item.snapshot(fixed_now())
    quality = snapshot["coins"]["ADAUSDT"]["quality"]["spot_trades"]
    assert quality["transport_quality"] == SourceQuality.FRESH
    assert quality["activity_quality"] == SourceQuality.WARMUP
    assert quality["quality"] == SourceQuality.WARMUP
    feature = snapshot["dispatcher_handoff"]["dispatcher_features"]["money.spot_pressure"]
    assert feature["status"] == "NO_DATA"
    assert feature["coverage"] == {"valid": 0, "total": 3}
    assert feature["confidence"] == 0


def test_flow_context_has_causal_current_and_prior_windows() -> None:
    item = prepared_engine()
    now = fixed_now().timestamp()
    # Prior 5m window: +100 USD. Current 5m window: +300 - 50 = +250 USD.
    item.on_trade("linear", "ADAUSDT", now - 590, "Buy", 100, 1)
    item.on_trade("linear", "ADAUSDT", now - 200, "Buy", 100, 3)
    item.on_trade("linear", "ADAUSDT", now - 100, "Sell", 50, 1)
    flow = item.snapshot(fixed_now())["coin_market_contexts"]["ADAUSDT"]["payload"]["money"]
    current = flow["derivatives"]["5m"]
    assert current["net_usd"] == pytest.approx(250)
    assert current["prior_turnover_usd"] == pytest.approx(100)
    assert current["speed_usd_per_min"] == pytest.approx(50)
    assert current["acceleration_usd_per_min2"] == pytest.approx(6)
    assert current["net_share"] == pytest.approx(250 / 350)


def test_flow_context_60m_keeps_full_prior_60m_window() -> None:
    item = prepared_engine()
    now = fixed_now().timestamp()
    # Previous 60m: +120 USD at T-7000. Current 60m: +300 USD at T-100.
    # The prior row must survive until the snapshot, so retention must exceed 60m.
    item.on_trade("linear", "ADAUSDT", now - 7000, "Buy", 120, 1)
    item.on_trade("linear", "ADAUSDT", now - 100, "Buy", 100, 3)
    flow = item.snapshot(fixed_now())["coin_market_contexts"]["ADAUSDT"]["payload"]["money"]
    current = flow["derivatives"]["60m"]
    assert current["net_usd"] == pytest.approx(300)
    assert current["prior_turnover_usd"] == pytest.approx(120)
    assert current["speed_usd_per_min"] == pytest.approx(5)
    assert current["acceleration_usd_per_min2"] == pytest.approx((5 - 2) / 60)
    assert current["turnover_ratio_to_prior"] == pytest.approx(2.5)


def test_past_snapshot_object_is_not_mutated_by_later_event() -> None:
    item = prepared_engine()
    now = fixed_now().timestamp()
    item.on_trade("linear", "BTCUSDT", now - 10, "Buy", 100, 1)
    before = item.snapshot(fixed_now())["coin_market_contexts"]["BTCUSDT"]
    frozen = json.loads(json.dumps(before, ensure_ascii=False, default=str))
    item.on_trade("linear", "BTCUSDT", now + 30, "Sell", 100, 100)
    assert json.loads(json.dumps(before, ensure_ascii=False, default=str)) == frozen


def test_same_event_stream_produces_same_context_in_two_engines() -> None:
    now = fixed_now()
    outputs = []
    for _ in range(2):
        item = prepared_engine()
        for symbol in item.symbols:
            item.on_trade("linear", symbol, now.timestamp() - 300, "Buy", 100, 1)
            item.on_trade("linear", symbol, now.timestamp() - 10, "Buy", 101, 2)
        outputs.append(item.snapshot(now)["coin_market_contexts"])
    assert outputs[0] == outputs[1]


def test_open_interest_speed_and_acceleration_are_causal() -> None:
    item = prepared_engine()
    now = fixed_now().timestamp()
    item.on_ticker("BTCUSDT", now - 601, open_interest=100)
    item.on_ticker("BTCUSDT", now - 301, open_interest=105)
    item.on_ticker("BTCUSDT", now, open_interest=115)
    positioning = item.snapshot(fixed_now())["coin_market_contexts"]["BTCUSDT"]["payload"][
        "positioning"
    ]
    current_change = (115 / 105 - 1) * 100
    previous_change = (105 / 100 - 1) * 100
    assert positioning["open_interest_change_5m_pct"] == pytest.approx(current_change)
    assert positioning["open_interest_speed_5m_pct_per_min"] == pytest.approx(current_change / 5)
    assert positioning["open_interest_acceleration_5m_pct_per_min2"] == pytest.approx(
        (current_change / 5 - previous_change / 5) / 5
    )


def test_positioning_exposes_premium_without_trading_interpretation() -> None:
    item = prepared_engine()
    now = fixed_now().timestamp()
    item.on_ticker(
        "BTCUSDT",
        now,
        last_price=102,
        mark_price=101,
        index_price=100,
        open_interest=10,
        open_interest_value=1010,
        funding_rate=0.0001,
    )
    positioning = item.snapshot(fixed_now())["coin_market_contexts"]["BTCUSDT"]["payload"][
        "positioning"
    ]
    assert positioning["open_interest_value"] == 1010
    assert positioning["mark_index_premium_pct"] == pytest.approx(1)
    assert positioning["last_index_premium_pct"] == pytest.approx(2)
    assert positioning["last_mark_premium_pct"] == pytest.approx((102 / 101 - 1) * 100)


def test_book_context_keeps_near_price_depth() -> None:
    item = prepared_engine()
    now = fixed_now().timestamp()
    item.on_book(
        "linear",
        "BTCUSDT",
        now,
        [(100.0, 10), (99.95, 20), (99.0, 100)],
        [(100.1, 10), (100.15, 20), (101.0, 100)],
    )
    book = item.snapshot(fixed_now())["coin_market_contexts"]["BTCUSDT"]["payload"][
        "liquidity"
    ]["derivatives"]
    assert book["mid_price"] == pytest.approx(100.05)
    assert book["bid_depth_50bps_usd"] < book["bid_usd"]
    assert book["ask_depth_50bps_usd"] < book["ask_usd"]


def test_per_coin_liquidations_keep_long_short_separate() -> None:
    item = prepared_engine()
    now = fixed_now().timestamp()
    # Build a causal non-zero calibration for BTC only.
    for minute in range(2, 7):
        item.on_liquidation("BTCUSDT", now - minute * 60, "Buy", 100, 1)
    item.on_liquidation("BTCUSDT", now - 20, "Buy", 100, 2)
    item.on_liquidation("BTCUSDT", now - 10, "Sell", 100, 3)
    contexts = item.snapshot(fixed_now())["coin_market_contexts"]
    btc = contexts["BTCUSDT"]["payload"]["liquidations"]
    ada = contexts["ADAUSDT"]["payload"]["liquidations"]
    assert btc["status"] == "VALID"
    assert btc["1m"]["long_notional_usd"] == 200
    assert btc["1m"]["short_notional_usd"] == 300
    assert btc["1m"]["long_count"] == 1
    assert btc["1m"]["short_count"] == 1
    assert ada["status"] == "VALID"
    assert ada["phase"] == "NONE"


def test_relative_strength_is_panel_relative_not_strategy_relative() -> None:
    item = prepared_engine()
    now = fixed_now().timestamp()
    for symbol, start, end in (
        ("BTCUSDT", 100, 101),
        ("ETHUSDT", 100, 102),
        ("ADAUSDT", 100, 104),
    ):
        item.on_trade("linear", symbol, now - 299, "Buy", start, 1)
        item.on_trade("linear", symbol, now - 1, "Buy", end, 1)
    relative = item.snapshot(fixed_now())["coin_market_contexts"]["ADAUSDT"]["payload"][
        "relative_strength"
    ]["5m"]
    assert relative["coin_return_pct"] == pytest.approx(4)
    assert relative["panel_median_return_pct"] == pytest.approx(2)
    assert relative["relative_to_panel_pct"] == pytest.approx(2)
    assert relative["relative_to_btc_pct"] == pytest.approx(3)


def test_coin_market_context_contains_no_trading_command_or_strategy_outcome() -> None:
    context = prepared_engine().snapshot(fixed_now())["coin_market_contexts"]["BTCUSDT"]
    serialized = json.dumps(context, ensure_ascii=False, default=str).lower()
    for forbidden in ("signal_id", "position_id", "pnl", "entry_decision", "strategycoinfit"):
        assert forbidden not in serialized
    assert context["provenance"]["trading_command"] is False
    assert context["schema_version"] == "coin-market-context-v1"
    assert context["engine_version"] == "mayak-v2.2"


def test_spot_subscription_limit_and_ack_contract_are_explicit() -> None:
    root = Path(__file__).parents[1]
    source = (root / "operations/monitoring/mayak_v2.py").read_text(encoding="utf-8")
    assert 'SUBSCRIBE_BATCH_LIMIT = {"spot": 10, "linear": 30}' in source
    assert '"req_id": req_id' in source
    assert "MAYAK_SUBSCRIPTION_REJECTED" in source


def test_coin_context_migration_is_append_only_and_no_trade_command() -> None:
    root = Path(__file__).parents[1]
    sql = (root / "operations/sql/20260906_mayak_coin_market_context_v1.sql").read_text(
        encoding="utf-8"
    )
    assert "runtime.reject_immutable_change()" in sql
    assert "GRANT SELECT, INSERT ON mayak_v2.coin_market_contexts TO cripta" in sql
    assert "trading_command" in sql
    assert "UPDATE mayak_v2.coin_market_contexts" not in sql
