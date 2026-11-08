"""Dedicated publicTrade MarketSource producer: exact native ID and fail-closed tests."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from operations.monitoring.native_public_trade_source import (
    NativeSourceInputUnverified,
    decode_native_trades,
)

RELEASE = "a" * 40
SYMBOLS = frozenset(("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT"))


def _trade(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "s": "LTCUSDT", "i": "test-exec-1", "seq": 1234,
        "T": 1794150000123, "p": "51.75", "v": "0.12", "S": "Buy",
    }
    row.update(overrides)
    return row


def test_real_bybit_public_trade_identity_survives_independent_capture() -> None:
    rows = decode_native_trades(
        {"topic": "publicTrade.LTCUSDT", "data": [_trade()]},
        symbols=SYMBOLS, release=RELEASE,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.external_symbol == "LTCUSDT"
    assert row.exec_id == "test-exec-1" and row.exchange_sequence == 1234
    assert row.event_at == datetime.fromtimestamp(1794150000.123, UTC)
    assert row.price == Decimal("51.75")
    assert row.quantity == Decimal("0.12")
    assert row.taker_side == "Buy"
    assert row.producer_release_commit == RELEASE


@pytest.mark.parametrize("message", [
    {"topic": "publicTrade.LTCUSDT", "data": [_trade(i="")]},
    {"topic": "publicTrade.LTCUSDT", "data": [_trade(seq=-1)]},
    {"topic": "publicTrade.LTCUSDT", "data": [_trade(T=0)]},
    {"topic": "publicTrade.LTCUSDT", "data": [_trade(v="0")]},
    {"topic": "publicTrade.LTCUSDT", "data": [_trade(S="Middle")]},
    {"topic": "publicTrade.LTCUSDT", "data": [_trade(s="BTCUSDT")]},
    {"topic": "publicTrade.INJUSDT", "data": [_trade()]},
    {"topic": "publicTrade.LTCUSDT", "data": "not-a-list"},
    {"topic": "publicTrade.LTCUSDT", "data": [_trade(), _trade(i="test-exec-1")]},
])
def test_invalid_native_provider_messages_never_become_recorded_facts(
    message: dict[str, object],
) -> None:
    with pytest.raises(NativeSourceInputUnverified):
        decode_native_trades(message, symbols=SYMBOLS, release=RELEASE)


def test_unrelated_market_topics_are_not_native_evidence() -> None:
    assert decode_native_trades(
        {"topic": "kline.5.LTCUSDT", "data": [_trade()]},
        symbols=SYMBOLS, release=RELEASE,
    ) == ()


def test_source_transport_requires_exact_release() -> None:
    with pytest.raises(NativeSourceInputUnverified):
        decode_native_trades(
            {"topic": "publicTrade.LTCUSDT", "data": [_trade()]},
            symbols=SYMBOLS, release="stale",
        )
