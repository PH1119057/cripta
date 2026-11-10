"""R04 source-only Bybit public topic decoding; no trading consumer and no DB."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bybit_workbench.market_data.public_source_event_codec import (
    OrderbookContinuity,
    PublicSourceFrameInvalid,
    decode_bybit_public_frame,
)

NOW = datetime(2026, 11, 10, 8, tzinfo=UTC)
MS = int(NOW.timestamp() * 1000) - 250
SYMBOLS = frozenset({"BTCUSDT", "LTCUSDT"})


def _event(
    topic: str, data: object, *, frame_type: str = "snapshot",
) -> dict[str, object]:
    return {"topic": topic, "ts": MS, "type": frame_type, "data": data}


def _book(
    *, snapshot: bool = True, seq: int = 1234, update: int = 100,
    bids: list[list[str]] | None = None,
    asks: list[list[str]] | None = None,
) -> dict[str, object]:
    return _event(
        "orderbook.50.BTCUSDT",
        {
            "s": "BTCUSDT", "seq": seq, "u": update,
            "b": bids if bids is not None else [["100", "1"], ["99", "2"]],
            "a": asks if asks is not None else [["101", "3"], ["102", "4"]],
        },
        frame_type="snapshot" if snapshot else "delta",
    )


def _decode(event: dict[str, object], market: str = "LINEAR"):
    return decode_bybit_public_frame(
        event, product=market,
        allowed_symbols=SYMBOLS, received_at=NOW,
    )


def test_native_trade_includes_category_seq_exec_id_and_unmodified_order() -> None:
    event = _event("publicTrade.LTCUSDT", [
        {"s": "LTCUSDT", "seq": 1234, "i": "exec1",
         "T": MS, "p": "52.25", "v": "0.1", "S": "Buy"},
        {"s": "LTCUSDT", "seq": 1234, "i": "exec2",
         "T": MS, "p": "52.25", "v": "0.2", "S": "Sell"},
    ])
    facts = _decode(event, "SPOT")
    assert len(facts) == 2
    assert all(x.product == "SPOT" for x in facts)
    assert facts[0].instrument_id == "inst-bybit-spot-ltcusdt"
    assert [x.exec_id for x in facts] == ["exec1", "exec2"]
    assert [x.exchange_sequence for x in facts] == [1234, 1234]
    assert [x.payload["side"] for x in facts] == ["Buy", "Sell"]


def test_duplicate_or_fake_native_ids_rejected() -> None:
    v = {"s": "LTCUSDT", "seq": 1234, "i": "exec1",
         "T": MS, "p": "52.25", "v": "0.1", "S": "Buy"}
    with pytest.raises(PublicSourceFrameInvalid, match="DUPLICATE"):
        _decode(_event("publicTrade.LTCUSDT", [v, v]))
    with pytest.raises(PublicSourceFrameInvalid, match="ID_INVALID"):
        _decode(_event("publicTrade.LTCUSDT", [{**v, "i": ""}]))


def test_partial_ticker_does_not_fabricate_missing_fields() -> None:
    result = _decode(_event(
        "tickers.BTCUSDT",
        {"symbol": "BTCUSDT", "openInterest": "200.4",
         "fundingRate": "-0.0001"},
        frame_type="delta",
    ))
    assert len(result) == 1
    assert result[0].payload == {
        "openInterest": "200.4", "fundingRate": "-0.0001",
    }
    assert "lastPrice" not in result[0].payload


def test_full_liquidation_preserves_source_side_event_time_without_execid() -> None:
    result = _decode(_event(
        "allLiquidation.BTCUSDT",
        [{"s": "BTCUSDT", "T": MS - 5,
          "p": "95000.2", "v": "0.003", "S": "Sell"}],
    ))
    assert len(result) == 1
    assert result[0].kind == "LIQUIDATION"
    assert result[0].payload["side"] == "Sell"
    assert result[0].exec_id is None


def test_orderbook_delta_continuity_and_reset_without_forged_levels() -> None:
    continuity = OrderbookContinuity()
    snapshot = _decode(_book())[0]
    with pytest.raises(PublicSourceFrameInvalid, match="WITHOUT_SNAPSHOT"):
        continuity.apply(_decode(_book(snapshot=False, seq=1233))[0])
    book = continuity.apply(snapshot)
    assert book["best_bid"] == "100"
    assert book["best_ask"] == "101"
    updated = continuity.apply(_decode(_book(
        snapshot=False, seq=1235, update=101,
        bids=[["100", "0"], ["99.5", "2"]],
        asks=[["101", "2"]],
    ))[0])
    assert updated["best_bid"] == "99.5"
    assert updated["best_ask"] == "101"
    assert Decimal(updated["bid_50bps_usd"]) >= Decimal(0)
    with pytest.raises(PublicSourceFrameInvalid, match="SEQUENCE_REGRESSION"):
        continuity.apply(_decode(_book(snapshot=False, seq=1235))[0])
    with pytest.raises(PublicSourceFrameInvalid, match="WITHOUT_SNAPSHOT"):
        continuity.apply(_decode(_book(snapshot=False, seq=1236))[0])
    restored = continuity.apply(_decode(_book(snapshot=True, seq=1, update=1))[0])
    assert restored["best_bid"] == "100"


@pytest.mark.parametrize("event,product,reason", [
    (_event("tickers.LTCUSDT", {"symbol": "LTCUSDT"}), "SPOT", "CATEGORY"),
    (_event("allLiquidation.BTCUSDT", []), "SPOT", "CATEGORY"),
    (_event("orderbook.50.BTCUSDT", {"s": "BTCUSDT"}), "LINEAR", "SEQUENCE"),
    (_event("tickers.BTCUSDT",
            {"symbol": "BTCUSDT", "openInterest": "NaN"}), "LINEAR", "NUMBER"),
    (_event("tickers.BTCUSDT",
            {"symbol": "BTCUSDT", "lastPrice": "-5"}), "LINEAR", "NUMBER"),
    (_event("publicTrade.LTCUSDT", [
        {"s": "BTCUSDT", "seq": 4, "i": "x",
         "T": MS, "p": "1", "v": "1", "S": "Buy"},
    ]), "LINEAR", "SYMBOL"),
])
def test_invalid_provider_facts_fail_closed(event, product, reason) -> None:
    with pytest.raises(PublicSourceFrameInvalid, match=reason):
        _decode(event, product)


def test_unknown_topic_not_a_fact_and_nonmatching_symbol_rejected() -> None:
    assert _decode(_event("kline.5.BTCUSDT", [])) == ()
    with pytest.raises(PublicSourceFrameInvalid, match="UNAUTHORIZED"):
        _decode(_event("tickers.ETHUSDT", {"symbol": "ETHUSDT"}))


def test_books_detect_crossed_snapshot() -> None:
    with pytest.raises(PublicSourceFrameInvalid, match="CROSSED"):
        OrderbookContinuity().apply(_decode(_book(
            bids=[["101", "1"]], asks=[["100", "1"]],
        ))[0])
