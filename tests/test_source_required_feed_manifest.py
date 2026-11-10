"""R01/R04 source union: 20 MAYAK coins + five P90; no redundant sockets.

This is the approved-feed *planner*, NOT proof of active producer capture.
It intentionally starts no public WS, no private connection and no DB writer.
"""
from __future__ import annotations

import pytest

from bybit_workbench.market_data.source_required_feed_manifest import (
    RequiredPublicFeed,
    SourceManifestInvalid,
    union_public_feed_requirements,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from operations.monitoring.mayak_v2 import DEFAULT_SYMBOLS

P90 = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT")


def _need(
    consumer: str, category: str, symbol: str, dataset: str,
    interval: str = "", bars: int = 0, ttl: int = 10,
) -> RequiredPublicFeed:
    return RequiredPublicFeed(
        consumer_id=consumer,
        market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
        instrument_id=bybit_instrument_id(category.lower(), symbol),
        product=category,
        symbol=symbol, dataset=dataset, timeframe=interval,
        lookback_bars=bars, freshness_seconds=ttl,
    )


def test_physically_one_feed_serves_multiple_entry_exit_and_mayak_consumers() -> None:
    needs = (
        _need("ENTRY", "LINEAR", "LTCUSDT", "PUBLIC_TRADE"),
        _need("EXIT", "LINEAR", "LTCUSDT", "PUBLIC_TRADE"),
        _need("MAYAK", "LINEAR", "LTCUSDT", "PUBLIC_TRADE"),
        _need("ENTRY", "LINEAR", "LTCUSDT", "CLOSED_KLINE", "5", 2600),
        _need("EXIT", "LINEAR", "LTCUSDT", "CLOSED_KLINE", "5", 240),
        _need("POSITION", "LINEAR", "LTCUSDT", "FORMING_KLINE", "5", 2),
        _need("MAYAK", "LINEAR", "LTCUSDT", "ORDERBOOK_50"),
        _need("MAYAK", "SPOT", "LTCUSDT", "PUBLIC_TRADE"),
        _need("ENTRY", "LINEAR", "LTCUSDT", "ACCOUNT_RATIO"),
    )
    union = union_public_feed_requirements(needs)
    assert union.consumer_requirements == 9
    linear = [r for r in union.websocket_topics if r.product == "LINEAR"]
    assert {x.dataset_topic for x in linear} == {
        "publicTrade.LTCUSDT", "kline.5.LTCUSDT",
        "orderbook.50.LTCUSDT",
    }
    assert next(
        r for r in linear if r.dataset_topic == "publicTrade.LTCUSDT"
    ).consumers == ("ENTRY", "EXIT", "MAYAK")
    assert next(
        r for r in linear if r.dataset_topic == "kline.5.LTCUSDT"
    ).consumers == ("ENTRY", "EXIT", "POSITION")
    assert len(union.rest_inputs) == 1
    assert union.rest_inputs[0].dataset_topic.startswith("REST:")
    assert union.websocket_batches("SPOT") == (("publicTrade.LTCUSDT",),)


def test_all_twenty_existing_mayak_symbols_and_p90_are_covered_without_dup() -> None:
    assert len(DEFAULT_SYMBOLS) == 20
    assert set(P90).issubset(set(DEFAULT_SYMBOLS))
    needs: list[RequiredPublicFeed] = []
    for sym in DEFAULT_SYMBOLS:
        for product in ("SPOT", "LINEAR"):
            needs.extend((
                _need("MAYAK", product, sym, "PUBLIC_TRADE"),
                _need("MAYAK", product, sym, "ORDERBOOK_50"),
            ))
        needs.extend((
            _need("MAYAK", "LINEAR", sym, "TICKER"),
            _need("MAYAK", "LINEAR", sym, "LIQUIDATION"),
        ))
    for sym in P90:
        needs.extend((
            _need("ENTRY", "LINEAR", sym, "PUBLIC_TRADE"),
            _need("EXIT", "LINEAR", sym, "PUBLIC_TRADE"),
            _need("ENTRY", "LINEAR", sym, "CLOSED_KLINE", "5", 2600),
            _need("ENTRY", "LINEAR", sym, "CLOSED_KLINE", "15", 1000),
            _need("EXIT", "LINEAR", sym, "CLOSED_KLINE", "5", 240),
        ))
    union = union_public_feed_requirements(tuple(needs))
    spot = [x for x in union.websocket_topics if x.product == "SPOT"]
    linear = [x for x in union.websocket_topics if x.product == "LINEAR"]
    assert len(spot) == 40
    assert len(linear) == 90
    assert len(union.rest_inputs) == 0
    assert sum(len(batch) for batch in union.websocket_batches("SPOT")) == 40
    assert sum(len(batch) for batch in union.websocket_batches("LINEAR")) == 90
    assert all(len(batch) <= 10 for batch in union.websocket_batches("SPOT"))
    assert all(len(batch) <= 30 for batch in union.websocket_batches("LINEAR"))
    ltc = next(
        item for item in linear
        if item.dataset_topic == "publicTrade.LTCUSDT"
    )
    assert ltc.consumers == ("ENTRY", "EXIT", "MAYAK")


@pytest.mark.parametrize("changes", [
    {"market_source_id": "external-source"},
    {"instrument_id": "inst-bybit-spot-ltcusdt"},
    {"symbol": "LtcUsdt"},
    {"freshness_seconds": 0},
    {"consumer_id": ""},
    {"timeframe": "invalid"},
    {"lookback_bars": 30001},
])
def test_invalid_input_never_becomes_subscription(changes: dict[str, object]) -> None:
    args: dict[str, object] = {
        "consumer_id": "ENTRY",
        "market_source_id": BYBIT_PUBLIC_MARKET_SOURCE_ID,
        "instrument_id": bybit_instrument_id("linear", "LTCUSDT"),
        "product": "LINEAR",
        "symbol": "LTCUSDT",
        "dataset": "CLOSED_KLINE",
        "timeframe": "5",
        "lookback_bars": 2600,
        "freshness_seconds": 10,
    }
    args.update(changes)
    with pytest.raises(SourceManifestInvalid):
        RequiredPublicFeed(**args)


def test_liquidations_are_linear_only_and_ratio_is_not_fake_ws() -> None:
    with pytest.raises(SourceManifestInvalid):
        _need("MAYAK", "SPOT", "BTCUSDT", "LIQUIDATION")
    union = union_public_feed_requirements((
        _need("MAYAK", "LINEAR", "BTCUSDT", "ACCOUNT_RATIO"),
        _need("MAYAK", "LINEAR", "BTCUSDT", "TICKER"),
    ))
    assert len(union.websocket_topics) == 1
    assert len(union.rest_inputs) == 1


def test_manifest_empty_or_unsupported_never_claims_a_live_source() -> None:
    with pytest.raises(SourceManifestInvalid):
        union_public_feed_requirements(())
    with pytest.raises(SourceManifestInvalid):
        _need("ENTRY", "LINEAR", "LTCUSDT", "CLOSED_KLINE", "7")
