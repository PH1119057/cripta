"""Real Observer native Bybit WS normalizer feeds the durable P90 codec.

This is a no-order, no-DB pure compatibility test. It does not prove the
full Observer callback/StrategyActivation or historical 90-day parity.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.universal_entry.p90l90_causal_fact_codec import (
    encode_p90l90_fact,
    restore_p90l90_fact,
)
from operations.monitoring.universal_entry_shadow import (
    _trade_cursor,
    _trade_fact,
)


def test_original_observer_bybit_trade_has_exact_durable_native_identity():
    traded_at = datetime(2026, 10, 28, 5, 0, 2, tzinfo=UTC)
    raw = {
        "s": "LTCUSDT", "T": int(traded_at.timestamp() * 1000),
        "i": "native-123-abc", "seq": 123, "p": "99.5000",
        "v": "1.25", "S": "Sell",
    }
    fact = _trade_fact(raw, traded_at+timedelta(milliseconds=100))
    cursor = _trade_cursor(raw)
    event = encode_p90l90_fact(fact, cursor=cursor)
    assert event.event_id == "trade-native-123-abc"
    assert event.exchange_sequence == 123
    assert event.exec_id == "native-123-abc"
    restored, replay_cursor = restore_p90l90_fact(event)
    assert restored == fact
    assert replay_cursor == cursor
    assert restored.source_refs == ("bybit:publicTrade:LTCUSDT:native-123-abc",)
    assert restored.attributes.to_dict()["price"] == "99.5000"


def test_original_observer_exact_native_cursor_does_not_guess_missing_seq():
    traded_at = datetime(2026, 10, 28, 5, 0, 2, tzinfo=UTC)
    raw = {
        "s": "LTCUSDT", "T": int(traded_at.timestamp() * 1000),
        "i": "native-123-abc", "p": "99.5", "v": "1", "S": "Buy",
    }
    fact = _trade_fact(raw, traded_at+timedelta(seconds=1))
    assert fact.fact_id == "trade-native-123-abc"
    with pytest.raises(Exception, match="exact execId/seq/time cursor"):
        _trade_cursor(raw)
