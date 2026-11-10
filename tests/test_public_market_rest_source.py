"""R04 public REST exact-provider evidence; no private key or Exchange action."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.market_data.public_market_rest_source import (
    PublicMarketRestInvalid,
    decode_public_rest_response,
    fetch_public_market_rest,
)

NOW = datetime(2026, 11, 10, 15, 12, tzinfo=UTC)
SOURCE = int((NOW - timedelta(minutes=2)).timestamp() * 1000)


def _ratio(**row: object) -> dict:
    data = {
        "symbol": "LTCUSDT",
        "buyRatio": "0.5431", "sellRatio": "0.4569",
        "timestamp": str(SOURCE),
    }
    data.update(row)
    return {"retCode": 0, "result": {"list": [data]}}


def _info(category: str = "spot", **row: object) -> dict:
    data: dict[str, object] = {
        "symbol": "LTCUSDT", "status": "Trading",
        "priceFilter": {"tickSize": "0.001"},
        "lotSizeFilter": {"qtyStep": "0.01"},
    }
    data.update(row)
    return {
        "retCode": 0,
        "result": {"category": category, "list": [data]},
    }


def _decode(payload: dict, dataset: str = "ACCOUNT_RATIO",
            product: str = "LINEAR"):
    return decode_public_rest_response(
        payload, dataset=dataset, product=product,
        symbol="LTCUSDT", received_at=NOW,
    )


def test_ratio_preserves_original_sample_time_not_receive_time() -> None:
    fact = _decode(_ratio())
    assert fact.source_at == datetime.fromtimestamp(SOURCE / 1000, UTC)
    assert fact.received_at == NOW
    assert fact.interval == "5min"
    assert fact.product == "LINEAR"
    assert fact.values == {
        "long_account_ratio": "0.5431",
        "short_account_ratio": "0.4569",
    }


def test_instrument_exact_spot_metadata_no_bulk_guess_or_fake_trading() -> None:
    fact = _decode(_info(), dataset="INSTRUMENT_INFO", product="SPOT")
    assert fact.instrument_id == "inst-bybit-spot-ltcusdt"
    assert fact.source_at == NOW
    assert fact.values == {
        "status": "Trading", "tick_size": "0.001", "qty_step": "0.01",
    }
    absent = _decode(
        {"retCode": 0, "result": {"category": "spot", "list": []}},
        dataset="INSTRUMENT_INFO", product="SPOT",
    )
    assert absent.values == {"status": "NOT_LISTED"}


def test_nontrading_provider_metadata_not_misclassified_trading() -> None:
    fact = _decode(
        _info("linear", status="PreLaunch"),
        dataset="INSTRUMENT_INFO",
    )
    assert fact.values["status"] == "PreLaunch"


@pytest.mark.parametrize("payload,reason", [
    (_ratio(symbol="BTCUSDT"), "SYMBOL"),
    (_ratio(buyRatio="NaN"), "DECIMAL"),
    (_ratio(buyRatio="-0.4"), "DECIMAL"),
    (_ratio(buyRatio="0.1", sellRatio="0.3"), "SUM"),
    (_ratio(timestamp="foo"), "TIME"),
    (_ratio(timestamp=str(int((NOW + timedelta(minutes=3)).timestamp() * 1000))), "FUTURE"),
    ({"retCode": 10001, "result": {"list": []}}, "NOT_OK"),
    ({"retCode": False, "result": {"list": []}}, "NOT_OK"),
    ({"retCode": 0, "result": {"list": []}}, "RATIO_MISSING"),
])
def test_invalid_ratio_provider_response_is_not_evidence(payload, reason) -> None:
    with pytest.raises(PublicMarketRestInvalid, match=reason):
        _decode(payload)


def test_ratio_is_not_spot_and_metadata_never_silently_assumes_status() -> None:
    with pytest.raises(PublicMarketRestInvalid, match="CATEGORY"):
        _decode(_ratio(), product="SPOT")
    with pytest.raises(PublicMarketRestInvalid, match="STATUS"):
        _decode(
            _info("linear", status=None),
            dataset="INSTRUMENT_INFO",
        )
    with pytest.raises(PublicMarketRestInvalid, match="STATUS"):
        _decode(
            _info("linear", status=["Trading"]),
            dataset="INSTRUMENT_INFO",
        )


def test_wrong_instrument_category_or_multirow_invalid() -> None:
    with pytest.raises(PublicMarketRestInvalid, match="CATEGORY"):
        _decode(_info("spot"), dataset="INSTRUMENT_INFO")
    with pytest.raises(PublicMarketRestInvalid, match="EXACT_SYMBOL"):
        _decode(
            {"retCode": 0, "result": {"category": "spot", "list": [
                {"symbol": "LTCUSDT", "status": "Trading"},
                {"symbol": "BTCUSDT", "status": "Trading"},
            ]}},
            dataset="INSTRUMENT_INFO", product="SPOT",
        )


def test_http_source_uses_fixed_public_host_exact_symbol_no_spot_cursor() -> None:
    recorded: list[tuple[str, float, dict[str, str]]] = []

    class Response:
        def __init__(self, payload: dict) -> None:
            self.data = json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self, _size: int) -> bytes:
            return self.data

    def opener(req, *, timeout: float):
        recorded.append((req.full_url, timeout, dict(req.header_items())))
        if "account-ratio" in req.full_url:
            return Response(_ratio())
        return Response(_info())

    ratio = fetch_public_market_rest(
        dataset="ACCOUNT_RATIO", product="LINEAR", symbol="LTCUSDT",
        received_at=lambda: NOW, opener=opener,
    )
    info = fetch_public_market_rest(
        dataset="INSTRUMENT_INFO", product="SPOT", symbol="LTCUSDT",
        received_at=lambda: NOW, opener=opener,
    )
    assert ratio.dataset == "ACCOUNT_RATIO"
    assert info.dataset == "INSTRUMENT_INFO"
    assert recorded[0][0].startswith(
        "https://api.bybit.kz/v5/market/account-ratio?"
    )
    assert "period=5min" in recorded[0][0]
    assert "limit=1" in recorded[0][0]
    assert recorded[1][0].startswith(
        "https://api.bybit.kz/v5/market/instruments-info?"
    )
    assert "cursor=" not in recorded[1][0]
    assert "limit=" not in recorded[1][0]
    assert all("X-BAPI-API-KEY" not in str(x) for x in recorded)


def test_public_only_rest_never_calls_unknown_endpoint() -> None:
    with pytest.raises(PublicMarketRestInvalid, match="UNAUTHORIZED"):
        fetch_public_market_rest(
            dataset="PRIVATE_POSITION", product="LINEAR", symbol="LTCUSDT",
            received_at=lambda: NOW,
        )
