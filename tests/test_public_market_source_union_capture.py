"""R04: existing independent MarketSource owns one exact spot/linear topic union.

No real connections are made in the test; no consumer is rewired or LIVE arm.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bybit_workbench.market_data.source_required_feed_manifest import (
    SourceManifestInvalid,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from operations.monitoring import native_public_trade_source as native
from operations.monitoring.public_market_source_union_capture import (
    _connect_subscribe,
    parse_explicit_source_manifest,
)

P90 = ("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT")


def _required(
    consumer: str, product: str, symbol: str, dataset: str,
    *, interval: str = "5",
) -> dict[str, object]:
    return {
        "consumer_id": consumer,
        "market_source_id": BYBIT_PUBLIC_MARKET_SOURCE_ID,
        "instrument_id": bybit_instrument_id(product.lower(), symbol),
        "product": product,
        "symbol": symbol,
        "dataset": dataset,
        "timeframe": interval if dataset in ("CLOSED_KLINE", "FORMING_KLINE") else "",
        "lookback_bars": 2600 if dataset == "CLOSED_KLINE" else 0,
        "freshness_seconds": 15,
    }


def _manifest() -> str:
    requirements = [
        _required("MAYAK", "SPOT", "BTCUSDT", "PUBLIC_TRADE"),
        _required("MAYAK", "SPOT", "BTCUSDT", "ORDERBOOK_50"),
        _required("MAYAK", "LINEAR", "BTCUSDT", "TICKER"),
        _required("MAYAK", "LINEAR", "BTCUSDT", "LIQUIDATION"),
        _required("ENTRY", "LINEAR", "LTCUSDT", "CLOSED_KLINE"),
        _required("EXIT", "LINEAR", "LTCUSDT", "CLOSED_KLINE"),
    ]
    for s in P90:
        requirements.extend((
            _required("ENTRY", "LINEAR", s, "PUBLIC_TRADE"),
            _required("EXIT", "LINEAR", s, "PUBLIC_TRADE"),
        ))
    return json.dumps(requirements)


def test_required_manifest_subscribes_one_topic_per_product() -> None:
    union = parse_explicit_source_manifest(
        _manifest(), native_symbols=P90,
    )
    spot_topics = {
        x.dataset_topic for x in union.websocket_topics if x.product == "SPOT"
    }
    linear_topics = {
        x.dataset_topic for x in union.websocket_topics if x.product == "LINEAR"
    }
    assert spot_topics == {
        "publicTrade.BTCUSDT", "orderbook.50.BTCUSDT",
    }
    assert "allLiquidation.BTCUSDT" in linear_topics
    assert "tickers.BTCUSDT" in linear_topics
    assert "kline.5.LTCUSDT" in linear_topics
    assert all(
        sum(x.dataset_topic == f"publicTrade.{s}"
            for x in union.websocket_topics if x.product == "LINEAR") == 1
        for s in P90
    )


@pytest.mark.parametrize("addition,reason", [
    (
        _required("POSITION", "SPOT", "BTCUSDT",
                  "FORMING_KLINE", interval="1"),
        "FORMING_SCOPE_UNSUPPORTED",
    ),
    (
        _required("POSITION", "LINEAR", "BTCUSDT",
                  "FORMING_KLINE", interval="5"),
        "FORMING_SCOPE_UNSUPPORTED",
    ),
])
def test_missing_required_physical_adapter_blocks_source_activation(
    addition: dict[str, object], reason: str,
) -> None:
    manifest = json.loads(_manifest())
    manifest.append(addition)
    with pytest.raises(SourceManifestInvalid, match=reason):
        parse_explicit_source_manifest(json.dumps(manifest), native_symbols=P90)


def test_spot_closed_kline_subscribes_once_in_same_source_process() -> None:
    requirements = json.loads(_manifest())
    requirements.extend((
        _required("MAYAK", "SPOT", "BTCUSDT", "CLOSED_KLINE", interval="1"),
        _required("POSITION", "SPOT", "BTCUSDT", "CLOSED_KLINE", interval="1"),
        _required("MAYAK", "LINEAR", "BTCUSDT", "CLOSED_KLINE", interval="1"),
    ))
    union = parse_explicit_source_manifest(
        json.dumps(requirements), native_symbols=P90,
    )
    assert sum(
        x.product == "SPOT" and x.dataset_topic == "kline.1.BTCUSDT"
        for x in union.websocket_topics
    ) == 1
    assert sum(
        x.product == "LINEAR" and x.dataset_topic == "kline.1.BTCUSDT"
        for x in union.websocket_topics
    ) == 1


def test_public_rest_feeds_are_deduplicated_without_new_websocket_topics() -> None:
    requirements = json.loads(_manifest())
    requirements.extend((
        _required("MAYAK", "LINEAR", "LTCUSDT", "ACCOUNT_RATIO"),
        _required("ENTRY", "LINEAR", "LTCUSDT", "ACCOUNT_RATIO"),
        _required("MAYAK", "SPOT", "BTCUSDT", "INSTRUMENT_INFO"),
    ))
    union = parse_explicit_source_manifest(
        json.dumps(requirements), native_symbols=P90,
    )
    assert len(union.rest_inputs) == 2
    assert {
        tuple(sorted(x.consumers)): x.dataset_topic for x in union.rest_inputs
    } == {
        ("ENTRY", "MAYAK"): "REST:market/account-ratio:LTCUSDT",
        ("MAYAK",): "REST:market/instruments-info:BTCUSDT",
    }
    assert all(
        "REST:" not in item.dataset_topic for item in union.websocket_topics
    )


def test_exact_linear_one_minute_forming_shares_existing_physical_kline_topic() -> None:
    requirements = json.loads(_manifest())
    requirements.extend((
        _required("ENTRY", "LINEAR", "LTCUSDT", "FORMING_KLINE", interval="1"),
        _required("EXIT", "LINEAR", "LTCUSDT", "FORMING_KLINE", interval="1"),
        _required("ENTRY", "LINEAR", "LTCUSDT", "CLOSED_KLINE", interval="1"),
    ))
    union = parse_explicit_source_manifest(
        json.dumps(requirements), native_symbols=P90,
    )
    assert sum(
        1 for topic in union.websocket_topics
        if topic.product == "LINEAR"
        and topic.dataset_topic == "kline.1.LTCUSDT"
    ) == 1
    assert sum(
        1 for fact in union.requirements if fact.dataset == "FORMING_KLINE"
    ) == 2


def test_missing_original_p90_native_subscription_fails_closed() -> None:
    manifest = [
        row for row in json.loads(_manifest())
        if not (
            row["dataset"] == "PUBLIC_TRADE"
            and row["symbol"] == "APTUSDT"
        )
    ]
    with pytest.raises(SourceManifestInvalid, match="P90_NATIVE_TOPIC_MISSING"):
        parse_explicit_source_manifest(json.dumps(manifest), native_symbols=P90)


def test_existing_source_remains_default_off_and_legacy_is_unmodified() -> None:
    path = Path("operations/monitoring/native_public_trade_source.py")
    script = path.read_text(encoding="utf-8")
    assert 'os.environ.get("CRIPTA_SOURCE_UNION_CAPTURE", "0")' in script
    assert "_run_stream(connection, symbols)" in script
    assert "run_source_union(" in script
    assert "cripta-private-runtime" not in script
    assert native._source_feature_enabled("0") is False


def test_single_websocket_per_category_batches_and_acks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    union = parse_explicit_source_manifest(_manifest(), native_symbols=P90)
    sent: list[dict[str, object]] = []
    sockets: list[object] = []

    class Socket:
        def settimeout(self, _value: float) -> None:
            pass

        def send(self, raw: str) -> None:
            sent.append(json.loads(raw))

        def recv(self) -> str:
            if not sent:
                return ""
            command = sent.pop(0)
            return json.dumps({
                "op": "subscribe", "req_id": command["req_id"],
                "success": True, "retCode": 0,
            })

        def close(self) -> None:
            pass

    def connect(*_args: object, **_kw: object) -> Socket:
        sock = Socket()
        sockets.append(sock)
        return sock

    from operations.monitoring import public_market_source_union_capture as source

    monkeypatch.setattr(source.websocket, "create_connection", connect)
    socket, early = _connect_subscribe(product="SPOT", union=union)
    assert socket in sockets
    assert early == []
    assert len(sockets) == 1
