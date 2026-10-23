#!/usr/bin/env python3
"""Independent #21 raw full-minute numerical QC by distinct-timestamp groups.

Independent grouping/re-entry formula is a first-crossing-index search.
A price return inside the SAME exact timestamp group never proves ordering.
No book depth, source receipt clock or research readiness is established.
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
from collections import deque
from decimal import Decimal

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FIRST = dt.date(2026, 5, 18)
RAW = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
ROOT = pathlib.Path("/data/cripta/research/runs/r158_firstmonth_print21_distinct_timestamp_20261023")
FORMULA = "print21_prior60_1m_trade_extrema_distinct_timestamp_reentry_v1"
PRIMARY = "PRINT21_DISTINCT_TS_PHYSICAL_COMPLETE_PENDING_INDEPENDENT_QC"
PASS = "PRINT21_DISTINCT_TS_FULL_MINUTE_DECIMAL_QC_PASS_NOT_RESEARCH_READY"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_source(day, path):
    start = Decimal(int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp()))
    grouped = [[] for _ in range(1440)]
    previous = None
    with gzip.open(path, "rt", newline="") as f:
        for r in csv.DictReader(f):
            stamp, price = Decimal(r["timestamp"]), Decimal(r["price"])
            if (not stamp.is_finite() or not price.is_finite() or price <= 0
                    or previous is not None and stamp < previous):
                raise ValueError("QC_RAW_INVALID_OR_UNSORTED")
            previous = stamp
            minute = int((stamp - start) // Decimal(60))
            if not 0 <= minute < 1440:
                raise ValueError("QC_TRADE_WRONG_UTC_DAY")
            groups = grouped[minute]
            if groups and groups[-1]["ts"] == stamp:
                g = groups[-1]
                g["count"] += 1
                g["minimum"] = min(g["minimum"], price)
                g["maximum"] = max(g["maximum"], price)
            else:
                groups.append({"ts": stamp, "minimum": price,
                               "maximum": price, "count": 1})
    return grouped


def recompute(history, groups):
    fills = sum(g["count"] for g in groups)
    if not fills:
        return None, "NO_CURRENT_PRINT"
    if len(history) != 60:
        return None, "INSUFFICIENT_PRIOR60_MINUTES"
    if any(slot[0] == 0 for slot in history):
        return None, "PRIOR60_CONTAINS_NO_PRINT_MINUTE"
    upper = max(item[2] for item in history)
    lower = min(item[1] for item in history)
    upper_cross = [i for i, x in enumerate(groups) if x["maximum"] > upper]
    lower_cross = [i for i, x in enumerate(groups) if x["minimum"] < lower]
    upper_seen = bool(upper_cross)
    lower_seen = bool(lower_cross)
    upper_return = (any(groups[i]["minimum"] <= upper
                        for i in range(upper_cross[0]+1, len(groups)))
                    if upper_seen else False)
    lower_return = (any(groups[i]["maximum"] >= lower
                        for i in range(lower_cross[0]+1, len(groups)))
                    if lower_seen else False)
    ties = [g for g in groups if g["count"] > 1]
    return {
        "prior60_trade_print_high": upper,
        "prior60_trade_print_low": lower,
        "current_trade_print_high": max(x["maximum"] for x in groups),
        "current_trade_print_low": min(x["minimum"] for x in groups),
        "high_trade_print_cross": upper_seen,
        "low_trade_print_cross": lower_seen,
        "high_reentry_later_timestamp": upper_return,
        "low_reentry_later_timestamp": lower_return,
        "high_same_timestamp_ambiguous": any(
            g["minimum"] <= upper < g["maximum"] for g in groups),
        "low_same_timestamp_ambiguous": any(
            g["minimum"] < lower <= g["maximum"] for g in groups),
        "tied_timestamp_groups": len(ties),
        "multiprice_tied_groups": sum(
            g["minimum"] != g["maximum"] for g in ties),
    }, "OBSERVED_PRIOR60_AND_CURRENT_PRINTS"


def check_decimal(observed, wanted, label):
    if wanted is None:
        if observed is not None:
            raise ValueError("QC_EXPECTED_NULL " + label)
    elif observed is None or Decimal(observed) != wanted:
        raise ValueError("QC_NUMERIC_DIFFERENCE " + label)


def verify(symbol, days):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("QC_ACTOR_NOT_CRIPTA")
    if symbol not in SYMS or days not in (2, 30):
        raise ValueError("QC_SCOPE_UNAPPROVED")
    folder = ROOT / f"physical_{days}d" / symbol
    receipt = folder / "independent_qc_report.json"
    if receipt.exists() or (folder / "independent_qc_report.json.part").exists():
        raise FileExistsError("QC_RECEIPT_EXISTS_DO_NOT_OVERWRITE")
    result = json.loads((folder / "result.json").read_text())
    sources = json.loads((folder / "input_provenance.json").read_text())["days"]
    datafile = folder / "features_1m.jsonl.gz"
    materializer = folder / "source_snapshot/materializer.py"
    if (result.get("status") != PRIMARY
            or result.get("indicator_id") != 21
            or result.get("symbol") != symbol or result.get("days") != days
            or result.get("rows") != 1440 * days
            or result.get("formula_version") != FORMULA
            or result.get("real_orderbook_sweep_verified") is not False
            or result.get("within_tied_timestamp_order_verified") is not False
            or result.get("receiver_clock_verified") is not False
            or result.get("research_ready") is not False or len(sources) != days):
        raise ValueError("QC_PRIMARY_METADATA_CONTRACT")
    if sha(datafile) != result["output_sha256"] or sha(materializer) != result["program_sha256"]:
        raise ValueError("QC_CODE_OR_ARTIFACT_SHA_MISMATCH")
    hist = deque(maxlen=60)
    counted = {"eligible": 0, "high_crossed": 0, "high_reentry": 0,
               "low_crossed": 0, "low_reentry": 0, "high_ambiguous": 0,
               "low_ambiguous": 0, "null": 0, "tied_groups": 0}
    proof = []
    rows = fills_total = 0
    with gzip.open(datafile, "rt") as output:
        for dayindex in range(days):
            day = FIRST + dt.timedelta(days=dayindex)
            path = RAW / symbol / "public_trades" / (symbol + day.isoformat() + ".csv.gz")
            digest = sha(path)
            old = sources[dayindex]
            if (old["date_utc"] != day.isoformat()
                    or old["source_path"] != str(path)
                    or old["source_sha256"] != digest
                    or old["source_bytes"] != path.stat().st_size
                    or old["minute_rows"] != 1440):
                raise ValueError("QC_RAW_DAY_PROVENANCE_DRIFT")
            slots = parse_source(day, path)
            dayfills = 0
            for minute, groups in enumerate(slots):
                fills = sum(g["count"] for g in groups)
                dayfills += fills
                measurements, quality = recompute(hist, groups)
                if measurements is None:
                    counted["null"] += 1
                else:
                    counted["eligible"] += 1
                    for counter, col in [
                        ("high_crossed", "high_trade_print_cross"),
                        ("high_reentry", "high_reentry_later_timestamp"),
                        ("low_crossed", "low_trade_print_cross"),
                        ("low_reentry", "low_reentry_later_timestamp"),
                        ("high_ambiguous", "high_same_timestamp_ambiguous"),
                        ("low_ambiguous", "low_same_timestamp_ambiguous"),
                    ]:
                        counted[counter] += int(measurements[col])
                    counted["tied_groups"] += measurements["tied_timestamp_groups"]
                line = output.readline()
                if not line:
                    raise ValueError("QC_PRIMARY_OUTPUT_TRUNCATED")
                row = json.loads(line)
                start = dt.datetime.combine(day, dt.time(), dt.UTC) + dt.timedelta(minutes=minute)
                expected = {
                    "venue_id": "Bybit", "instrument_id": symbol,
                    "market_source_id": "BYBIT_PUBLIC_TRADES_ARCHIVE",
                    "indicator_id": 21, "formula_version": FORMULA,
                    "window_start_utc": start.isoformat(),
                    "derived_window_end_utc": (
                        start + dt.timedelta(minutes=1)).isoformat(),
                    "observed_at_utc": None,
                    "verified_available_after_utc": None,
                    "fill_count": fills,
                    "quality": quality,
                    "source_archive_sha256": digest,
                }
                for col in ("high_trade_print_cross", "low_trade_print_cross",
                            "high_reentry_later_timestamp", "low_reentry_later_timestamp",
                            "high_same_timestamp_ambiguous", "low_same_timestamp_ambiguous",
                            "tied_timestamp_groups", "multiprice_tied_groups"):
                    expected[col] = measurements[col] if measurements else None
                if any(row.get(col) != val for col, val in expected.items()):
                    raise ValueError("QC_MINUTE_FIELDS_OR_QUALITY " + str(rows))
                for col in ("prior60_trade_print_high", "prior60_trade_print_low",
                            "current_trade_print_high", "current_trade_print_low"):
                    check_decimal(row[col], measurements[col] if measurements else None, col)
                if fills:
                    hist.append((fills, min(x["minimum"] for x in groups),
                                 max(x["maximum"] for x in groups)))
                else:
                    hist.append((0, None, None))
                rows += 1
            if dayfills != old["fills"]:
                raise ValueError("QC_DAILY_TRADE_COUNT_DIFF")
            fills_total += dayfills
            proof.append({"date_utc": day.isoformat(), "source_sha256": digest,
                          "rows": 1440, "fills": dayfills})
            print(json.dumps({"stage": "PRINT21_INDEPENDENT_QC",
                              "symbol": symbol, "days_done": dayindex+1,
                              "days_total": days, "heartbeat": True}), flush=True)
        if output.readline():
            raise ValueError("QC_EXTRA_FEATURE_ROWS")
    if (rows != days * 1440 or fills_total != result["fills"]
            or counted != result["counts"]):
        raise ValueError("QC_COHORT_TOTALS_MISMATCH")
    checker = pathlib.Path(__file__).resolve()
    snapshot = folder / "source_snapshot/independent_checker.py"
    if snapshot.exists():
        raise FileExistsError("QC_CHECKER_SNAPSHOT_ALREADY_EXISTS")
    shutil.copyfile(checker, snapshot)
    report = {
        "status": PASS, "symbol": symbol, "days": days,
        "verified_minute_rows": rows, "verified_fills": fills_total,
        "counts": counted, "source_days": proof,
        "indicator_id": 21, "formula_version": FORMULA,
        "original_sha256": result["output_sha256"], "checker_sha256": sha(checker),
        "real_orderbook_sweep_verified": False,
        "within_tied_timestamp_order_verified": False,
        "receiver_clock_verified": False,
        "source_continuity_verified": False, "research_ready": False,
    }
    partial = receipt.with_name(receipt.name + ".part")
    with partial.open("x", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    partial.rename(receipt)
    print(json.dumps({"status": PASS, "symbol": symbol,
                      "verified_minute_rows": rows,
                      "receipt_sha256": sha(receipt)}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", choices=SYMS, required=True)
    p.add_argument("--days", choices=(2, 30), type=int, required=True)
    a = p.parse_args()
    verify(a.symbol, a.days)
