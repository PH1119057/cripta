from datetime import UTC, datetime

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
