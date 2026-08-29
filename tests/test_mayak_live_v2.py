from datetime import UTC, datetime

import pytest

from bybit_workbench.mayak.core.live import LiveMayakEngine, MarketState, SourceQuality


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


def test_open_interest_uses_causal_horizons_not_previous_message() -> None:
    item = engine()
    now = datetime.now(UTC).timestamp()
    item.on_ticker("BTCUSDT", now - 301, open_interest=100)
    item.on_ticker("BTCUSDT", now, open_interest=110)
    ticker = item.snapshot(datetime.now(UTC))["coins"]["BTCUSDT"]["ticker"]
    assert ticker["open_interest_change_5m_pct"] == pytest.approx(10)
    assert ticker["open_interest_change_15m_pct"] is None


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
    assert first["dispatcher_features"]["liquidation.phase"]["status"] == "NO_DATA"
    forbidden = {"signals", "positions", "pnl", "portfolio", "decision"}
    assert not forbidden.intersection(first)


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
