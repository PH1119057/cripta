"""Compact source-only per-second facts: no Exchange/consumer mutations."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.market_data.public_source_event_codec import (
    decode_bybit_public_frame,
)
from bybit_workbench.market_data.public_source_second_store import (
    PublicSecondAccumulator,
    PublicSourceSecondUnavailable,
    persist_compact_source_seconds,
)

AT = datetime(2026, 11, 10, 8, 0, 0, 500000, tzinfo=UTC)
MS = int(AT.timestamp() * 1000)
SESSION = "f" * 32
RELEASE = "a" * 40
SYMBOLS = frozenset({"BTCUSDT"})


def _event(topic: str, data: object, *, typ: str = "snapshot"):
    return {"topic": topic, "data": data, "ts": MS, "type": typ}


def _trade(exec_id: str, side: str):
    return _event("publicTrade.BTCUSDT", [{
        "s": "BTCUSDT", "i": exec_id, "seq": 17, "T": MS,
        "p": "100", "v": "0.5", "S": side,
    }])


def _decode(frame: dict, product: str = "LINEAR"):
    return decode_bybit_public_frame(
        frame, product=product, allowed_symbols=SYMBOLS,
        received_at=AT,
    )


def test_second_bucket_combines_native_trades_and_ticker_and_book() -> None:
    group = PublicSecondAccumulator(session=SESSION)
    for native in (
        _trade("a1", "Buy"), _trade("b2", "Sell"), _trade("a1", "Buy"),
    ):
        for e in _decode(native):
            group.accept(e, received_at=AT)
    for e in _decode(_event(
        "tickers.BTCUSDT", {"symbol": "BTCUSDT",
                            "lastPrice": "100.25", "openInterest": "3"},
    )):
        group.accept(e, received_at=AT)
    for e in _decode(_event("orderbook.50.BTCUSDT", {
        "s": "BTCUSDT", "u": 1, "seq": 50,
        "b": [["100", "2"]], "a": [["101", "2"]],
    })):
        group.accept(e, received_at=AT)
    assert group.ready_seconds(before=AT) == ()
    rows = group.ready_seconds(before=AT + timedelta(seconds=1))
    assert len(rows) == 1
    row = rows[0]
    assert row["trade_count"] == 2
    assert row["trade_buy_usd"] == Decimal("50.0")
    assert row["trade_sell_usd"] == Decimal("50.0")
    assert row["ticker"]["lastPrice"] == "100.25"
    assert row["book"]["best_bid"] == "100"
    group.mark_committed(rows)
    assert group.ready_seconds(before=AT + timedelta(seconds=2)) == ()
    with pytest.raises(PublicSourceSecondUnavailable, match="LATE"):
        group.accept(_decode(_trade("late", "Buy"))[0], received_at=AT)


def test_market_invalidation_invalidates_old_book_and_ticker() -> None:
    agg = PublicSecondAccumulator(session=SESSION)
    for fact in _decode(_event(
        "tickers.BTCUSDT", {"symbol": "BTCUSDT", "openInterest": "3"},
    )):
        agg.accept(fact, received_at=AT)
    agg.invalidate_market("inst-bybit-linear-btcusdt")
    for fact in _decode(_event(
        "tickers.BTCUSDT", {"symbol": "BTCUSDT", "lastPrice": "100"},
        typ="delta",
    )):
        agg.accept(fact, received_at=AT + timedelta(milliseconds=50))
    rows = agg.ready_seconds(before=AT + timedelta(seconds=1))
    assert rows[0]["ticker"] == {"lastPrice": "100"}
    assert "openInterest" not in rows[0]["ticker"]


def test_same_symbol_spot_and_linear_are_distinct_instrument_buckets() -> None:
    agg = PublicSecondAccumulator(session=SESSION)
    for product in ("SPOT", "LINEAR"):
        for fact in _decode(_trade(product, "Buy"), product):
            agg.accept(fact, received_at=AT)
    rows = agg.ready_seconds(before=AT + timedelta(seconds=1))
    assert len(rows) == 2
    assert {x["instrument_id"] for x in rows} == {
        "inst-bybit-spot-btcusdt", "inst-bybit-linear-btcusdt",
    }


def test_commit_writer_fails_closed_on_conflicting_immutable_bucket() -> None:
    agg = PublicSecondAccumulator(session=SESSION)
    agg.accept(_decode(_trade("a1", "Buy"))[0], received_at=AT)
    rows = agg.ready_seconds(before=AT + timedelta(seconds=1))
    calls: list[str] = []

    class DB:
        @contextmanager
        def transaction(self):
            calls.append("begin")
            yield
            calls.append("commit")

        def execute(self, sql: str, _params: object):
            calls.append(sql)
            return self

        def fetchone(self):
            if "FROM analytics.native_public_trade_source_sessions" in calls[-1]:
                return ("ACTIVE", "ms-bybit-mainnet-public-v1", RELEASE)
            return (AT,)

    assert persist_compact_source_seconds(
        DB(), rows=rows, producer_release_commit=RELEASE,
    ) == 1
    assert any("ON CONFLICT" in x for x in calls)


def test_writer_requires_exact_source_release_and_row_identity() -> None:
    with pytest.raises(PublicSourceSecondUnavailable, match="RELEASE"):
        persist_compact_source_seconds(
            None, rows=({},), producer_release_commit="invalid",
        )
    with pytest.raises(PublicSourceSecondUnavailable, match="EPOCH"):
        PublicSecondAccumulator(session="bad")
