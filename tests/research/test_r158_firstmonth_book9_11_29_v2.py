"""Deterministic snapshot/delta orderbook first-month #9/#11/#29 tests."""
import datetime as dt
import importlib.util
import json
import zipfile
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "research/microstructure/experiments/r158_firstmonth_book9_11_29_v2.py"
)
SPEC = importlib.util.spec_from_file_location("r158_book_month", SCRIPT)
SUT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUT)


def fake_zip(tmp_path, records):
    path = tmp_path / "data.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("2026-05-18_APTUSDT_ob200.data", "".join(
            json.dumps(r) + "\n" for r in records
        ))
    return path


def rec(t0, offset_ms, typ, update, bid, ask):
    return {
        "topic": "orderbook.200.APTUSDT",
        "type": typ,
        "ts": t0 + offset_ms, "cts": t0 + offset_ms - 2,
        "data": {
            "s": "APTUSDT", "u": update, "seq": update + 100,
            "b": bid, "a": ask,
        },
    }


def test_book_minute_end_snapshot_delta_and_no_fabricated_missing(tmp_path):
    day = dt.date(2026, 5, 18)
    t0 = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp() * 1000)
    z = fake_zip(tmp_path, [
        rec(t0, 100, "snapshot", 100, [["100", "10"]], [["100.02", "10"]]),
        rec(t0, 60500, "delta", 101, [["100", "9"]], [["100.02", "11"]]),
    ])
    rows, stats = SUT.book_day("APTUSDT", day, z)
    assert len(rows) == 1440
    assert stats["snapshot"] == 1
    assert stats["delta"] == 1
    assert stats["processed"] == 2
    assert rows[0]["latest_book_event_ts_ms"] == t0 + 100
    assert rows[0]["book_age_at_minute_end_ms"] == 59900
    assert rows[1]["latest_book_event_ts_ms"] == t0 + 60500
    assert rows[1]["book_age_at_minute_end_ms"] == 59500
    assert rows[1]["factor_11_bid_depth_quote_5bps"] < rows[0][
        "factor_11_bid_depth_quote_5bps"
    ]
    assert rows[1]["factor_29_spread_bps"] == rows[0]["factor_29_spread_bps"]
    assert rows[2] is None
    assert rows[0]["factor_9_near_mid_queue_imbalance"] == (
        rows[0]["factor_11_bid_depth_quote_5bps"]
        - rows[0]["factor_11_ask_depth_quote_5bps"]
    ) / (
        rows[0]["factor_11_bid_depth_quote_5bps"]
        + rows[0]["factor_11_ask_depth_quote_5bps"]
    )


def test_snapshot_required_for_asof_state(tmp_path):
    day = dt.date(2026, 5, 18)
    t0 = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp() * 1000)
    z = fake_zip(tmp_path, [
        rec(t0, 100, "delta", 1, [["100", "2"]], [["101", "2"]]),
    ])
    try:
        SUT.book_day("APTUSDT", day, z)
    except ValueError as err:
        assert "BOOK_NO_INITIAL_SNAPSHOT" in str(err)
    else:
        raise AssertionError("Must fail without book snapshot")


def test_wrong_event_symbol_fails_closed(tmp_path):
    day = dt.date(2026, 5, 18)
    t0 = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp() * 1000)
    event = rec(t0, 100, "snapshot", 1, [["100", "2"]], [["100.01", "2"]])
    event["data"]["s"] = "BTCUSDT"
    z = fake_zip(tmp_path, [event])
    try:
        SUT.book_day("APTUSDT", day, z)
    except ValueError as err:
        assert "BOOK_SOURCE_SYMBOL" in str(err)
    else:
        raise AssertionError("Must reject wrong instrument")
