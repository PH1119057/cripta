from datetime import UTC, datetime

import pytest

from bybit_workbench.mayak.core.live import LiveMayakEngine
from bybit_workbench.mayak.research.objective_replay import CausalMayakReplay, MarketEvent


def test_replay_routes_into_same_live_engine() -> None:
    symbols = ("BTCUSDT", "ETHUSDT", "ADAUSDT")
    at = datetime(2026, 9, 6, 3, 0, tzinfo=UTC).timestamp()
    replay = CausalMayakReplay(symbols)
    direct = LiveMayakEngine(symbols, exact_liquidations=False)
    for item in (replay.engine, direct):
        item.set_instrument_support("linear", set(symbols))
        item.on_transport("linear", connected=True, timestamp=at - 3600)
        item.on_transport("linear", connected=True, timestamp=at)
    events = [
        MarketEvent(
            at - 299, "TRADE", "BTCUSDT", "linear", {"side": "Buy", "price": 100, "size": 1}
        ),
        MarketEvent(
            at - 200, "TRADE", "ETHUSDT", "linear", {"side": "Buy", "price": 100, "size": 1}
        ),
        MarketEvent(
            at - 100, "TRADE", "ADAUSDT", "linear", {"side": "Sell", "price": 100, "size": 1}
        ),
        MarketEvent(
            at - 1, "TICKER", "BTCUSDT", payload={"open_interest": 1000, "mark_price": 100}
        ),
    ]
    for event in events:
        replay.feed(event)
        payload = event.payload or {}
        if event.kind == "TRADE":
            direct.on_trade(
                event.market or "",
                event.symbol or "",
                event.event_at,
                str(payload["side"]),
                float(payload["price"]),
                float(payload["size"]),
            )
        elif event.kind == "TICKER":
            direct.on_ticker(
                event.symbol or "", event.event_at, **{k: float(v) for k, v in payload.items()}
            )
    assert replay.snapshot(at) == direct.snapshot(datetime.fromtimestamp(at, UTC))


def test_replay_rejects_out_of_order_event() -> None:
    replay = CausalMayakReplay(("BTCUSDT",))
    replay.feed(
        MarketEvent(100, "TRADE", "BTCUSDT", "linear", {"side": "Buy", "price": 1, "size": 1})
    )
    with pytest.raises(ValueError, match="OUT_OF_ORDER"):
        replay.feed(
            MarketEvent(99, "TRADE", "BTCUSDT", "linear", {"side": "Buy", "price": 1, "size": 1})
        )


def test_replay_rejects_snapshot_before_latest_event() -> None:
    replay = CausalMayakReplay(("BTCUSDT",))
    replay.feed(
        MarketEvent(100, "TRADE", "BTCUSDT", "linear", {"side": "Buy", "price": 1, "size": 1})
    )
    with pytest.raises(ValueError, match="SNAPSHOT_BEFORE_EVENT"):
        replay.snapshot(99)


def test_historical_replay_does_not_invent_exact_liquidations() -> None:
    replay = CausalMayakReplay(("BTCUSDT",), exact_liquidations=False)
    at = 1_788_650_000.0
    replay.set_supported("linear", {"BTCUSDT"})
    replay.feed(MarketEvent(at - 1000, "TRANSPORT", market="linear", payload={"connected": True}))
    replay.feed(MarketEvent(at, "TRANSPORT", market="linear", payload={"connected": True}))
    liquidation = replay.snapshot(at)["coin_market_contexts"]["BTCUSDT"]["payload"]["liquidations"]
    assert liquidation["status"] == "NO_DATA"
    assert liquidation["reason"] == "EXACT_LIQUIDATION_SOURCE_NOT_AVAILABLE"
