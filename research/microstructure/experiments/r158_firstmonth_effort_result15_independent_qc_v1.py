#!/usr/bin/env python3
"""Independent full-minute Decimal replay QC for physical #15, first month only.

This is an independent aggregation from raw trades; no import of materializer.
Does not verify receiver publication, market completeness or trading edge.
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import os
import pathlib
import shutil
from decimal import Decimal

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FIRST = dt.date(2026, 5, 18)
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
OUT = pathlib.Path(
    "/data/cripta/research/runs/r158_firstmonth_effort_result15_20261023"
)
PHYSICAL = "PHYSICAL_15_COMPLETE_PENDING_INDEPENDENT_QC"
PASS = "EFFORT_RESULT_15_FULL_MINUTE_DECIMAL_QC_PASS_NOT_RESEARCH_READY"
FORMULA = "effort_result_intraminute_endpoints_quote_million_v1"


def hashfile(path):
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(1048576), b""):
            hasher.update(part)
    return hasher.hexdigest()


def calculate_independent(source, day):
    """Reconstruct a UTC minute's list of prints then derive exact Decimal."""
    epoch = Decimal(int(dt.datetime.combine(
        day, dt.time(), dt.UTC
    ).timestamp()))
    buckets = [[] for _ in range(1440)]
    preceding = None
    with gzip.open(source, "rt", newline="") as f:
        for record in csv.DictReader(f):
            timestamp = Decimal(record["timestamp"])
            if not timestamp.is_finite():
                raise ValueError("QC_TIMESTAMP_NOT_FINITE")
            if preceding is not None and timestamp < preceding:
                raise ValueError("QC_SOURCE_TIMESTAMP_NONMONOTONE")
            preceding = timestamp
            minute = int((timestamp - epoch) // Decimal(60))
            if minute not in range(1440):
                raise ValueError("QC_SOURCE_OUTSIDE_DAY")
            p, q = Decimal(record["price"]), Decimal(record["foreignNotional"])
            if not p.is_finite() or p <= 0 or not q.is_finite() or q < 0:
                raise ValueError("QC_SOURCE_INVALID_PRICE_OR_VOLUME")
            buckets[minute].append((p, q))
    for prints in buckets:
        if not prints:
            yield {
                "fill_count": 0,
                "first_trade_price": None,
                "last_trade_price": None,
                "quote_turnover_usdt": Decimal(0),
                "abs_intraminute_endpoint_bps": None,
                "effort_result_bps_per_million_usdt": None,
                "quality": "NO_PRINTS_IN_MINUTE",
            }
            continue
        volume = sum((q for _, q in prints), Decimal(0))
        first = prints[0][0]
        last = prints[-1][0]
        movement = abs(last / first - 1) * Decimal(10000)
        computed = (
            movement / (volume / Decimal(1000000)) if volume else None
        )
        yield {
            "fill_count": len(prints),
            "first_trade_price": first,
            "last_trade_price": last,
            "quote_turnover_usdt": volume,
            "abs_intraminute_endpoint_bps": movement,
            "effort_result_bps_per_million_usdt": computed,
            "quality": (
                "NUMERIC_PHYSICAL_SOURCE_CONTINUITY_UNKNOWN" if volume
                else "ZERO_QUOTE_NOTIONAL"
            ),
        }


def compare_row(row, expected, symbol, day, minute, source_hash):
    now = dt.datetime.combine(
        day, dt.time(), dt.UTC
    ) + dt.timedelta(minutes=minute)
    if (
        row["instrument_id"] != symbol
        or row["indicator_id"] != 15
        or row["venue_id"] != "Bybit"
        or row["formula_version"] != FORMULA
        or row["window_start_utc"] != now.isoformat()
        or row["derived_window_end_utc"] != (
            now + dt.timedelta(minutes=1)
        ).isoformat()
        or row["source_archive_sha256"] != source_hash
        or row["observed_at_utc"] is not None
        or row["verified_available_after_utc"] is not None
        or row["quality"] != expected["quality"]
        or row["fill_count"] != expected["fill_count"]
    ):
        raise ValueError("QC_ROW_METADATA_OR_FILL_MISMATCH " + str(minute))
    for key in (
        "first_trade_price",
        "last_trade_price",
        "quote_turnover_usdt",
        "abs_intraminute_endpoint_bps",
        "effort_result_bps_per_million_usdt",
    ):
        actual = row[key]
        calculated = expected[key]
        if calculated is None:
            if actual is not None:
                raise ValueError("QC_EXPECTED_NULL " + key)
        elif actual is None or Decimal(actual) != calculated:
            raise ValueError(
                "QC_DECIMAL_VALUE_DIFF " + key + " " + str(minute)
            )


def check(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("QC_ACTOR_NOT_CRIPTA")
    if symbol not in SYMS or days not in (2, 30):
        raise ValueError("QC_SCOPE_INVALID")
    dest = OUT / ("physical_" + str(days) + "d") / symbol
    r = json.loads((dest / "result.json").read_text())
    evidence = json.loads((dest / "input_provenance.json").read_text())["days"]
    if (
        r.get("status") != PHYSICAL or r.get("symbol") != symbol
        or r.get("days") != days or r.get("rows") != days * 1440
        or r.get("source_continuity_verified") is not False
        or r.get("receiver_clock_verified") is not False
        or len(evidence) != days
    ):
        raise ValueError("QC_RUN_SCOPE_INVALID")
    original = dest / "features.jsonl.gz"
    snapshot = dest / "source_snapshot/materializer.py"
    if hashfile(original) != r["output_sha256"]:
        raise ValueError("QC_OUTPUT_HASH_MISMATCH")
    if hashfile(snapshot) != r["program_sha256"]:
        raise ValueError("QC_MATERIALIZER_SNAPSHOT_DRIFT")
    receipt = dest / "independent_qc_report.json"
    if receipt.exists():
        raise FileExistsError("QC_ALREADY_EXISTS_DO_NOT_OVERWRITE")
    day_stats = []
    counted = 0
    with gzip.open(original, "rt") as output:
        for i in range(days):
            day = FIRST + dt.timedelta(days=i)
            source = RAW / symbol / "public_trades" / (
                symbol + day.isoformat() + ".csv.gz"
            )
            source_hash = hashfile(source)
            expected_manifest = evidence[i]
            if (
                expected_manifest["date_utc"] != day.isoformat()
                or expected_manifest["source_path"] != str(source)
                or expected_manifest["source_sha256"] != source_hash
                or expected_manifest["source_bytes"] != source.stat().st_size
            ):
                raise ValueError("QC_INPUT_PROVENANCE_MISMATCH")
            daily_fills = 0
            for minute, expected in enumerate(
                calculate_independent(source, day)
            ):
                rawline = output.readline()
                if not rawline:
                    raise ValueError("QC_MISSING_MINUTE_ROWS")
                row = json.loads(rawline)
                compare_row(
                    row, expected, symbol, day, minute, source_hash
                )
                daily_fills += expected["fill_count"]
                counted += 1
            if (
                daily_fills != expected_manifest["fills"]
                or expected_manifest["minute_rows"] != 1440
            ):
                raise ValueError("QC_DAILY_COUNTS_MISMATCH")
            day_stats.append({
                "day": day.isoformat(), "fills": daily_fills,
                "source_sha256": source_hash, "rows": 1440,
            })
            print(json.dumps({
                "stage": "INDEPENDENT_DECIMAL_QC_15",
                "symbol": symbol, "finished_day": i + 1,
                "days_total": days, "heartbeat": True,
            }), flush=True)
        if output.readline():
            raise ValueError("QC_EXTRA_MINUTE_ROWS")
    if counted != days * 1440:
        raise ValueError("QC_TOTAL_ROWS_MISMATCH")
    script = pathlib.Path(__file__).resolve()
    report = {
        "status": PASS, "symbol": symbol, "days": days,
        "verified_minute_rows": counted,
        "original_sha256": r["output_sha256"],
        "checker_sha256": hashfile(script),
        "day_stats": day_stats,
        "message_continuity_verified": False,
        "receiver_clock_verified": False,
        "research_ready": False,
    }
    snapshot_target = dest / "source_snapshot/independent_checker.py"
    if snapshot_target.exists():
        raise FileExistsError("QC_CHECKER_SNAPSHOT_ALREADY_EXISTS")
    shutil.copyfile(script, snapshot_target)
    partial = receipt.with_name(receipt.name + ".part")
    with partial.open("x", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    partial.rename(receipt)
    print(json.dumps({
        "status": PASS, "symbol": symbol, "rows": counted,
        "receipt_sha256": hashfile(receipt), "path": str(receipt),
    }), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True, choices=SYMS)
    parser.add_argument("--days", required=True, type=int, choices=(2, 30))
    args = parser.parse_args()
    check(args.symbol, args.days)
