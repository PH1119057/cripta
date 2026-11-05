#!/usr/bin/env python3
"""Bounded, immutable, research-only minute facts from native Bybit archives.

V1 covers ONLY the five symbols supported by the published independent
trade/book readers. It does not preserve native tick/OB200 event order and
NEVER authorizes deletion/replacement of compressed raw archives.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import math
import os
import pathlib
import pwd
import resource
import shutil
import sys
import threading
import time
from decimal import Decimal

ROOT = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
RUNS = pathlib.Path("/data/cripta/research/runs")
MODULES = pathlib.Path("/srv/cripta/source_checkout/research/microstructure/experiments")
COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FILES = (
    "trade_minute_factors_v2_exact_timestamp_floor.py",
    "r158_firstmonth_book9_11_29_utcclip_v2.py",
    "r158_firstmonth_book12_visible_decrease_v1.py",
    "trade_minute_full_row_crosscheck_v2.py",
    "r158_firstmonth_book9_11_29_utcclip_independent_qc_v2.py",
    "r158_firstmonth_book12_visible_decrease_independent_qc_v1.py",
)
FIELD_BOOK = (
    "factor_9_queue_imbalance_near5bp", "factor_11_bid_depth_usdt_near5bp",
    "factor_11_ask_depth_usdt_near5bp", "factor_29_spread_bps",
    "best_bid_quote", "best_ask_quote", "quality",
    "last_book_event_timestamp_ms", "book_event_age_to_derived_end_ms",
)
FIELD_TRADE = (
    "fill_count", "trade_rate_accel_per_min", "buy_notional_usd",
    "sell_notional_usd", "delta_usd", "cvd_from_day_start_usd",
    "delta_accel_usd", "rvol_60m",
)
FIELD_DECREASE = (
    ("book_messages", "messages"), ("snapshots", "snapshots"),
    ("deltas", "deltas"), ("bid_decrease_events", "bid_count"),
    ("ask_decrease_events", "ask_count"),
    ("bid_visible_decrease_usdt", "bid_quote"),
    ("ask_visible_decrease_usdt", "ask_quote"),
)
FIELDS_PERSIST_TRADE = (
    "fill_count", "trade_rate_accel_per_min", "buy_notional_usd",
    "sell_notional_usd", "delta_usd", "cvd_from_day_start_usd",
    "delta_accel_usd", "rvol_60m", "close",
    "close_staleness_minutes", "log_return_1m",
)
FIELDS_PERSIST_BOOK = (
    "factor_9_queue_imbalance_near5bp", "factor_11_bid_depth_usdt_near5bp",
    "factor_11_ask_depth_usdt_near5bp", "factor_29_spread_bps",
    "best_bid_quote", "best_ask_quote", "last_book_event_timestamp_ms",
    "book_event_age_to_derived_end_ms", "book_levels_bid",
    "book_levels_ask", "quality",
)
FIELDS_PERSIST_DECREASE = (
    "book_messages", "snapshots", "deltas", "bid_decrease_events",
    "ask_decrease_events", "bid_visible_decrease_usdt",
    "ask_visible_decrease_usdt",
)

def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for piece in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(piece)
    return h.hexdigest()

def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n").encode("utf-8")

def write_once(path, payload):
    if path.exists() or path.with_name(path.name + ".part").exists():
        raise FileExistsError(str(path))
    partial = path.with_name(path.name + ".part")
    with partial.open("xb") as dst:
        dst.write(payload)
        dst.flush()
        os.fsync(dst.fileno())
    partial.rename(path)

def load(name):
    path = MODULES / name
    spec = importlib.util.spec_from_file_location("research_" + name.replace(".", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def compare(primary_trade, primary_book, primary_decrease, independent_trade, independent_book, independent_decrease, date, symbol):
    states, messages, snapshots, _ = independent_book.source_minute_states(
        symbol, date, ROOT / symbol / "orderbook"
        / f"{date.isoformat()}_{symbol}_ob200.data.zip"
    )
    epoch = int(dt.datetime.combine(date, dt.time(), dt.UTC).timestamp() * 1000)
    counts = {"book": 0, "decrease": 0, "trade": 0}
    for idx, (raw, state) in enumerate(zip(primary_book, states, strict=True)):
        other = independent_book.expected_from_state(state, epoch, idx)
        for field in FIELD_BOOK:
            a, b = raw[field], other[field]
            if field in ("quality", "last_book_event_timestamp_ms", "book_event_age_to_derived_end_ms"):
                valid = a == b
            else:
                valid = (a is None and b is None or
                         a is not None and b is not None and Decimal(str(a)) == b)
            if not valid:
                raise ValueError(f"BOOK_QC_MISMATCH at {idx} {field}")
            counts["book"] += 1
    is_, totals, _ = independent_decrease.independently_measure(
        date, ROOT / symbol / "orderbook"
        / f"{date.isoformat()}_{symbol}_ob200.data.zip", symbol
    )
    if totals["messages"] != messages or totals["snapshots"] != snapshots:
        raise ValueError("INDEPENDENT_BOOK_TOTALS_DIVERGE")
    for idx, (row, ref) in enumerate(zip(primary_decrease, is_, strict=True)):
        for field, key in FIELD_DECREASE:
            if Decimal(str(row[field])) != Decimal(str(ref[key])):
                raise ValueError(f"DECREASE_QC_MISMATCH at {idx} {field}")
            counts["decrease"] += 1
    checked_trade = independent_trade.run(symbol, date.isoformat())
    for idx, (row, ref) in enumerate(zip(primary_trade, checked_trade, strict=True)):
        for field in FIELD_TRADE:
            a, b = row[field], ref[field]
            if a is None or b is None:
                valid = a is None and b is None
            elif field in ("fill_count", "trade_rate_accel_per_min"):
                valid = a == b
            else:
                valid = math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-6)
            if not valid:
                raise ValueError(f"TRADE_QC_MISMATCH at {idx} {field}")
            counts["trade"] += 1
    if counts != {"book": 12960, "decrease": 10080, "trade": 11520}:
        raise ValueError("INDEPENDENT_COMPARISON_COUNT_INVALID")
    return counts, messages, snapshots

def day_compute(date, symbol, modules, output):
    trade_path = ROOT / symbol / "public_trades" / f"{symbol}{date.isoformat()}.csv.gz"
    book_path = ROOT / symbol / "orderbook" / f"{date.isoformat()}_{symbol}_ob200.data.zip"
    inputs = {}
    for source in (trade_path, book_path):
        if not source.is_file() or not os.access(source, os.R_OK):
            raise FileNotFoundError(str(source))
        inputs[str(source)] = {"sha256": sha256(source), "bytes": source.stat().st_size}
    trade_core, book_core, decrease_core, trade_qc, book_qc, decrease_qc = modules
    trade = trade_core.calculate(symbol, date.isoformat())
    book, bm = book_core.day_rows(symbol, date, book_path)
    decreases, dm = decrease_core.snapshots_and_deltas(date, book_path, symbol)
    if not all(len(arr) == 1440 for arr in (trade["rows"], book, decreases)):
        raise ValueError("INCOMPLETE_UTC_MINUTE_GRID")
    if bm["messages"] != dm["messages"] or bm["snapshots"] != dm["snapshots"]:
        raise ValueError("PRIMARY_BOOK_TOTALS_DIVERGE")
    checks, im, iss = compare(
        trade["rows"], book, decreases,
        trade_qc, book_qc, decrease_qc, date, symbol
    )
    if im != bm["messages"] or iss != bm["snapshots"]:
        raise ValueError("INDEPENDENT_BOOK_TOTALS_DIVERGE")
    rows = []
    for item, b, dd in zip(trade["rows"], book, decreases, strict=True):
        rows.append({
            "utc_minute": item["minute_utc"],
            "derived_minute_end": item["available_after_utc"],
            "verified_receiver_available_after": None,
            "instrument": "BYBIT_LINEAR:" + symbol,
            "trade_source": "BYBIT_PUBLIC_TRADES_ARCHIVE",
            "book_source": "BYBIT_OB200_ARCHIVE",
            "trade": {k: item.get(k) for k in FIELDS_PERSIST_TRADE},
            "book": {k: b.get(k) for k in FIELDS_PERSIST_BOOK},
            "book_observations": {
                k: str(dd.get(k)) if isinstance(dd.get(k), Decimal) else dd.get(k)
                for k in FIELDS_PERSIST_DECREASE
            },
        })
    plain = "\n".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows
    ).encode("utf-8")
    blob = gzip.compress(plain, compresslevel=6, mtime=0)
    name = f"{symbol}_{date.isoformat()}_minute.jsonl.gz"
    write_once(output / name, blob)
    if sha256(output / name) != hashlib.sha256(blob).hexdigest():
        raise ValueError("PERSISTED_GZIP_SHA_MISMATCH")
    with gzip.open(output / name, "rt", encoding="utf-8") as inp:
        reread = [json.loads(x) for x in inp]
    if len(reread) != 1440 or reread[0]["utc_minute"] != f"{date.isoformat()}T00:00:00+00:00" or reread[-1]["utc_minute"] != f"{date.isoformat()}T23:59:00+00:00":
        raise ValueError("READBACK_MINUTES_INVALID")
    if sum(x["trade"]["fill_count"] for x in reread) != trade["source_fills"]:
        raise ValueError("TRADE_FILLS_NOT_PRESERVED")
    return {
        "date": date.isoformat(), "input_provenance": inputs,
        "trade_fills": trade["source_fills"],
        "book_messages": bm["messages"], "book_snapshots": bm["snapshots"],
        "observed_book_minutes": bm["minute_observed"],
        "independent_checks": checks, "independent_mismatches": 0,
        "output": name, "output_bytes": len(blob),
        "output_sha256": hashlib.sha256(blob).hexdigest(),
        "raw_input_bytes": sum(x["bytes"] for x in inputs.values()),
        "receiver_timestamp_proven": False, "book_source_continuity_proven": False,
        "raw_delete_allowed": False,
    }

def main():
    arg = argparse.ArgumentParser(description=__doc__)
    arg.add_argument("--symbol", required=True, choices=COHORT)
    arg.add_argument("--start", required=True)
    arg.add_argument("--days", required=True, type=int)
    arg.add_argument("--run-id", required=True)
    opts = arg.parse_args()
    try:
        start = dt.date.fromisoformat(opts.start)
    except ValueError as exc:
        raise ValueError("INVALID_START_DATE") from exc
    if not (1 <= opts.days <= 7):
        raise ValueError("BOUNDED_PILOT_ONLY_MAX_7_DAYS")
    if start < dt.date(2026, 5, 18) or start + dt.timedelta(days=opts.days) > dt.date(2026, 8, 16):
        raise ValueError("DATE_RANGE_OUTSIDE_ARCHIVE")
    if opts.run_id != f"minute_chunk_v1_{opts.symbol}_{start.strftime('%Y%m%d')}_{opts.days}d_20261105":
        raise ValueError("EXACT_IMMUTABLE_RUN_ID_REQUIRED")
    actor = pwd.getpwnam("cripta").pw_uid
    if os.geteuid() != actor:
        raise PermissionError("EFFECTIVE_WRITER_MUST_BE_CRIPTA")
    if not os.access(RUNS, os.W_OK | os.X_OK):
        raise PermissionError("RESEARCH_PARENT_NOT_WRITABLE")
    destination = RUNS / opts.run_id
    if destination.exists() or shutil.disk_usage(RUNS).free < 1024**3:
        raise RuntimeError("TARGET_EXISTS_OR_DISK_RESERVE_FAILED")
    source_checks = {n: sha256(MODULES / n) for n in FILES}
    dates = [start + dt.timedelta(days=i) for i in range(opts.days)]
    for date in dates:
        for path in (
            ROOT / opts.symbol / "public_trades" / f"{opts.symbol}{date.isoformat()}.csv.gz",
            ROOT / opts.symbol / "orderbook" / f"{date.isoformat()}_{opts.symbol}_ob200.data.zip",
        ):
            if not path.is_file() or not os.access(path, os.R_OK):
                raise FileNotFoundError(str(path))
    os.umask(0o007)
    destination.mkdir(mode=0o2770)
    snap = destination / "source_snapshot"
    snap.mkdir(mode=0o2770)
    actual_script = globals().get("__snapshot_source__")
    if not isinstance(actual_script, str):
        actual_script = pathlib.Path(__file__).read_text(encoding="utf-8")
    write_once(snap / "native_minute_chunk_v1.py", actual_script.encode("utf-8"))
    for n in FILES:
        write_once(snap / n, (MODULES / n).read_bytes())
        if sha256(snap / n) != source_checks[n]:
            raise ValueError("SNAPSHOT_SOURCE_MISMATCH")
    write_once(destination / "source_commit.txt",
               b"63c6d645fbaf557fd366757e37db06eb32c756a2\n")
    write_once(destination / "command.txt",
               encoded({"symbol": opts.symbol, "start": opts.start,
                        "days": opts.days, "run_id": opts.run_id}))
    write_once(destination / "environment.txt",
               encoded({"unix_actor": "cripta", "pid": os.getpid(),
                        "python": sys.version, "source_repo": "PH1119057/cripta:main",
                        "source_head": "63c6d645fbaf557fd366757e37db06eb32c756a2",
                        "isolated_branch_source": "research/compact-native-minute-pilot-20261105",
                        "trading_mutation": False}))
    write_once(destination / "run_manifest.json",
               encoded({"status": "RUNNING", "scope": "bounded replay partial features",
                        "symbol": opts.symbol, "start": opts.start, "days": opts.days,
                        "raw_purge_allowed": False,
                        "source_files_sha256": source_checks}))
    modules = tuple(load(n) for n in FILES)
    started = time.monotonic()
    indicator = {"phase": "setup"}
    halt = threading.Event()
    def heartbeat():
        while not halt.wait(25):
            print(json.dumps({"phase": "HEARTBEAT", "detail": indicator,
                              "elapsed_s": round(time.monotonic() - started, 1),
                              "rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}), flush=True)
    ticker = threading.Thread(target=heartbeat, daemon=True)
    ticker.start()
    report = []
    try:
        for i, date in enumerate(dates):
            indicator.clear()
            indicator.update({"date": date.isoformat(), "day_index": i + 1, "total_days": len(dates)})
            print(json.dumps({"phase": "DAY_START", "detail": indicator}), flush=True)
            t0 = time.monotonic()
            result = day_compute(date, opts.symbol, modules, destination)
            write_once(destination / f"{date.isoformat()}_qc.json", encoded(result))
            report.append(result)
            print(json.dumps({"phase": "DAY_COMPLETE", "day": date.isoformat(),
                              "processed": i+1, "total": len(dates),
                              "elapsed_day_s": round(time.monotonic()-t0, 2),
                              "output_bytes": result["output_bytes"], "qc_mismatches": 0}), flush=True)
            if shutil.disk_usage(RUNS).free < 1024**3:
                raise RuntimeError("DATA_DISK_BELOW_MINIMUM_FREE")
    finally:
        halt.set()
        ticker.join(timeout=2)
    summary = {
        "status": "COMPLETE_FOR_BOUNDED_CHUNK_QC_NOT_RAW_REPLACEMENT",
        "symbol": opts.symbol, "start": opts.start, "days": opts.days,
        "daily": report,
        "total_minutes": 1440 * len(report),
        "total_fills": sum(x["trade_fills"] for x in report),
        "total_book_messages": sum(x["book_messages"] for x in report),
        "total_independent_cells": sum(sum(x["independent_checks"].values()) for x in report),
        "total_independent_mismatches": 0,
        "total_output_bytes": sum(x["output_bytes"] for x in report),
        "raw_input_bytes": sum(x["raw_input_bytes"] for x in report),
        "rss_peak_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "elapsed_seconds": round(time.monotonic()-started, 2),
        "raw_delete_allowed": False,
        "limitations": [
            "Only five verified research symbols",
            "Native public trade event sequence NOT retained",
            "Individual OB200 delta events NOT retained",
            "Receiver clock and continuous source sequence UNKNOWN",
            "Missing OI funding liquidations and full set of market dimensions",
            "NOT eligible for raw expiry or tick-order research",
        ]
    }
    write_once(destination / "result.json", encoded(summary))
    write_once(destination / "final_manifest.json", encoded({
        "status": summary["status"], "result_sha256": sha256(destination / "result.json"),
        "source_builder_sha256": sha256(snap / "native_minute_chunk_v1.py"),
        "raw_purge_allowed": False
    }))
    print(json.dumps({"phase": "COMPLETE", "status": summary["status"],
                      "job_result": str(destination / "result.json"),
                      "days": len(report), "minutes": summary["total_minutes"],
                      "qc_cells": summary["total_independent_cells"],
                      "qc_mismatches": 0, "output_bytes": summary["total_output_bytes"],
                      "rss_peak_mib": summary["rss_peak_mib"],
                      "elapsed_seconds": summary["elapsed_seconds"],
                      "raw_deletions": 0}), flush=True)

if __name__ == "__main__":
    main()
