"""R01 source-of-truth manifest: exact five-symbol consumer inventory and TTL.

Tests a checked-in, still-inert operator artifact. This test must never
be used as evidence that the producer was enabled or installed.
"""
from __future__ import annotations

import json
from pathlib import Path

from bybit_workbench.market_data.source_required_feed_manifest import (
    RequiredPublicFeed,
    union_public_feed_requirements,
)
from operations.monitoring.public_market_source_union_capture import (
    _closed_repair_windows,
    parse_explicit_source_manifest,
)

_MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "operations" / "monitoring" / "required_public_feeds.json"
)
_SYMBOLS = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT")


def _load() -> tuple[RequiredPublicFeed, ...]:
    payload = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    return tuple(RequiredPublicFeed(**item) for item in payload)


def test_r01_manifest_exact_five_symbol_scope_and_no_unreviewed_defaults() -> None:
    items = _load()
    assert len(items) == 120
    assert {item.symbol for item in items} == set(_SYMBOLS)
    assert {item.consumer_id for item in items} == {
        "ENTRY_P90", "EXIT_P90", "EXIT_SHADOW", "EXECUTION_R1",
        "EXECUTION_P90", "POSITION", "MAYAK",
    }
    assert all(
        item.market_source_id == "ms-bybit-mainnet-public-v1"
        and item.instrument_id ==
        f"inst-bybit-{item.product.lower()}-{item.symbol.lower()}"
        and item.freshness_seconds > 0
        for item in items
    )
    # No physically duplicate consumer rows or accidental public feed expansion.
    identities = [
        (
            item.consumer_id, item.product, item.symbol,
            item.dataset, item.timeframe,
        )
        for item in items
    ]
    assert len(identities) == len(set(identities))
    assert all(
        item.required for item in items
        if item.consumer_id in {"ENTRY_P90", "EXIT_P90", "EXECUTION_R1"}
    )


def test_r01_source_union_is_deduplicated_and_accepted_by_native_parser() -> None:
    union = parse_explicit_source_manifest(
        _MANIFEST.read_text(encoding="utf-8"), native_symbols=_SYMBOLS,
    )
    independently_grouped = union_public_feed_requirements(_load())
    assert union == independently_grouped
    assert union.consumer_requirements == 120
    assert len(union.websocket_topics) == 45
    assert len(union.rest_inputs) == 15
    assert len(union.websocket_batches("SPOT")) == 1
    assert sum(len(batch) for batch in union.websocket_batches("SPOT")) == 10
    assert sum(len(batch) for batch in union.websocket_batches("LINEAR")) == 35
    assert all(
        sum(
            topic.dataset_topic == f"publicTrade.{symbol}"
            for topic in union.websocket_topics if topic.product == "LINEAR"
        ) == 1
        for symbol in _SYMBOLS
    )


def test_r01_exact_history_and_active_consumers_cannot_be_silently_dropped() -> None:
    items = _load()
    for symbol in _SYMBOLS:
        matching = {
            (need.consumer_id, need.dataset, need.timeframe): need
            for need in items if need.symbol == symbol
        }
        assert matching[("ENTRY_P90", "CLOSED_KLINE", "5")].lookback_bars == 2600
        assert matching[("ENTRY_P90", "CLOSED_KLINE", "15")].lookback_bars == 999
        assert matching[("EXIT_P90", "CLOSED_KLINE", "5")].lookback_bars == 2600
        assert matching[("EXIT_P90", "CLOSED_KLINE", "15")].lookback_bars == 999
        assert matching[("EXIT_SHADOW", "CLOSED_KLINE", "5")].lookback_bars == 240
        assert matching[("EXECUTION_R1", "CLOSED_KLINE", "5")].lookback_bars == 240
        assert matching[("POSITION", "FORMING_KLINE", "1")].freshness_seconds == 8
        assert matching[("POSITION", "CLOSED_KLINE", "1")].lookback_bars == 6
        assert matching[("POSITION", "TICKER", "")].freshness_seconds == 2
        assert matching[("POSITION", "ORDERBOOK_50", "")].freshness_seconds == 3
        assert matching[("MAYAK", "ACCOUNT_RATIO", "")].freshness_seconds == 650
        assert matching[("MAYAK", "INSTRUMENT_INFO", "")].freshness_seconds == 1800
        assert matching[("MAYAK", "LIQUIDATION", "")].required is False

    union = parse_explicit_source_manifest(
        _MANIFEST.read_text(encoding="utf-8"), native_symbols=_SYMBOLS,
    )
    windows = _closed_repair_windows(union, native_symbols=_SYMBOLS)
    assert len(windows) == 15
    for window in windows:
        assert window.required == (
            6 if window.key.timeframe == "1"
            else 2600 if window.key.timeframe == "5"
            else 999
        )
        assert window.key.product_category == "LINEAR"
