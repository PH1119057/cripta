"""Physical OB200 price-identity book #9/#11/#29 unit smoke: no future leakage."""
import datetime as dt
import importlib.util
import json
import zipfile
from decimal import Decimal
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "research/microstructure/experiments/r158_firstmonth_book9_11_29_utcclip_v2.py"
)
SPEC = importlib.util.spec_from_file_location("r158_book", SCRIPT)
SUT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUT)


def fixture(tmp_path, date, messages):
    symbol = "APTUSDT"
    path = tmp_path / (date.isoformat() + "_" + symbol + "_ob200.data.zip")
    member = date.isoformat() + "_" + symbol + "_ob200.data"
    start = int(dt.datetime.combine(date, dt.time(), dt.UTC).timestamp() * 1000)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as out:
        rows = []
        for offset, kind, bids, asks in messages:
            rows.append(json.dumps({
                "ts": start + offset,
                "type": kind,
                "data": {"b": bids, "a": asks, "seq": offset, "u": offset},
            }))
        out.writestr(member, "\n".join(rows) + "\n")
    return path


def test_book_minute_state_and_no_data(tmp_path):
    day = dt.date(2026, 5, 18)
    source = fixture(tmp_path, day, [
        (1000, "snapshot", [["100", "2"]], [["100.01", "3"]]),
        (2000, "delta", [], [["100.01", "4"]]),
        (125000, "delta", [["100", "3"]], []),
    ])
    rows, meta = SUT.day_rows("APTUSDT", day, source)
    assert len(rows) == 1440
    assert meta["messages"] == 3
    assert meta["snapshots"] == 1
    assert meta["minute_observed"] == 2
    assert meta["sequence_continuity_verified"] is False
    assert rows[0]["factor_11_bid_depth_usdt_near5bp"] == "200"
    assert rows[0]["factor_11_ask_depth_usdt_near5bp"] == "400.04"
    assert rows[0]["factor_9_queue_imbalance_near5bp"] == str(
        (Decimal("200") - Decimal("400.04")) / Decimal("600.04")
    )
    assert rows[0]["book_event_age_to_derived_end_ms"] == 58000
    assert rows[1]["quality"] == "NO_BOOK_EVENT_IN_MINUTE"
    assert rows[1]["factor_29_spread_bps"] is None
    assert rows[2]["factor_11_bid_depth_usdt_near5bp"] == "300"
    assert rows[2]["last_book_event_timestamp_ms"] is not None
    assert rows[0]["verified_available_after_utc"] is None


def test_delta_without_snapshot_is_not_invented(tmp_path):
    day = dt.date(2026, 5, 18)
    file = fixture(tmp_path, day, [
        (1000, "delta", [["100", "3"]], [["100.01", "4"]]),
    ])
    try:
        SUT.day_rows("APTUSDT", day, file)
    except ValueError as error:
        assert "DELTA_BEFORE_INITIAL_SNAPSHOT" in str(error)
    else:
        raise AssertionError("Should reject delta-only day, not fabricate book state")


def test_replacement_snapshot_reinitializes_book(tmp_path):
    day = dt.date(2026, 5, 18)
    file = fixture(tmp_path, day, [
        (1000, "snapshot", [["100", "2"]], [["100.01", "3"]]),
        (62000, "snapshot", [["99", "5"]], [["99.01", "1"]]),
    ])
    rows, meta = SUT.day_rows("APTUSDT", day, file)
    assert meta["snapshots"] == 2
    assert rows[1]["factor_11_bid_depth_usdt_near5bp"] == "495"
    assert rows[1]["best_bid_quote"] == "99"
    assert rows[1]["best_ask_quote"] == "99.01"


def test_secondary_full_minute_replay_is_independent_and_not_per_event(tmp_path):
    checker_path = (
        Path(__file__).resolve().parents[2]
        / "research/microstructure/experiments/"
        "r158_firstmonth_book9_11_29_utcclip_independent_qc_v2.py"
    )
    cs = importlib.util.spec_from_file_location("r158_book_qc", checker_path)
    independent = importlib.util.module_from_spec(cs)
    cs.loader.exec_module(independent)
    day = dt.date(2026, 5, 18)
    file = fixture(tmp_path, day, [
        (1000, "snapshot", [["100", "2"]], [["100.01", "3"]]),
        (2000, "delta", [], [["100.01", "4"]]),
        (125000, "delta", [["100", "3"]], []),
    ])
    states, messages, snapshots, clip = independent.source_minute_states(
        "APTUSDT", day, file
    )
    assert messages == 3 and snapshots == 1
    assert clip["out_of_day_suffix_messages"] == 0
    assert sum(x is not None for x in states) == 2
    assert states[0][2] == int(dt.datetime.combine(
        day, dt.time(), dt.UTC
    ).timestamp() * 1000) + 2000
    assert independent.expected_from_state(
        states[0],
        int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp() * 1000),
        0,
    )["factor_11_ask_depth_usdt_near5bp"] == Decimal("400.04")


def test_utc_day_suffix_is_excluded_not_imputed(tmp_path):
    day = dt.date(2026, 5, 18)
    source = fixture(tmp_path, day, [
        (1000, "snapshot", [["100", "2"]], [["100.01", "3"]]),
        (5000, "delta", [["100", "4"]], []),
        (86400132, "delta", [["101", "2"]], []),
        (86400232, "snapshot", [["101", "2"]], [["101.01", "3"]]),
    ])
    rows, meta = SUT.day_rows("APTUSDT", day, source)
    assert meta["messages"] == 2
    assert meta["out_of_day_suffix_messages"] == 2
    assert meta["out_of_day_suffix_last_type"] == "snapshot"
    assert rows[0]["factor_11_bid_depth_usdt_near5bp"] == "400"
    assert rows[1]["quality"] == "NO_BOOK_EVENT_IN_MINUTE"
    assert rows[-1]["quality"] == "NO_BOOK_EVENT_IN_MINUTE"
    prev = {
        "out_of_day_suffix_messages": 2,
        "out_of_day_suffix_last_hash": meta["out_of_day_suffix_last_hash"],
        "out_of_day_suffix_last_type": "snapshot",
    }
    nxt = {
        "first_event_hash": meta["out_of_day_suffix_last_hash"],
        "first_event_type": "snapshot",
    }
    assert SUT.verified_boundary(prev, nxt) is True
    assert SUT.verified_boundary(prev, {**nxt, "first_event_hash": "bad"}) is False


def test_independent_utc_clip_matches_primary_suffix(tmp_path):
    qcfile = (
        Path(__file__).resolve().parents[2]
        / "research/microstructure/experiments/"
        "r158_firstmonth_book9_11_29_utcclip_independent_qc_v2.py"
    )
    spec = importlib.util.spec_from_file_location("r158_clip_qc", qcfile)
    independent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(independent)
    day = dt.date(2026, 5, 18)
    source = fixture(tmp_path, day, [
        (1000, "snapshot", [["100", "2"]], [["100.01", "3"]]),
        (86400232, "snapshot", [["101", "4"]], [["101.01", "5"]]),
    ])
    rows, primary_meta = SUT.day_rows("APTUSDT", day, source)
    states, messages, snapshots, secondary_meta = (
        independent.source_minute_states("APTUSDT", day, source)
    )
    assert messages == 1 and snapshots == 1
    assert secondary_meta["out_of_day_suffix_messages"] == 1
    assert all(primary_meta[k] == value for k, value in secondary_meta.items())
    assert len(rows) == 1440
    assert sum(x is not None for x in states) == 1
