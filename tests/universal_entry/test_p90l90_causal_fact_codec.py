"""P90L90 transport codec: exact normalized events, no Exchange or runtime DB."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.universal_entry.contracts import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_entry.p90l90_causal_fact_codec import (
    encode_p90l90_fact,
    restore_p90l90_fact,
)
from bybit_workbench.universal_entry.p90l90_causal_transport_join import (
    P90L90CausalTransportJoin,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

B = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)


def _trade(*, second: int = 1, symbol: str = "LTCUSDT", seq: int = 12):
    at = B + timedelta(seconds=second)
    return MarketFactEnvelope(
        fact_id=f"native-{seq}", event_kind="PUBLIC_TRADE", symbol=symbol,
        observed_at=at, event_at=at, received_at=at+timedelta(milliseconds=40),
        source_refs=(f"bybit:publicTrade:{symbol}:{seq}",),
        attributes=FrozenPolicy.from_mapping({
            "price": "100.25", "size": "12.5", "taker_side": "Buy",
        }),
    ), TradeCursor(symbol, f"bybit-exec-{seq}", seq, at)


def _bar(tf: str, *, at: datetime = B, symbol: str = "LTCUSDT"):
    return MarketFactEnvelope(
        fact_id=f"bar-{tf}-{int(at.timestamp())}",
        event_kind="CANDLE_CLOSED", symbol=symbol,
        observed_at=at, event_at=at, received_at=at+timedelta(seconds=2),
        source_refs=(f"bybit:kline.{tf}:{symbol}:{at.isoformat()}",),
        attributes=FrozenPolicy.from_mapping({
            "timeframe": tf,
            "opened_at": (at-timedelta(minutes=int(tf))).isoformat(),
            "closed_at": at.isoformat(), "open": "100", "high": "101",
            "low": "99", "close": "100.5", "volume": "400",
        }),
    )


def test_native_trade_and_both_bars_roundtrip_verbatim():
    inputs = [_trade(), (_bar("5"), None), (_bar("15"), None)]
    for fact, cursor in inputs:
        envelope = encode_p90l90_fact(fact, cursor=cursor)
        restored, recovered_cursor = restore_p90l90_fact(envelope)
        assert restored == fact
        assert recovered_cursor == cursor
        assert json.loads(envelope.source_json)["attributes"] == fact.attributes.to_dict()


def test_delayed_transport_releases_5m_15m_then_exact_trade_after_restart():
    fact, cursor = _trade()
    native = encode_p90l90_fact(fact, cursor=cursor)
    close_15 = encode_p90l90_fact(_bar("15"))
    close_5 = encode_p90l90_fact(_bar("5"))
    gate = P90L90CausalTransportJoin(
        symbol="LTCUSDT", closed_5m_at=B-timedelta(minutes=5),
        closed_15m_at=B-timedelta(minutes=15),
    )
    assert gate.accept(native) == ()
    assert gate.accept(close_15) == ()
    gate = P90L90CausalTransportJoin.restore(gate.snapshot())
    released = gate.accept(close_5)
    assert released == (close_5, close_15, native)
    assert [restore_p90l90_fact(item)[0].fact_id for item in released] == [
        close_5.event_id, close_15.event_id, fact.fact_id,
    ]
    # Committed native cursor re-delivery must not repeat Entry or Exit.
    assert gate.accept(native) == ()


def test_corrupt_record_cannot_be_dispatched_as_another_trade_or_candle():
    fact, cursor = _trade()
    envelope = encode_p90l90_fact(fact, cursor=cursor)
    payload = json.loads(envelope.source_json)
    payload["attributes"]["price"] = "99.8"
    with pytest.raises(ValueError, match="roundtrip identity"):
        restore_p90l90_fact(replace(
            envelope, source_json=json.dumps(payload),
        ))
    # Even if the tampered blob re-encodes as another *valid* fact, the
    # original event identity is insufficient to prove immutable raw bytes:
    # source ingress must be journaled and hash/identity validated upstream.
    payload = json.loads(envelope.source_json)
    payload["fact_id"] = "forged-id"
    with pytest.raises(ValueError, match="source identity"):
        restore_p90l90_fact(replace(
            envelope, source_json=json.dumps(payload),
        ))
    with pytest.raises(ValueError, match="closed kline identity"):
        replace(
            encode_p90l90_fact(_bar("5")),
            exchange_sequence=12, exec_id="fake",
        )


def test_bad_source_and_future_or_missing_geometry_fail_closed():
    fact, cursor = _trade()
    with pytest.raises(ValueError, match="native trade cursor mismatch"):
        encode_p90l90_fact(fact, cursor=replace(cursor, seq=13, symbol="DOTUSDT"))
    with pytest.raises(ValueError, match="before exchange"):
        encode_p90l90_fact(
            replace(fact, received_at=fact.event_at-timedelta(milliseconds=1)),
            cursor=cursor,
        )
    with pytest.raises(ValueError, match="outside exact Strategy"):
        encode_p90l90_fact(
            replace(fact, symbol="BTCUSDT"),
            cursor=replace(cursor, symbol="BTCUSDT"),
        )
    with pytest.raises(ValueError, match="boundary inconsistent"):
        encode_p90l90_fact(replace(
            _bar("5"), event_at=B+timedelta(minutes=5),
            observed_at=B+timedelta(minutes=5),
        ))
    assert encode_p90l90_fact(_bar("15")).timeframe == "15"
