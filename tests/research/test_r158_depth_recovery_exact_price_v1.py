"""Issue #158 strict #13 Depth Recovery physical-contract tests."""
import importlib.util
import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

SOURCE = (
    Path(__file__).resolve().parents[2]
    / "research/microstructure/experiments/"
    "r158_depth_recovery_exact_price_v1.py"
)
spec = importlib.util.spec_from_file_location("r158_depth13", SOURCE)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SYMBOL = "APTUSDT"
DAY = date(2026, 5, 18)


def snapshot(ts, u=100, seq=1000):
    return {
        "topic": f"orderbook.200.{SYMBOL}",
        "type": "snapshot",
        "ts": ts,
        "data": {
            "s": SYMBOL,
            "u": u,
            "seq": seq,
            "b": [[str(300 - k), "10"] for k in range(200)],
            "a": [[str(310 + k), "10"] for k in range(200)],
        },
    }


def delta(ts, u, seq, *, bid=None, ask=None):
    return {
        "topic": f"orderbook.200.{SYMBOL}",
        "type": "delta",
        "ts": ts,
        "data": {
            "s": SYMBOL,
            "u": u,
            "seq": seq,
            "b": bid or [],
            "a": ask or [],
        },
    }


def archive(tmp_path, records):
    path = tmp_path / f"{DAY}_{SYMBOL}_ob200.data.zip"
    member = f"{DAY}_{SYMBOL}_ob200.data"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(member, "\n".join(json.dumps(x) for x in records) + "\n")
    return path


def rows_for(result_rows, *, price="300"):
    return [row for row in result_rows if row["side"] == "BID" and row["price"] == price]


def test_endpoint_is_inclusive_and_recovery_requires_later_timestamp(tmp_path):
    midnight = int(datetime(DAY.year, DAY.month, DAY.day, tzinfo=UTC).timestamp() * 1000)
    start = midnight + 1_000
    records = [
        snapshot(midnight),
        delta(start, 101, 1001, bid=[["300", "8"]]),
        delta(start + 15_000, 102, 1002, bid=[["300", "10"]]),
    ]
    manifest, rows = m.calculate_archive(archive(tmp_path, records), SYMBOL, DAY)
    target = rows_for(rows)
    assert [row["horizon_s"] for row in target] == [15, 60, 300]
    assert [row["outcome"] for row in target] == ["R", "R", "R"]
    assert all(row["recovery_ms"] == 15_000 for row in target)
    assert manifest["horizon_endpoint_inclusive"] is True
    assert manifest["recovery_requires_strictly_later_native_timestamp"] is True


def test_horizons_are_independent_n15_then_r60_r300(tmp_path):
    midnight = int(datetime(DAY.year, DAY.month, DAY.day, tzinfo=UTC).timestamp() * 1000)
    start = midnight + 1_000
    records = [
        snapshot(midnight),
        delta(start, 101, 1001, bid=[["300", "8"]]),
        delta(start + 20_000, 102, 1002, bid=[["299", "9"]]),
        delta(start + 30_000, 103, 1003, bid=[["300", "10"]]),
    ]
    _, rows = m.calculate_archive(archive(tmp_path, records), SYMBOL, DAY)
    target = rows_for(rows)
    assert [(row["horizon_s"], row["outcome"]) for row in target] == [
        (15, "N"),
        (60, "R"),
        (300, "R"),
    ]


def test_sequence_gap_censors_only_unresolved_horizons(tmp_path):
    midnight = int(datetime(DAY.year, DAY.month, DAY.day, tzinfo=UTC).timestamp() * 1000)
    start = midnight + 1_000
    records = [
        snapshot(midnight),
        delta(start, 101, 1001, bid=[["300", "8"]]),
        delta(start + 20_000, 102, 1002, bid=[["299", "9"]]),
        delta(start + 30_000, 104, 1003, bid=[["300", "10"]]),
    ]
    _, rows = m.calculate_archive(archive(tmp_path, records), SYMBOL, DAY)
    target = rows_for(rows)
    assert [(row["horizon_s"], row["outcome"]) for row in target] == [
        (15, "N"),
        (60, "C"),
        (300, "C"),
    ]
    assert target[1]["outcome_reason"] == "SOURCE_SEQUENCE_GAP"
    assert target[2]["outcome_reason"] == "SOURCE_SEQUENCE_GAP"


def test_same_timestamp_multi_touch_is_fail_closed(tmp_path):
    midnight = int(datetime(DAY.year, DAY.month, DAY.day, tzinfo=UTC).timestamp() * 1000)
    start = midnight + 1_000
    tie = start + 10_000
    records = [
        snapshot(midnight),
        delta(start, 101, 1001, bid=[["300", "8"]]),
        delta(tie, 102, 1002, bid=[["300", "10"]]),
        delta(tie, 103, 1003, bid=[["300", "7"]]),
    ]
    _, rows = m.calculate_archive(archive(tmp_path, records), SYMBOL, DAY)
    target = rows_for(rows)
    assert all(row["outcome"] == "C" for row in target)
    assert all(
        row["outcome_reason"] == "SAME_TIMESTAMP_ORDER_AMBIGUOUS"
        for row in target
    )


def test_leaving_visible_ob200_edge_censors_unresolved(tmp_path):
    midnight = int(datetime(DAY.year, DAY.month, DAY.day, tzinfo=UTC).timestamp() * 1000)
    start = midnight + 1_000
    edge_price = "101"
    records = [
        snapshot(midnight),
        delta(start, 101, 1001, bid=[[edge_price, "8"]]),
        # Add one new best bid. Trimming back to 200 makes 101 no longer
        # provably visible, without touching 101 itself.
        delta(start + 5_000, 102, 1002, bid=[["301", "1"]]),
    ]
    _, rows = m.calculate_archive(archive(tmp_path, records), SYMBOL, DAY)
    target = rows_for(rows, price=edge_price)
    assert all(row["outcome"] == "C" for row in target)
    assert all(
        row["outcome_reason"] == "OB200_EDGE_VISIBILITY_LOST"
        for row in target
    )


def test_unresolved_day_end_is_right_censored(tmp_path):
    midnight = int(datetime(DAY.year, DAY.month, DAY.day, tzinfo=UTC).timestamp() * 1000)
    start = midnight + 86_399_000
    records = [
        snapshot(midnight),
        delta(start, 101, 1001, bid=[["300", "8"]]),
    ]
    _, rows = m.calculate_archive(archive(tmp_path, records), SYMBOL, DAY)
    target = rows_for(rows)
    assert all(row["outcome"] == "C" for row in target)
    assert all(row["outcome_reason"] == "DAY_END_RIGHT_CENSOR" for row in target)
