#!/usr/bin/env python3
"""Bounded 7-day Bybit LINEAR research-only combined trade/OB200 minute mart.

TRADE input: previously persisted and independently checked trade-minute mart.
BOOK input: full native OB200 compressed source read independently four times
(two primary and two independent QC parser paths) per UTC day.
All writes limited to immutable /data/cripta/research/runs/<run-id> by cripta.
This is NOT lossless native trade/OB200 replay, NOT R8 canonical publication,
and DOES NOT permit expiry/deletion of original source archives.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import os
import pathlib
import pwd
import shutil
import sys
import threading
import time
from decimal import Decimal

VERSION = "historical-combined-partial-minute-mart-v1"
ROOT = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
RUNS = pathlib.Path("/data/cripta/research/runs")
MODULES = pathlib.Path("/srv/cripta/source_checkout/research/microstructure/experiments")
SYMBOL = "TRXUSDT"
FIRST = dt.date(2026, 5, 18)
DAYS = 7
TRADES = RUNS / "historical_trade_minute_mart_v1_TRXUSDT_20260518_7d_20261105"
RUN_ID = "historical_combined_minute_mart_v1_TRXUSDT_20260518_7d_20261106"
REQUIRED_FREE = 8 * 1024**3
MAX_BOOK_ZIP = 300 * 1024**2
SOURCES = {
    "book": "r158_firstmonth_book9_11_29_utcclip_v2.py",
    "book_qc": "r158_firstmonth_book9_11_29_utcclip_independent_qc_v2.py",
    "decrease": "r158_firstmonth_book12_visible_decrease_v1.py",
    "decrease_qc": "r158_firstmonth_book12_visible_decrease_independent_qc_v1.py",
}
BOOK_FIELDS = (
    "factor_9_queue_imbalance_near5bp", "factor_11_bid_depth_usdt_near5bp",
    "factor_11_ask_depth_usdt_near5bp", "factor_29_spread_bps",
    "best_bid_quote", "best_ask_quote", "quality",
    "last_book_event_timestamp_ms", "book_event_age_to_derived_end_ms",
)
BOOK_SAVED = BOOK_FIELDS + ("book_levels_bid", "book_levels_ask")
DECREASE_MAP = (
    ("book_messages", "messages"), ("snapshots", "snapshots"),
    ("deltas", "deltas"), ("bid_decrease_events", "bid_count"),
    ("ask_decrease_events", "ask_count"),
    ("bid_visible_decrease_usdt", "bid_quote"),
    ("ask_visible_decrease_usdt", "ask_quote"),
)

def digest(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def encoded(obj) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()

def write_once(path: pathlib.Path, data: bytes) -> None:
    temp = path.with_name(path.name + ".part")
    if path.exists() or temp.exists():
        raise FileExistsError(str(path))
    with temp.open("xb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    temp.rename(path)

def import_module(name, snap: pathlib.Path):
    path = snap / name
    spec = importlib.util.spec_from_file_location("research_" + name.replace(".", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def load_input_trade_mart():
    result = TRADES / "result.json"
    manifest = TRADES / "final_manifest.json"
    if not result.is_file() or not manifest.is_file():
        raise ValueError("PREVIOUS_TRADE_MART_NOT_FINAL")
    meta = json.loads(result.read_text())
    fin = json.loads(manifest.read_text())
    if digest(result) != fin.get("result_sha256"):
        raise ValueError("PREVIOUS_TRADE_MART_MANIFEST_BAD")
    if (
        meta.get("status") != "TRADE_ONLY_RESEARCH_MART_QC_PASS_NOT_FULL_MARKET_MART"
        or meta.get("symbol") != SYMBOL or meta.get("days") != DAYS
        or meta.get("rows") != 1440 * DAYS or meta.get("raw_purge_allowed") is not False
        or len(meta.get("per_day", [])) != DAYS
    ):
        raise ValueError("PREVIOUS_TRADE_MART_MISMATCH")
    return meta, fin

def preflight_inputs(meta):
    out = []
    for i in range(DAYS):
        day = FIRST + dt.timedelta(days=i)
        date = day.isoformat()
        info = meta["per_day"][i]
        if info["date_utc"] != date:
            raise ValueError("TRADE_MART_DATE_MISMATCH")
        book = ROOT / SYMBOL / "orderbook" / f"{date}_{SYMBOL}_ob200.data.zip"
        gz = TRADES / "results" / info["output_filename"]
        trade_raw = ROOT / SYMBOL / "public_trades" / f"{SYMBOL}{date}.csv.gz"
        for f in (book, gz, trade_raw):
            if not f.is_file() or f.is_symlink() or not os.access(f, os.R_OK):
                raise FileNotFoundError(str(f))
        if not (0 < book.stat().st_size <= MAX_BOOK_ZIP):
            raise ValueError("BOOK_INPUT_SIZE_CAP")
        if digest(gz) != info["output_sha256"] or digest(trade_raw) != info["source_sha256"]:
            raise ValueError("TRADE_SOURCE_OR_MART_SHA_DRIFT")
        out.append({
            "date_utc": date, "book_path": str(book),
            "book_bytes": book.stat().st_size, "book_sha256": digest(book),
            "trade_path": str(trade_raw),
            "trade_bytes": trade_raw.stat().st_size,
            "trade_sha256": info["source_sha256"],
            "trade_mart_path": str(gz), "trade_mart_sha256": info["output_sha256"],
            "independent_trade_mart_cells": info["independent_qc"]["independent_minute_cells"],
        })
    return out

def primary_and_independent(day, info, modules):
    book_path = pathlib.Path(info["book_path"])
    trade_path = pathlib.Path(info["trade_mart_path"])
    if digest(book_path) != info["book_sha256"] or digest(trade_path) != info["trade_mart_sha256"]:
        raise ValueError("INPUT_SHA_CHANGED_DURING_RUN")
    with gzip.open(trade_path, "rt", encoding="utf-8") as f:
        trades = [json.loads(s) for s in f]
    if len(trades) != 1440:
        raise ValueError("TRADE_GRID_BROKEN")
    book, bm = modules["book"].day_rows(SYMBOL, day, book_path)
    decreases, dm = modules["decrease"].snapshots_and_deltas(day, book_path, SYMBOL)
    states, msgs, snaps, clipped = modules["book_qc"].source_minute_states(SYMBOL, day, book_path)
    check_dec, totals, suffix = modules["decrease_qc"].independently_measure(day, book_path, SYMBOL)
    if not all(len(x) == 1440 for x in (book, decreases, states, check_dec)):
        raise ValueError("INCOMPLETE_BOOK_OBSERVED_MINUTE_GRID")
    if not (
        bm["messages"] == dm["messages"] == msgs == totals["messages"]
        and bm["snapshots"] == dm["snapshots"] == snaps == totals["snapshots"]
    ):
        raise ValueError("BOOK_SOURCE_TOTALS_DISAGREE")
    if dm["deltas"] != totals["deltas"] or dm["source_suffix_messages"] != suffix:
        raise ValueError("BOOK_DELTA_TOTALS_OR_SUFFIX_DISAGREE")
    epoch = int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp() * 1000)
    checks = {"book": 0, "decrease": 0, "trade_prior": info["independent_trade_mart_cells"]}
    combined = []
    for idx, (t, b, d, state, measure) in enumerate(
        zip(trades, book, decreases, states, check_dec, strict=True)
    ):
        expected_minute = (
            dt.datetime.combine(day, dt.time(), dt.UTC) + dt.timedelta(minutes=idx)
        ).isoformat()
        if t["observed_minute_utc"] != expected_minute:
            raise ValueError("TRADE_UTC_MINUTE_MISMATCH")
        if t["identity_binding"] != "UNBOUND_EXTERNAL_BYBIT_RESEARCH_SOURCE":
            raise ValueError("TRADE_MARKET_IDENTITY_UNEXPECTED")
        if t["external_symbol"] != SYMBOL or t["native_closed_candle"] is not False:
            raise ValueError("TRADE_IDENTITY_OR_NATIVE_CLOSE_FALSE")
        expected = modules["book_qc"].expected_from_state(state, epoch, idx)
        for key in BOOK_FIELDS:
            a, z = b[key], expected[key]
            if key in ("quality", "last_book_event_timestamp_ms", "book_event_age_to_derived_end_ms"):
                ok = a == z
            else:
                ok = a is None and z is None or a is not None and z is not None and Decimal(str(a)) == z
            if not ok:
                raise ValueError(f"INDEPENDENT_BOOK_MISMATCH_{idx}_{key}")
            checks["book"] += 1
        for key, other in DECREASE_MAP:
            if Decimal(str(d[key])) != Decimal(str(measure[other])):
                raise ValueError(f"INDEPENDENT_DECREASE_MISMATCH_{idx}_{key}")
            checks["decrease"] += 1
        combined.append({
            "schema_version": VERSION,
            "observed_minute_utc": expected_minute,
            "derived_available_after_utc": t["derived_available_after_utc"],
            "receiver_available_after_utc": None,
            "venue_identity_status": "EXTERNAL_ARCHIVE_UNBOUND_TO_CANONICAL_MARKET_SOURCE",
            "external_symbol": SYMBOL,
            "product_category": "LINEAR",
            "trade": t,
            "book": {k: b[k] for k in BOOK_SAVED},
            "visible_decrease": {
                k: str(d[k]) if isinstance(d[k], Decimal) else d[k] for k, other in DECREASE_MAP
            },
            "source_book_continuity_quality": "UNKNOWN",
        })
    if checks != {"book": 12960, "decrease": 10080, "trade_prior": 8640}:
        raise ValueError("INDEPENDENT_QC_EXPECTED_COUNTS_CHANGED")
    return combined, bm, dm, checks

def run_day(day, info, modules, outputdir):
    start = time.monotonic()
    combined, bm, dm, counts = primary_and_independent(day, info, modules)
    plain = "\n".join(json.dumps(x, sort_keys=True, separators=(",", ":")) for x in combined).encode()
    blob = gzip.compress(plain, compresslevel=6, mtime=0)
    filename = f"{SYMBOL}_{day.isoformat()}_combined_partial_minute.jsonl.gz"
    path = outputdir / filename
    write_once(path, blob)
    if digest(path) != hashlib.sha256(blob).hexdigest():
        raise ValueError("PERSISTED_SHA_MISMATCH")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        readback = [json.loads(line) for line in f]
    if (
        len(readback) != 1440
        or readback[0]["observed_minute_utc"] != day.isoformat() + "T00:00:00+00:00"
        or readback[-1]["observed_minute_utc"] != day.isoformat() + "T23:59:00+00:00"
        or sum(r["trade"]["sample_count"] for r in readback) <= 0
    ):
        raise ValueError("READBACK_INCOMPLETE")
    if digest(pathlib.Path(info["book_path"])) != info["book_sha256"]:
        raise ValueError("RAW_SOURCE_CHANGED_AFTER_QC")
    return {
        "date_utc": day.isoformat(),
        "raw_input_bytes": info["book_bytes"] + info["trade_bytes"],
        "raw_book_sha256": info["book_sha256"],
        "raw_trade_sha256": info["trade_sha256"],
        "minute_rows": 1440, "book_messages": bm["messages"],
        "book_snapshots": bm["snapshots"],
        "book_minutes_observed": bm["minute_observed"],
        "native_source_continuity_verified": False,
        "native_receiver_clock_verified": False,
        "book_meta": bm,
        "decrease_meta": dm,
        "source_qc_cells": counts, "source_mismatches": 0,
        "saved": filename, "bytes": len(blob), "sha256": digest(path),
        "readback_pass": True,
        "elapsed_s": round(time.monotonic() - start, 2),
        "raw_purge_allowed": False,
    }

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    if args.run_id != RUN_ID:
        raise ValueError("EXACT_RUN_ID_REQUIRED")
    if not (len(args.source_commit) == 40 and all(c in "0123456789abcdef" for c in args.source_commit)):
        raise ValueError("EXACT_SOURCE_SHA_REQUIRED")
    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("ACTOR_MUST_BE_CRIPTA")
    if not os.access(RUNS, os.W_OK | os.X_OK):
        raise PermissionError("RESEARCH_RUNS_PARENT_PERMISSIONS")
    if shutil.disk_usage(RUNS).free < REQUIRED_FREE:
        raise RuntimeError("DATA_DISK_BELOW_8GIB_RESERVE")
    target = RUNS / RUN_ID
    if target.exists():
        raise FileExistsError(str(target))
    trade_meta, trade_manifest = load_input_trade_mart()
    inputs = preflight_inputs(trade_meta)
    code_hashes = {name: digest(MODULES / script) for name, script in SOURCES.items()}
    code = globals().get("__snapshot_source__")
    if not isinstance(code, str):
        code = pathlib.Path(__file__).read_text(encoding="utf-8")
    os.umask(0o007)
    target.mkdir(mode=0o2770)
    snapshot = target / "source_snapshot"
    snapshot.mkdir(mode=0o2770)
    results = target / "results"
    results.mkdir(mode=0o2770)
    write_once(snapshot / "historical_combined_minute_mart_v1.py", code.encode())
    for name, script in SOURCES.items():
        write_once(snapshot / script, (MODULES / script).read_bytes())
        if digest(snapshot / script) != code_hashes[name]:
            raise ValueError("SNAPSHOT_READER_DIGEST_MISMATCH")
    write_once(target / "source_commit.txt", (args.source_commit + "\n").encode())
    write_once(target / "command.txt", encoded(vars(args)))
    write_once(target / "environment.txt", encoded({
        "actor": "cripta", "uid": os.geteuid(), "pid": os.getpid(),
        "source_checkout_mirror": "/srv/cripta/source_checkout",
        "python": sys.version, "writer": "research_only_data_disk",
    }))
    write_once(target / "input_provenance.json", encoded({
        "days": inputs, "previous_trade_mart_result_sha": digest(TRADES / "result.json"),
        "source_modules_sha256": code_hashes,
    }))
    write_once(target / "run_manifest.json", encoded({
        "status": "RUNNING", "source_commit": args.source_commit,
        "script_sha256": digest(snapshot / "historical_combined_minute_mart_v1.py"),
        "symbol": SYMBOL, "start": FIRST.isoformat(), "days": DAYS,
        "purpose": "PARTIAL_SOURCE_ONLY_RESEARCH_MART",
        "raw_delete_allowed": False,
    }))
    modules = {name: import_module(script, snapshot) for name, script in SOURCES.items()}
    halt = threading.Event()
    started = time.monotonic()
    phase = {"stage": "INIT"}
    def heartbeat():
        while not halt.wait(30):
            print(json.dumps({"heartbeat": phase.copy(),
                              "elapsed_s": round(time.monotonic() - started, 1)}), flush=True)
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    daily = []
    try:
        for idx, info in enumerate(inputs):
            day = FIRST + dt.timedelta(days=idx)
            if shutil.disk_usage(RUNS).free < REQUIRED_FREE:
                raise RuntimeError("DATA_DISK_RESERVE_BEFORE_NEXT_DAY")
            phase.update({"stage": "RUN_DAY", "date": day.isoformat(), "progress": idx, "total": DAYS})
            print(json.dumps({"phase": "DAY_START", "date": day.isoformat(), "idx": idx+1}), flush=True)
            stats = run_day(day, info, modules, results)
            write_once(results / (day.isoformat() + "_qc.json"), encoded(stats))
            daily.append(stats)
            phase.update({"stage": "DAY_COMPLETE", "progress": idx+1})
            print(json.dumps({"phase": "DAY_QC_PASS", "date": day.isoformat(), "minutes": 1440,
                              "checks": stats["source_qc_cells"], "bytes": stats["bytes"]}), flush=True)
    finally:
        halt.set()
        thread.join(timeout=2)
    aggregate = {
        "status": "COMPLETE_PARTIAL_COMBINED_MINUTE_MART_INDEPENDENT_QC_PASS_NOT_RAW_REPLACEMENT",
        "source_symbol": SYMBOL, "start": FIRST.isoformat(), "days": DAYS,
        "utc_minutes": DAYS*1440, "native_book_messages": sum(x["book_messages"] for x in daily),
        "raw_compressed_book_and_trade_input_bytes": sum(x["raw_input_bytes"] for x in daily),
        "saved_gzip_bytes": sum(x["bytes"] for x in daily),
        "independent_book_qc_cells": sum(x["source_qc_cells"]["book"] for x in daily),
        "independent_visible_decrease_qc_cells": sum(x["source_qc_cells"]["decrease"] for x in daily),
        "prior_independent_trade_qc_cells": sum(x["source_qc_cells"]["trade_prior"] for x in daily),
        "independent_total_mismatches": 0,
        "elapsed_s": round(time.monotonic()-started, 2),
        "daily": daily,
        "source_exact_reacquisition_verified": "TRXUSDT_20260518_20260524_REMOTE_LOCAL_SOURCE_SHA_PASS",
        "native_book_event_order_retained": False,
        "native_trade_tick_order_retained": False,
        "native_book_continuity_verified": False,
        "historical_receiver_availability_verified": False,
        "R8C_233_FEATURE_PARITY_VERIFIED": False,
        "source_market_instrument_id_bound": False,
        "raw_delete_allowed": False,
        "physical_reclaimed_bytes": 0,
    }
    write_once(target / "result.json", encoded(aggregate))
    write_once(target / "final_manifest.json", encoded({
        "status": aggregate["status"], "result_sha256": digest(target / "result.json"),
        "source_script_sha256": digest(snapshot / "historical_combined_minute_mart_v1.py"),
        "raw_delete_allowed": False,
    }))
    print(json.dumps({"phase": "COMPLETED", "result": str(target / "result.json"),
                      "minutes": aggregate["utc_minutes"],
                      "qc_book": aggregate["independent_book_qc_cells"],
                      "qc_decrease": aggregate["independent_visible_decrease_qc_cells"],
                      "output_bytes": aggregate["saved_gzip_bytes"], "raw_deleted": 0}), flush=True)

if __name__ == "__main__":
    main()
