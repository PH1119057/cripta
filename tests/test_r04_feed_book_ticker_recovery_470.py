"""R04 #470: one coherent negative suite for book/ticker/forming recovery.

Pure tests: no real WebSocket, PostgreSQL, Exchange, or second writer.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.market_data.public_source_event_codec import (
    PublicSourceFrameInvalid,
    decode_bybit_public_frame,
)
from bybit_workbench.market_data.public_source_second_store import (
    PublicSecondAccumulator,
    PublicSourceSecondUnavailable,
)
from operations.monitoring.public_market_source_union_capture import (
    _BookResubscribe,
    _invalidate_scoped_market,
    _scoped_quality_reason,
)

AT = datetime(2026, 11, 11, 10, 15, tzinfo=UTC)
SESSION = "a" * 32
INSTRUMENT = "inst-bybit-linear-aptusdt"
SYMBOL = "APTUSDT"
TOPIC = "orderbook.50.APTUSDT"


def _decode(topic: str, data: object, *, kind: str = "snapshot"):
    return decode_bybit_public_frame(
        {"topic": topic, "ts": int(AT.timestamp() * 1000),
         "type": kind, "data": data},
        product="LINEAR", allowed_symbols=frozenset({SYMBOL}),
        received_at=AT,
    )[0]


def _book(*, seq: int, update: int, kind: str = "snapshot"):
    return _decode(TOPIC, {
        "s": SYMBOL, "seq": seq, "u": update,
        "b": [["100", "1"]], "a": [["101", "1"]],
    }, kind=kind)


def test_malformed_forming_does_not_destroy_valid_book_and_ticker() -> None:
    agg = PublicSecondAccumulator(session=SESSION)
    agg.accept(_book(seq=100, update=10), received_at=AT)
    agg.accept(_decode(
        "tickers.APTUSDT",
        {"symbol": SYMBOL, "lastPrice": "100.3", "openInterest": "200"},
    ), received_at=AT)
    # A malformed/late forming kline from the same instrument has no
    # permission to invalidate either existing data family.
    _invalidate_scoped_market(
        agg, topic="kline.1.APTUSDT", instrument_id=INSTRUMENT,
    )
    agg.accept(
        _book(seq=101, update=11, kind="delta"),
        received_at=AT + timedelta(milliseconds=25),
    )
    agg.accept(_decode(
        "tickers.APTUSDT", {"symbol": SYMBOL, "ask1Price": "101"},
        kind="delta",
    ), received_at=AT + timedelta(milliseconds=35))
    row = agg.ready_seconds(before=AT + timedelta(seconds=1))[0]
    assert row["book"]["best_bid"] == "100"
    assert row["book"]["source_sequence"] == 101
    assert row["ticker"] == {
        "lastPrice": "100.3", "openInterest": "200",
        "ask1Price": "101",
    }


def test_book_fault_isolated_and_real_snapshot_required_for_recovery() -> None:
    agg = PublicSecondAccumulator(session=SESSION)
    agg.accept(_book(seq=100, update=10), received_at=AT)
    agg.accept(_decode(
        "tickers.APTUSDT",
        {"symbol": SYMBOL, "lastPrice": "100.3", "openInterest": "200"},
    ), received_at=AT)
    _invalidate_scoped_market(
        agg, topic=TOPIC, instrument_id=INSTRUMENT,
    )
    with pytest.raises(PublicSourceFrameInvalid, match="WITHOUT_SNAPSHOT"):
        agg.accept(
            _book(seq=101, update=11, kind="delta"),
            received_at=AT + timedelta(milliseconds=10),
        )
    # A transport restart cannot carry a synthetic/missing book.
    agg.accept(
        _book(seq=200, update=1),
        received_at=AT + timedelta(seconds=1),
    )
    agg.accept(_decode(
        "tickers.APTUSDT", {"symbol": SYMBOL, "ask1Price": "101"},
        kind="delta",
    ), received_at=AT + timedelta(seconds=1))
    rows = agg.ready_seconds(before=AT + timedelta(seconds=2))
    assert len(rows) == 2
    assert rows[1]["book"]["source_sequence"] == 200
    assert rows[1]["ticker"] == {
        "lastPrice": "100.3", "openInterest": "200",
        "ask1Price": "101",
    }
    assert rows[1]["ticker_field_times"]["lastPrice"] == AT.isoformat()


def test_bad_trade_or_liquidation_never_invalidates_book_or_ticker() -> None:
    agg = PublicSecondAccumulator(session=SESSION)
    agg.accept(_book(seq=100, update=10), received_at=AT)
    _invalidate_scoped_market(
        agg, topic="publicTrade.APTUSDT", instrument_id=INSTRUMENT,
    )
    _invalidate_scoped_market(
        agg, topic="allLiquidation.APTUSDT", instrument_id=INSTRUMENT,
    )
    agg.accept(_book(seq=101, update=11, kind="delta"), received_at=AT)
    assert agg.ready_seconds(before=AT + timedelta(seconds=1))[0][
        "book"
    ]["source_sequence"] == 101


def test_scoped_book_resubscribe_requires_ack_then_authentic_snapshot() -> None:
    recovery = _BookResubscribe.start(TOPIC, now=10.0)
    start = json.loads(recovery.command())
    assert start["op"] == "unsubscribe" and start["args"] == [TOPIC]
    assert recovery.allows({
        "topic": TOPIC, "type": "snapshot",
    }) is False
    subscribe = recovery.on_ack({
        "op": "unsubscribe", "req_id": start["req_id"],
        "success": True,
    }, now=11.0)
    assert subscribe is not None
    second = json.loads(subscribe)
    assert second["op"] == "subscribe"
    assert recovery.allows({
        "topic": TOPIC, "type": "snapshot",
    }) is False
    assert recovery.on_ack({
        "op": "subscribe", "req_id": second["req_id"],
        "success": True,
    }, now=12.0) is None
    assert recovery.allows({
        "topic": TOPIC, "type": "delta",
    }) is False
    assert recovery.allows({
        "topic": TOPIC, "type": "snapshot",
    }) is True
    assert recovery.allows({
        "topic": "orderbook.50.LTCUSDT", "type": "snapshot",
    }) is False


def test_rejected_book_resubscribe_never_claims_ready() -> None:
    recovery = _BookResubscribe.start(TOPIC, now=10.0)
    req = json.loads(recovery.command())["req_id"]
    with pytest.raises(
        PublicSourceSecondUnavailable, match="ACK_REJECTED",
    ):
        recovery.on_ack({
            "op": "unsubscribe", "req_id": req,
            "success": False,
        }, now=11.0)
    assert recovery.allows({
        "topic": TOPIC, "type": "snapshot",
    }) is False
    with pytest.raises(Exception, match="RESUBSCRIBE_SCOPE_INVALID"):
        _BookResubscribe.start("publicTrade.APTUSDT", now=10.0)


def test_delayed_forming_source_frame_is_explicitly_not_relabelled_ready() -> None:
    from bybit_workbench.market_data.public_source_event_codec import (
        decode_bybit_public_frame,
    )

    start = int((AT - timedelta(minutes=1)).timestamp() * 1000)
    row = {
        "interval": "1", "confirm": False,
        "start": start, "end": start + 59999,
        "timestamp": start + 1000,
        "open": "100", "high": "102", "low": "99",
        "close": "101", "volume": "1", "turnover": "100",
    }
    with pytest.raises(
        PublicSourceFrameInvalid, match="FORMING_EXPIRED_OR_FUTURE_SOURCE",
    ):
        decode_bybit_public_frame(
            {"topic": "kline.1.APTUSDT",
             "ts": int(AT.timestamp() * 1000),
             "type": "snapshot", "data": [row]},
            product="LINEAR", allowed_symbols=frozenset({SYMBOL}),
            received_at=AT,
        )


def test_only_exact_typed_reasons_can_enter_quality_ledger() -> None:
    assert _scoped_quality_reason(
        PublicSourceFrameInvalid("PUBLIC_BOOK_DELTA_WITHOUT_SNAPSHOT"),
        "PUBLIC_TOPIC_FRAME_UNVERIFIED",
    ) == "PUBLIC_BOOK_DELTA_WITHOUT_SNAPSHOT"
    assert _scoped_quality_reason(
        RuntimeError("PUBLIC_BAD with untrusted content"),
        "PUBLIC_TOPIC_FRAME_UNVERIFIED",
    ) == "PUBLIC_TOPIC_FRAME_UNVERIFIED"
    assert _scoped_quality_reason(
        RuntimeError(""), "PUBLIC_TOPIC_FRAME_UNVERIFIED",
    ) == "PUBLIC_TOPIC_FRAME_UNVERIFIED"
