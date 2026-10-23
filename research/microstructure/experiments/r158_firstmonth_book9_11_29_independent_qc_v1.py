#!/usr/bin/env python3
"""Independent Decimal complete-minute source QC for Bybit OB200 #9/#11/#29.

No primary computations imported. Exact source ZIP re-read with independent
book event replay and per-minute last event state checks. Preserves NO_DATA
and source-continuity/receiver-time UNKNOWN. Writer cripta only, separate
new QC receipt under already-created first-month research result directory.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import shutil
import zipfile
from decimal import Decimal

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
OUT = pathlib.Path(
    "/data/cripta/research/runs/microstructure_firstmonth_book9_11_29_20261023"
)
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
FIRST = dt.date(2026, 5, 18)


def hashfile(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for data in iter(lambda: source.read(1048576), b""):
            h.update(data)
    return h.hexdigest()


def source_minute_states(symbol, date, zip_path):
    start = int(dt.datetime.combine(date, dt.time(), dt.UTC).timestamp() * 1000)
    last = [None] * 1440
    buy, sell = {}, {}
    ready = False
    previous_ms = None
    previous_minute = None
    messages = snapshots = 0
    with zipfile.ZipFile(zip_path) as archive:
        expected = date.isoformat() + "_" + symbol + "_ob200.data"
        if archive.namelist() != [expected]:
            raise ValueError("QC_ZIP_MEMBER_INVALID")
        with archive.open(expected) as rows:
            for line in rows:
                obj = json.loads(line)
                ms = int(obj["ts"])
                if not start <= ms < start + 86400000:
                    raise ValueError("QC_SOURCE_OUT_OF_UTC_DAY")
                if previous_ms is not None and ms < previous_ms:
                    raise ValueError("QC_TIMESTAMP_REVERSED")
                minute = (ms - start) // 60000
                if previous_minute is not None and minute != previous_minute:
                    last[previous_minute] = (buy.copy(), sell.copy(), previous_ms)
                kind = obj["type"]
                if kind == "snapshot":
                    buy.clear()
                    sell.clear()
                    ready = True
                    snapshots += 1
                elif kind != "delta" or not ready:
                    raise ValueError("QC_MISSING_INITIAL_SNAPSHOT_OR_BAD_TYPE")
                for key, book in (("b", buy), ("a", sell)):
                    for pair in obj["data"][key]:
                        price = Decimal(pair[0])
                        size = Decimal(pair[1])
                        if price <= 0 or size < 0 or not price.is_finite() or not size.is_finite():
                            raise ValueError("QC_BAD_PRICE_SIZE")
                        if size:
                            book[price] = size
                        else:
                            book.pop(price, None)
                previous_ms, previous_minute = ms, minute
                messages += 1
    if previous_minute is not None:
        last[previous_minute] = (buy.copy(), sell.copy(), previous_ms)
    if not messages or not snapshots:
        raise ValueError("QC_EMPTY_SOURCE")
    return last, messages, snapshots


def expected_from_state(state, day_start_ms, idx):
    if state is None:
        return {
            "factor_9_queue_imbalance_near5bp": None,
            "factor_11_bid_depth_usdt_near5bp": None,
            "factor_11_ask_depth_usdt_near5bp": None,
            "factor_29_spread_bps": None,
            "best_bid_quote": None, "best_ask_quote": None,
            "quality": "NO_BOOK_EVENT_IN_MINUTE",
            "last_book_event_timestamp_ms": None,
            "book_event_age_to_derived_end_ms": None,
        }
    buy, sell, ms = state
    if not buy or not sell:
        raise ValueError("QC_BOOK_SIDE_EMPTY")
    bid, ask = max(buy), min(sell)
    if bid >= ask:
        raise ValueError("QC_BOOK_CROSSED")
    mid = (bid + ask) / 2
    bid_quote = sum((
        price * size for price, size in buy.items()
        if mid * Decimal("0.9995") <= price <= mid
    ), Decimal(0))
    ask_quote = sum((
        price * size for price, size in sell.items()
        if mid <= price <= mid * Decimal("1.0005")
    ), Decimal(0))
    denom = bid_quote + ask_quote
    return {
        "factor_9_queue_imbalance_near5bp": (
            (bid_quote - ask_quote) / denom if denom else None
        ),
        "factor_11_bid_depth_usdt_near5bp": bid_quote,
        "factor_11_ask_depth_usdt_near5bp": ask_quote,
        "factor_29_spread_bps": (ask - bid) * 10000 / mid,
        "best_bid_quote": bid, "best_ask_quote": ask,
        "quality": "LAST_OBSERVED_BOOK_STATE_SOURCE_CONTINUITY_UNKNOWN",
        "last_book_event_timestamp_ms": ms,
        "book_event_age_to_derived_end_ms": day_start_ms + (idx+1)*60000 - ms,
    }


def check(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("QC_ACTOR_NOT_CRIPTA")
    if symbol not in SYMS or days not in (2, 30):
        raise ValueError("QC_SCOPE_INVALID")
    dest = OUT / ("physical_" + str(days) + "d") / symbol
    manifest = json.loads((dest / "input_provenance.json").read_text())
    result = json.loads((dest / "result.json").read_text())
    if (
        result["status"] != "PHYSICAL_BOOK_MONTH_COMPLETE_PENDING_INDEPENDENT_QC"
        or result["rows"] != days * 1440
        or result["symbol"] != symbol or result["days"] != days
        or result["has_verified_source_continuity"]
        or result["has_receiver_clock"]
        or len(manifest["days"]) != days
    ):
        raise ValueError("QC_MANIFEST_SCOPE_BAD")
    feature_file = dest / "features.jsonl.gz"
    if result["output_sha256"] != hashfile(feature_file):
        raise ValueError("QC_OUTPUT_HASH_DRIFT")
    if result["program_sha256"] != hashfile(dest / "source_snapshot/materializer.py"):
        raise ValueError("QC_EXECUTED_SOURCE_HASH_DRIFT")
    receipt = dest / "independent_qc_report.json"
    if receipt.exists():
        raise FileExistsError("QC_ALREADY_EXISTS")
    stats = []
    seen = 0
    with gzip.open(feature_file, "rt") as stored:
        for day_num in range(days):
            day = FIRST + dt.timedelta(days=day_num)
            path = RAW / symbol / "orderbook" / (
                day.isoformat() + "_" + symbol + "_ob200.data.zip"
            )
            evidence = manifest["days"][day_num]
            input_sha = hashfile(path)
            if evidence["day"] != day.isoformat() or evidence["zip_sha256"] != input_sha:
                raise ValueError("QC_DAY_SOURCE_SHA_MISMATCH")
            state, n, snapshots = source_minute_states(symbol, day, path)
            if n != evidence["messages"] or snapshots != evidence["snapshots"]:
                raise ValueError("QC_SOURCE_EVENT_COUNTS_MISMATCH")
            active = 0
            utc0 = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp() * 1000)
            for minute in range(1440):
                line = stored.readline()
                if not line:
                    raise ValueError("QC_TRUNCATED_PRIMARY_ROWS")
                row = json.loads(line)
                expected = expected_from_state(state[minute], utc0, minute)
                stamp = dt.datetime.combine(day, dt.time(), dt.UTC) + dt.timedelta(minutes=minute)
                if (
                    row["instrument_id"] != symbol
                    or row["window_start_utc"] != stamp.isoformat()
                    or row["derived_window_end_utc"] != (
                        stamp + dt.timedelta(minutes=1)
                    ).isoformat()
                    or row["source_archive_sha256"] != input_sha
                    or row["observed_at_utc"] is not None
                    or row["verified_available_after_utc"] is not None
                    or row["quality"] != expected["quality"]
                ):
                    raise ValueError("QC_ID_TIME_OR_QUALITY_MISMATCH")
                for key, value in expected.items():
                    if key == "quality":
                        continue
                    if value is None:
                        if row[key] is not None:
                            raise ValueError("QC_WRONG_NULL " + key)
                    elif isinstance(value, Decimal):
                        if row[key] is None or Decimal(row[key]) != value:
                            raise ValueError("QC_NUMERIC_DIFF " + key + " " + str(seen))
                    elif row[key] != value:
                        raise ValueError("QC_TIMESTAMP_DIFF " + key + " " + str(seen))
                if state[minute] is not None:
                    active += 1
                    if (
                        row["book_levels_bid"] != len(state[minute][0])
                        or row["book_levels_ask"] != len(state[minute][1])
                    ):
                        raise ValueError("QC_LEVEL_COUNT_MISMATCH")
                seen += 1
            if active != evidence["minute_observed"]:
                raise ValueError("QC_DAILY_MINUTE_COVERAGE_DIFF")
            stats.append({"day": day.isoformat(), "messages": n,
                          "snapshots": snapshots, "active_minutes": active,
                          "source_zip_sha256": input_sha})
            print(json.dumps({
                "stage": "BOOK_DECIMAL_FULL_MINUTE_QC", "symbol": symbol,
                "finished_day": day_num + 1, "total_days": days,
                "heartbeat": True,
            }), flush=True)
        if stored.readline():
            raise ValueError("QC_EXTRA_OUTPUT_ROWS")
    if seen != days * 1440:
        raise ValueError("QC_TOTAL_MINUTE_COUNT")
    script = pathlib.Path(__file__).resolve()
    shutil.copyfile(script, dest / "source_snapshot" / "independent_checker.py")
    report = {
        "status": "FULL_MINUTE_BOOK_DECIMAL_QC_PASS_NOT_RESEARCH_READY",
        "symbol": symbol, "days": days, "minute_rows": seen,
        "source_day_checks": stats,
        "primary_sha256": result["output_sha256"],
        "checker_sha256": hashfile(script),
        "verified_message_continuity": False,
        "verified_receiver_time": False,
    }
    partial = receipt.with_suffix(".json.part")
    with partial.open("x", encoding="utf-8") as output:
        json.dump(report, output, sort_keys=True, indent=2)
        output.write("\n")
    partial.rename(receipt)
    print(json.dumps({
        "status": report["status"], "symbol": symbol,
        "days": days, "rows": seen,
        "receipt_sha256": hashfile(receipt),
        "output": str(receipt),
    }))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, choices=SYMS)
    parser.add_argument("--days", required=True, type=int, choices=(2, 30))
    args = parser.parse_args()
    check(args.symbol, args.days)
