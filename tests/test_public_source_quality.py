"""R04 exact source-quality scope validation, no network or DB mutation."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from bybit_workbench.market_data.public_source_quality import (
    PublicSourceQualityEvent,
    PublicSourceQualityInvalid,
)

AT = datetime(2026, 11, 11, 8, 0, tzinfo=UTC)


def _event(
    *, product: str = "SPOT", topic: str = "*", symbol: str = "",
    quality: str = "NO_DATA",
) -> PublicSourceQualityEvent:
    return PublicSourceQualityEvent.create(
        source_session_id=uuid4().hex, transport_epoch_id=uuid4().hex,
        product=product, topic=topic, symbol=symbol,
        quality=quality, reason="PUBLIC_SPOT_TRANSPORT_UNAVAILABLE",
        observed_at=AT,
    )


@pytest.mark.parametrize(
    ("topic", "symbol"),
    (
        ("publicTrade.APTUSDT", "LTCUSDT"),
        ("orderbook.50.APTUSDT", "DOTUSDT"),
        ("REST:market/account-ratio:APTUSDT", "APTUSDT"),
        ("tickers.LTCUSDT", "LTCUSDT"),
        ("allLiquidation.LTCUSDT", "LTCUSDT"),
        ("kline.7.APTUSDT", "APTUSDT"),
    ),
)
def test_spot_quality_rejects_foreign_or_unsupported_topic(
    topic: str, symbol: str,
) -> None:
    with pytest.raises(
        PublicSourceQualityInvalid, match="QUALITY_TOPIC_SCOPE_INVALID",
    ):
        _event(topic=topic, symbol=symbol)


def test_linear_and_spot_quality_do_not_merge_transport_epochs() -> None:
    a = _event(product="LINEAR", topic="orderbook.50.APTUSDT",
               symbol="APTUSDT", quality="UNKNOWN")
    b = _event(product="SPOT", topic="orderbook.50.APTUSDT",
               symbol="APTUSDT", quality="GAP")
    assert a.instrument_id != b.instrument_id
    assert a.transport_epoch_id != b.transport_epoch_id
    assert a.source_session_id != b.source_session_id


def test_failed_spot_reconnect_is_not_a_ready_proof() -> None:
    e = _event(quality="RECOVERING")
    assert e.quality == "RECOVERING"
    with pytest.raises(
        PublicSourceQualityInvalid, match="QUALITY_EVENT_INVALID",
    ):
        replace(e, quality="READY").validate()


def test_not_listed_requires_exact_instrument_rest_proof() -> None:
    with pytest.raises(
        PublicSourceQualityInvalid, match="NOT_LISTED_PROOF_INVALID",
    ):
        _event(quality="NOT_LISTED")
    e = _event(
        topic="REST:market/instruments-info:LTCUSDT",
        symbol="LTCUSDT", quality="NOT_LISTED",
    )
    assert e.quality == "NOT_LISTED"
