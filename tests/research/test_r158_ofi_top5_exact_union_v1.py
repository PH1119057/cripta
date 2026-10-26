"""#158 #10 exact-price OFI, unknown visibility, gap and UTC tests."""
import importlib.util
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "research/microstructure/experiments/r158_ofi_top5_exact_union_v1.py"
)
spec = importlib.util.spec_from_file_location("r158_ofi", SOURCE)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
DAY = date(2026, 5, 18)
TS = int(datetime(2026, 5, 18, tzinfo=UTC).timestamp() * 1000)


def original_snapshot():
    return {
        "topic": "orderbook.200.APTUSDT", "type": "snapshot", "ts": TS,
        "data": {
            "s": "APTUSDT", "u": 100, "seq": 1000,
            "b": [[str(300 - k), "10"] for k in range(200)],
            "a": [[str(310 + k), "10"] for k in range(200)],
        },
    }


def delta(b=None, a=None, *, uid=101, seq=1001, ts=TS + 100):
    return {
        "topic": "orderbook.200.APTUSDT", "type": "delta", "ts": ts,
        "data": {
            "s": "APTUSDT", "u": uid, "seq": seq,
            "b": b if b is not None else [],
            "a": a if a is not None else [],
        },
    }


def test_same_exact_price_signed_quote():
    reader = m.Reader("APTUSDT")
    assert reader.on_record(original_snapshot()) is None
    event = reader.on_record(delta(b=[["300", "12"]], a=[["310", "11"]]))
    assert event["status"] == "OBSERVED"
    assert Decimal(event["bid_quote_usdt"]) == 600
    assert Decimal(event["ask_quote_usdt"]) == 310
    assert Decimal(event["signed_quote_usdt"]) == 290
    assert event["bid_union_levels"] == 5


def test_new_best_price_union_not_rank_index():
    reader = m.Reader("APTUSDT")
    reader.on_record(original_snapshot())
    event = reader.on_record(delta(b=[["301", "2"]]))
    assert event["status"] == "OBSERVED"
    assert Decimal(event["bid_quote_usdt"]) == 602
    assert Decimal(event["ask_quote_usdt"]) == 0
    assert event["bid_union_levels"] == 6


def test_200_price_visibility_unproven_is_unknown_not_zero():
    reader = m.Reader("APTUSDT")
    snap = original_snapshot()
    # Keep ask above new bids; exercise visibility, not the crossed-book gate.
    snap["data"]["a"] = [[str(1010 + k), "10"] for k in range(200)]
    reader.on_record(snap)
    # 200 new higher bids remove the old Top5 from truncated new OB200.
    event = reader.on_record(
        delta(b=[[str(500 - k), "10"] for k in range(200)])
    )
    assert event["status"] == "UNKNOWN_VISIBILITY"
    assert event["signed_quote_usdt"] is None
    assert event["unknown_bid_prices"]


def test_source_seq_gap_discards_state_until_new_snapshot():
    reader = m.Reader("APTUSDT")
    reader.on_record(original_snapshot())
    assert reader.on_record(delta(uid=102)) is None
    assert reader.counts["source_gap_or_order_failures"] == 1
    assert reader.on_record(delta(uid=103, seq=1002)) is None
    assert reader.counts["deltas_without_snapshot"] == 1
    reader.on_record(original_snapshot())
    e = reader.on_record(delta(b=[["300", "12"]]))
    assert e["status"] == "OBSERVED"


def test_bad_source_symbol_and_malformed_book_fails():
    reader = m.Reader("APTUSDT")
    snap = original_snapshot()
    snap["topic"] = "orderbook.200.DOTUSDT"
    with pytest.raises(ValueError, match="wrong native book"):
        reader.on_record(snap)
    snap = original_snapshot()
    snap["data"]["b"][0][1] = "-1"
    with pytest.raises(ValueError, match="invalid source book sign"):
        reader.on_record(snap)
    snap = original_snapshot()
    snap["data"]["b"][1] = snap["data"]["b"][0]
    with pytest.raises(ValueError, match="duplicate side/price"):
        reader.on_record(snap)


def test_genuine_snapshot_replaces_state_without_cross_snapshot_delta():
    reader = m.Reader("APTUSDT")
    reader.on_record(original_snapshot())
    reader.on_record(delta(b=[["300", "20"]]))
    again = original_snapshot()
    again["ts"] = TS + 300
    again["data"]["u"] = 200
    again["data"]["seq"] = 2000
    assert reader.on_record(again) is None
    e = reader.on_record(delta(b=[["300", "11"]], uid=201, seq=2001, ts=TS + 400))
    assert e["signed_quote_usdt"] == "300"
