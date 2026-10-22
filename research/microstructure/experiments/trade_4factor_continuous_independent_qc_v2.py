#!/usr/bin/env python3
"""Independent full-minute read-only Decimal QC of continuous 4-factor research.

No writes to server. Reconstructs each minute from original compressed trade CSV,
not from primary materializer functions or the previously produced feature values.
Validates UTC midnight previous60 lookback, source SHA, all minutes, formulas,
missingness, grouped-print semantics, and exact output snapshot provenance.
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections import deque
from decimal import Decimal
from pathlib import Path

BASE = Path("/data/cripta/research/runs/microstructure_joint_4f_20261023")
RAW = Path("/data/cripta/datasets/raw/20260518_20260816")
START = dt.date(2026, 5, 18)
SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FEATURES = (
    ("factor_4_large_trade_share", "share"),
    ("factor_8_max_timestamp_side_group_streak", "streak"),
    ("factor_27_median_positive_gap_ms", "duration"),
    ("factor_28_minute_fill_notional_p90_usdt", "p90"),
    ("factor_2_trade_rate", "rate"),
    ("factor_5_delta_usdt", "delta"),
)


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def read_archive(symbol, day):
    path = RAW / symbol / "public_trades" / (symbol + day.isoformat() + ".csv.gz")
    start = Decimal(int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp()))
    groups = [[] for _ in range(1440)]
    previous = None
    with gzip.open(path, "rt", newline="") as stream:
        for raw in csv.DictReader(stream):
            timestamp = Decimal(raw["timestamp"])
            if previous is not None and timestamp < previous:
                raise ValueError("SOURCE_TS_NONMONOTONE")
            previous = timestamp
            minute = int((timestamp - start) // Decimal(60))
            if not 0 <= minute < 1440:
                raise ValueError("OUTSIDE_UTC_DAY")
            if raw["symbol"] != symbol or raw["side"] not in ("Buy", "Sell"):
                raise ValueError("SOURCE_SYMBOL_OR_SIDE")
            amount = Decimal(raw["foreignNotional"])
            price = Decimal(raw["price"])
            if not amount.is_finite() or amount < 0 or not price.is_finite() or price <= 0:
                raise ValueError("SOURCE_VALUE_INVALID")
            groups[minute].append((timestamp, raw["side"], amount, price))
    return groups


def recompute(prints, history):
    if not prints:
        return {
            "share": None, "streak": None, "duration": None,
            "p90": None, "rate": None, "delta": None, "groups": 0,
            "close": None, "prior60_n": sum(map(len, history)) if len(history) == 60 else 0,
        }
    amounts = [x[2] for x in prints]
    ratio = None
    n = 0
    if len(history) == 60:
        prior = sorted(v for group in history for v in group)
        n = len(prior)
        if n >= 100 and sum(amounts) > 0:
            cutoff = prior[int(Decimal("0.99") * (n - 1))]
            ratio = sum((v for v in amounts if v >= cutoff), Decimal(0))
            ratio /= sum(amounts)
    ordered = sorted(amounts)
    p90 = ordered[int(Decimal("0.9") * (len(ordered) - 1))]
    groups = []
    gaps = []
    previous_timestamp = None
    for timestamp, side, _amount, _price in prints:
        if previous_timestamp is not None and timestamp > previous_timestamp:
            gaps.append((timestamp - previous_timestamp) * 1000)
        previous_timestamp = timestamp
        if not groups or groups[-1] != (timestamp, side):
            groups.append((timestamp, side))
    streak, largest, prior_side = 0, 0, None
    for _timestamp, side in groups:
        streak = streak + 1 if side == prior_side else 1
        prior_side = side
        largest = max(largest, streak)
    return {
        "share": ratio,
        "streak": largest,
        "duration": statistics.median(gaps) if gaps else None,
        "p90": p90,
        "rate": len(prints),
        "delta": sum(
            (amount if side == "Buy" else -amount
             for _stamp, side, amount, _price in prints),
            Decimal(0),
        ),
        "groups": len(groups),
        "close": prints[-1][3],
        "prior60_n": n,
    }


def assert_same(expected, actual, label, rownum):
    if expected is None and actual is None:
        return
    if expected is None or actual is None:
        raise ValueError(f"NULL_MISMATCH_{label}_{rownum}")
    if not math.isclose(float(expected), float(actual), rel_tol=1e-10, abs_tol=1e-6):
        raise ValueError(f"NUMERIC_MISMATCH_{label}_{rownum}_{expected}_{actual}")


def check(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("ACTOR_NOT_CRIPTA")
    root = BASE / ("continuous_" + str(days) + "d_v2")
    run = root / (symbol + "_" + str(days) + "d_v2")
    result = json.loads((run / "result.json").read_text())
    final = json.loads((run / "run_final.json").read_text())
    feature_path = run / "features.jsonl.gz"
    if result["status"] != "PHYSICAL_4FACTOR_COMPLETED_NOT_INDEPENDENT_QC_NOT_RESEARCH_READY":
        raise ValueError("UNEXPECTED_RESEARCH_STATE")
    if result["symbol"] != symbol or result["days"] != days:
        raise ValueError("SCOPE_MISMATCH")
    if final["status"] != "PHYSICAL_COMPLETE":
        raise ValueError("MANIFEST_STATUS")
    if result["receipt_clock"] != "UNKNOWN" or final["verified_available_after_utc"] is not None:
        raise ValueError("FABRICATED_RECEIPT_TIME")
    if (
        final["result_sha256"] != sha256(run / "result.json")
        or result["features_sha256"] != sha256(feature_path)
        or final["output_sha256"] != sha256(feature_path)
        or result["code_sha256"] != sha256(run / "source_snapshot.py")
    ):
        raise ValueError("RESULT_PROVENANCE_HASH_MISMATCH")
    prev60 = deque(maxlen=60)
    nulls = {name: 0 for name, _ in FEATURES}
    day_infos = []
    nrows = 0
    raw_total = 0
    started = time.monotonic()
    with gzip.open(feature_path, "rt") as stream:
        for d in range(days):
            day = START + dt.timedelta(days=d)
            raw = RAW / symbol / "public_trades" / (
                symbol + day.isoformat() + ".csv.gz"
            )
            source_sha = sha256(raw)
            expected_day = result["source_days"][d]
            if (
                expected_day["day"] != day.isoformat()
                or expected_day["source_sha256"] != source_sha
            ):
                raise ValueError("SOURCE_PROVENANCE_DRIFT")
            buckets = read_archive(symbol, day)
            day_fills = 0
            for minute, prints in enumerate(buckets):
                line = stream.readline()
                if not line:
                    raise ValueError("TRUNCATED_FEATURE_FILE")
                row = json.loads(line)
                global_minute = d * 1440 + minute
                stamp = (
                    dt.datetime.combine(day, dt.time(), dt.UTC)
                    + dt.timedelta(minutes=minute)
                )
                if (
                    row["minute_index"] != global_minute
                    or row["window_start_utc"] != stamp.isoformat()
                    or row["derived_window_end_utc"]
                    != (stamp + dt.timedelta(minutes=1)).isoformat()
                    or row["source_archive_sha256"] != source_sha
                    or row["instrument_id"] != symbol
                    or row["formula_version"] != "trade_4factor_continuous_prior60_v2"
                    or row["observed_at_utc"] is not None
                    or row["verified_available_after_utc"] is not None
                ):
                    raise ValueError("IDENTITY_TIME_OR_SOURCE_MISMATCH")
                expected = recompute(prints, prev60)
                for key, field in FEATURES:
                    assert_same(expected[field], row[key], key, global_minute)
                    nulls[key] += int(row[key] is None)
                assert_same(expected["close"], row["price_close"], "close", global_minute)
                if (
                    expected["groups"] != row["timestamp_side_groups"]
                    or expected["rate"] != (
                        row["input_fill_count"] if row["input_fill_count"] else None
                    )
                    or len(prints) != row["input_fill_count"]
                    or row["prior60_n"] != expected["prior60_n"]
                    or row["quality"] != (
                        "OBSERVED_EXCHANGE_TAPE" if prints else "NO_DATA"
                    )
                ):
                    raise ValueError("GROUPS_FILLS_OR_HISTORY_MISMATCH")
                prev60.append([x[2] for x in prints])
                day_fills += len(prints)
                nrows += 1
            if day_fills != expected_day["fills"]:
                raise ValueError("SOURCE_DAILY_FILL_COUNT_MISMATCH")
            raw_total += day_fills
            day_infos.append({"date": day.isoformat(), "fills": day_fills})
            elapsed = time.monotonic() - started
            print(json.dumps({
                "stage": "FULL_DECIMAL_INDEPENDENT_QC",
                "symbol": symbol, "processed_days": d + 1, "total_days": days,
                "percent": round(100 * (d + 1) / days, 2),
                "elapsed_seconds": round(elapsed, 2),
                "eta_seconds": round(elapsed * (days - d - 1) / (d + 1), 2),
            }), file=sys.stderr, flush=True)
        if stream.readline():
            raise ValueError("EXTRA_FEATURE_ROWS")
    if nrows != result["minute_rows"] or raw_total != result["raw_fill_count"]:
        raise ValueError("TOTAL_ROWS_OR_FILLED_SOURCE_MISMATCH")
    if nulls != result["null_counts"]:
        raise ValueError("NULL_DISTRIBUTION_MISMATCH")
    return {
        "status": "FULL_4FACTOR_DECIMAL_INDEPENDENT_QC_PASS_NOT_RESEARCH_READY",
        "symbol": symbol, "days": days, "rows": nrows,
        "raw_fills": raw_total, "source_days": day_infos,
        "feature_sha256": sha256(feature_path),
        "null_counts": nulls, "elapsed_seconds": round(time.monotonic() - started, 2),
        "limitations": [
            "No receiver availability timestamp",
            "Independent numeric checks do not validate source completeness",
            "No predictive edge or trade execution verified",
        ],
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", choices=SYMS, required=True)
    p.add_argument("--days", type=int, choices=(2, 30), required=True)
    args = p.parse_args()
    print(json.dumps(check(args.symbol, args.days), ensure_ascii=False))
