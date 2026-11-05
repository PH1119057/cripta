#!/usr/bin/env python3
"""Research-only cold historical trade-minute mart from Bybit LINEAR public trades.

This is a PARTIAL trade-only input layer, NOT analytics.market_minutes,
NOT native exchange closed candles, NOT a native tick replay replacement.
Any downstream physical MarketSource/InstrumentIdentity binding requires
a separate exact verified identity contract. No raw purge is authorized.

Runs at most seven contiguous UTC days and writes immutable result bundles
ONLY under /data/cripta/research/runs as Unix actor 'cripta'.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
import pathlib
import pwd
import shutil
import sys
import time
from decimal import Decimal, localcontext

VERSION = "historical-trade-minute-mart-v1"
RAW_ROOT = pathlib.Path("/data/cripta/datasets/raw/20260518_20260816")
RUN_ROOT = pathlib.Path("/data/cripta/research/runs")
RESEARCH_SYMBOLS = (
    "AAVEUSDT", "ADAUSDT", "APTUSDT", "ARBUSDT", "AVAXUSDT",
    "BCHUSDT", "BNBUSDT", "BTCUSDT", "DOTUSDT", "ETHUSDT",
    "HBARUSDT", "INJUSDT", "LINKUSDT", "LTCUSDT", "OPUSDT",
    "SOLUSDT", "SUIUSDT", "TRXUSDT", "UNIUSDT", "XRPUSDT",
)
REQUIRED = frozenset((
    "timestamp", "symbol", "side", "size", "price",
    "foreignNotional", "trdMatchID", "tickDirection",
))
Z = Decimal(0)
DAY = Decimal(86400)
SIXTY = Decimal(60)
SAFETY_FREE_BYTES = 8 * 1024**3
MAX_ARCHIVE_BYTES = 300 * 1024**2


def hash_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as inp:
        for chunk in iter(lambda: inp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n").encode("utf-8")


def write_once(path: pathlib.Path, content: bytes) -> None:
    pending = path.with_name(path.name + ".part")
    if path.exists() or pending.exists():
        raise FileExistsError(str(path))
    with pending.open("xb") as out:
        out.write(content)
        out.flush()
        os.fsync(out.fileno())
    pending.rename(path)


def parse_number(text: str, name: str, positive: bool = False) -> Decimal:
    try:
        value = Decimal(text)
    except Exception as exc:
        raise ValueError("NON_NUMERIC_" + name) from exc
    if not value.is_finite() or value < 0 or (positive and value == 0):
        raise ValueError("INVALID_" + name)
    return value


def start_epoch(day: dt.date) -> Decimal:
    return Decimal(
        int(dt.datetime.combine(day, dt.time(), dt.UTC).timestamp())
    )


def empty_bucket() -> dict:
    return {
        "count": 0, "buy_count": 0, "sell_count": 0,
        "base": Z, "buy_base": Z, "sell_base": Z,
        "quote": Z, "buy_quote": Z, "sell_quote": Z,
        "vwap_num": Z, "open": None, "high": None,
        "low": None, "close": None,
        "first_ts": None, "last_ts": None,
        "first_ts_count": 0, "last_ts_count": 0,
    }


def aggregate(rows, symbol: str, day: dt.date) -> tuple[list[dict], dict]:
    """Independent of filesystem; consume native records in archive order."""
    buckets = [empty_bucket() for _ in range(1440)]
    epoch = start_epoch(day)
    previous = None
    latest_ids = set()
    rows_seen = 0
    notional_variances = 0
    for row in rows:
        if not REQUIRED.issubset(row):
            raise ValueError("SOURCE_FIELDS_MISSING")
        if row["symbol"] != symbol or row["side"] not in ("Buy", "Sell"):
            raise ValueError("WRONG_SOURCE_IDENTITY")
        if not row["trdMatchID"] or not row["tickDirection"]:
            raise ValueError("MISSING_NATIVE_TRADE_IDENTITY")
        ts = parse_number(row["timestamp"], "timestamp")
        if not epoch <= ts < epoch + DAY:
            raise ValueError("OFF_DAY_NATIVE_TRADE")
        if previous is not None and ts < previous:
            raise ValueError("NATIVE_TIMESTAMP_OUT_OF_ORDER")
        if previous != ts:
            latest_ids.clear()
        if row["trdMatchID"] in latest_ids:
            raise ValueError("DUPLICATE_ID_AT_SAME_TIMESTAMP")
        latest_ids.add(row["trdMatchID"])
        previous = ts
        idx = int((ts - epoch) // SIXTY)
        px = parse_number(row["price"], "price", True)
        size = parse_number(row["size"], "size")
        notion = parse_number(row["foreignNotional"], "notional")
        if abs(px * size - notion) > max(Decimal("0.00000001"), notion * Decimal("0.000001")):
            notional_variances += 1
        b = buckets[idx]
        b["count"] += 1
        b["base"] += size
        b["quote"] += notion
        side = "buy" if row["side"] == "Buy" else "sell"
        b[side + "_count"] += 1
        b[side + "_base"] += size
        b[side + "_quote"] += notion
        b["vwap_num"] += px * size
        if b["open"] is None:
            b["open"] = px
            b["first_ts"] = ts
            b["first_ts_count"] = 1
        elif ts == b["first_ts"]:
            b["first_ts_count"] += 1
        if b["last_ts"] == ts:
            b["last_ts_count"] += 1
        else:
            b["last_ts_count"] = 1
        b["last_ts"] = ts
        b["close"] = px
        b["high"] = px if b["high"] is None else max(px, b["high"])
        b["low"] = px if b["low"] is None else min(px, b["low"])
        rows_seen += 1
    if rows_seen == 0:
        raise ValueError("EMPTY_DAY_ARCHIVE_NOT_REPLACEMENT_EVIDENCE")
    output = []
    cvd = Z
    for i, b in enumerate(buckets):
        at = dt.datetime.combine(day, dt.time(), dt.UTC) + dt.timedelta(minutes=i)
        cvd += b["buy_quote"] - b["sell_quote"]
        price_quality = (
            "NO_RECORDED_TRADES" if b["count"] == 0 else
            "FILE_ORDER_TIE_UNVERIFIED" if b["first_ts_count"] > 1 or b["last_ts_count"] > 1 else
            "ARCHIVE_FILE_ORDER_ONLY"
        )
        output.append({
            "schema_version": VERSION,
            "market_source_id": None,
            "venue_id": None,
            "instrument_id": None,
            "identity_binding": "UNBOUND_EXTERNAL_BYBIT_RESEARCH_SOURCE",
            "external_symbol": symbol,
            "product_category": "LINEAR",
            "source_kind": "BYBIT_PUBLIC_TRADES_ARCHIVE",
            "observed_minute_utc": at.isoformat(),
            "derived_available_after_utc": (at + dt.timedelta(minutes=1)).isoformat(),
            "original_receiver_available_after": None,
            "native_closed_candle": False,
            "sample_count": b["count"],
            "buy_count": b["buy_count"],
            "sell_count": b["sell_count"],
            "quantity_base": str(b["base"]),
            "buy_quantity_base": str(b["buy_base"]),
            "sell_quantity_base": str(b["sell_base"]),
            "notional_quote_usdt": str(b["quote"]),
            "buy_notional_quote_usdt": str(b["buy_quote"]),
            "sell_notional_quote_usdt": str(b["sell_quote"]),
            "delta_quote_usdt": str(b["buy_quote"] - b["sell_quote"]),
            "cvd_from_utc_day_start_quote_usdt": str(cvd),
            "file_order_open": str(b["open"]) if b["open"] is not None else None,
            "native_trades_high": str(b["high"]) if b["high"] is not None else None,
            "native_trades_low": str(b["low"]) if b["low"] is not None else None,
            "file_order_close": str(b["close"]) if b["close"] is not None else None,
            "vwap_from_day_minute_base_weighted": (
                str(b["vwap_num"] / b["base"]) if b["base"] > 0 else None
            ),
            "first_source_timestamp": str(b["first_ts"]) if b["first_ts"] is not None else None,
            "last_source_timestamp": str(b["last_ts"]) if b["last_ts"] is not None else None,
            "price_order_quality": price_quality,
            "source_completeness": "GZIP_STREAM_READ_ALL_SOURCE_EVENTS_COMPLETENESS_UNKNOWN",
            "orderbook_quality": "NO_DATA",
            "oi_quality": "NO_DATA",
            "liquidations_quality": "NO_DATA",
        })
    return output, {
        "source_trade_rows": rows_seen,
        "recorded_trade_minutes": sum(b["count"] > 0 for b in buckets),
        "size_price_quote_notional_variances_over_1ppm": notional_variances,
        "first_last_order_tie_minutes": sum(
            b["count"] and (b["first_ts_count"] > 1 or b["last_ts_count"] > 1)
            for b in buckets
        ),
        "minute_rows": len(output),
    }


def independent_check(path: pathlib.Path, symbol: str, day: dt.date, primary: list[dict]) -> dict:
    """Separate positional CSV reader; verify per-minute raw counts/volumes/prices."""
    epoch = start_epoch(day)
    bucket = [[0, Z, Z, Z, None, None] for _ in range(1440)]
    with gzip.open(path, "rt", newline="", encoding="utf-8") as stream:
        reader = csv.reader(stream)
        headers = next(reader)
        if len(headers) != len(set(headers)) or not REQUIRED.issubset(set(headers)):
            raise ValueError("INDEPENDENT_SOURCE_HEADER_INVALID")
        idx = {name: headers.index(name) for name in REQUIRED}
        previous = None
        rows_seen = 0
        for record in reader:
            if len(record) != len(headers):
                raise ValueError("INDEPENDENT_CSV_RECORD_WIDTH")
            if record[idx["symbol"]] != symbol:
                raise ValueError("INDEPENDENT_WRONG_SYMBOL")
            ts = Decimal(record[idx["timestamp"]])
            if not ts.is_finite() or not epoch <= ts < epoch + DAY or (previous is not None and ts < previous):
                raise ValueError("INDEPENDENT_NATIVE_TIMESTAMP_INVALID")
            previous = ts
            minute = int((ts - epoch) // SIXTY)
            b = bucket[minute]
            price = Decimal(record[idx["price"]])
            amount = Decimal(record[idx["foreignNotional"]])
            b[0] += 1
            if record[idx["side"]] == "Buy":
                b[1] += amount
            elif record[idx["side"]] == "Sell":
                b[2] += amount
            else:
                raise ValueError("INDEPENDENT_INVALID_SIDE")
            b[3] += Decimal(record[idx["size"]])
            b[4] = price if b[4] is None else min(price, b[4])
            b[5] = price if b[5] is None else max(price, b[5])
            rows_seen += 1
    comparisons = 0
    for i, (b, p) in enumerate(zip(bucket, primary, strict=True)):
        expected = (
            b[0], str(b[1]), str(b[2]), str(b[3]),
            str(b[4]) if b[4] is not None else None,
            str(b[5]) if b[5] is not None else None,
        )
        got = (
            p["sample_count"], p["buy_notional_quote_usdt"],
            p["sell_notional_quote_usdt"], p["quantity_base"],
            p["native_trades_low"], p["native_trades_high"],
        )
        for j, (x, y) in enumerate(zip(expected, got, strict=True)):
            if j > 0 and x is not None and y is not None:
                equal = Decimal(x) == Decimal(y)
            else:
                equal = x == y
            if not equal:
                raise ValueError(f"INDEPENDENT_MINUTE_MISMATCH_{i}_{j}")
            comparisons += 1
    if sum(x["sample_count"] for x in primary) != rows_seen:
        raise ValueError("TOTAL_NATIVE_ROWS_MISMATCH")
    return {"independent_minute_cells": comparisons, "mismatches": 0, "native_rows": rows_seen}


def one_day(symbol: str, day: dt.date, outdir: pathlib.Path) -> dict:
    path = RAW_ROOT / symbol / "public_trades" / f"{symbol}{day.isoformat()}.csv.gz"
    if not path.is_file() or path.is_symlink() or not os.access(path, os.R_OK):
        raise FileNotFoundError(str(path))
    if not 0 < path.stat().st_size <= MAX_ARCHIVE_BYTES:
        raise ValueError("SOURCE_ARCHIVE_SIZE_OUT_OF_BOUNDS")
    source_sha = hash_file(path)
    with localcontext() as c:
        c.prec = 48
        with gzip.open(path, "rt", newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or not REQUIRED.issubset(set(reader.fieldnames)):
                raise ValueError("SOURCE_HEADER_INVALID")
            rows, base = aggregate(reader, symbol, day)
        check = independent_check(path, symbol, day, rows)
    if hash_file(path) != source_sha:
        raise ValueError("SOURCE_CHANGED_DURING_COMPUTE")
    content = "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows).encode("utf-8")
    encoded = gzip.compress(content, compresslevel=6, mtime=0)
    name = f"{symbol}_{day.isoformat()}_trade_minutes.jsonl.gz"
    write_once(outdir / name, encoded)
    with gzip.open(outdir / name, "rt", encoding="utf-8") as inp:
        reconstructed = [json.loads(line) for line in inp]
    if len(reconstructed) != 1440 or sum(x["sample_count"] for x in reconstructed) != base["source_trade_rows"]:
        raise ValueError("PERSISTED_MART_READBACK_MISMATCH")
    gzsha = hash_file(outdir / name)
    if gzsha != hashlib.sha256(encoded).hexdigest():
        raise ValueError("PERSISTED_GZIP_HASH_MISMATCH")
    return {
        "date_utc": day.isoformat(), "source_file": str(path),
        "source_archive_bytes": path.stat().st_size, "source_sha256": source_sha,
        "output_filename": name, "output_bytes": len(encoded), "output_sha256": gzsha,
        "primary_stats": base, "independent_qc": check, "readback": "PASS",
        "raw_purge_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", choices=RESEARCH_SYMBOLS, required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD UTC")
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    start = dt.date.fromisoformat(args.start)
    if not 1 <= args.days <= 7:
        raise ValueError("ONLY_BOUNDED_1_TO_7_DAYS")
    if start < dt.date(2026, 5, 18) or start + dt.timedelta(days=args.days) > dt.date(2026, 8, 16):
        raise ValueError("OUTSIDE_HISTORICAL_ARCHIVE")
    exact_id = f"historical_trade_minute_mart_v1_{args.symbol}_{start:%Y%m%d}_{args.days}d_20261105"
    if args.run_id != exact_id:
        raise ValueError("EXACT_IMMUTABLE_RUN_ID_REQUIRED")
    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("RESEARCH_WRITER_ACTOR_MUST_BE_CRIPTA")
    if not os.access(RUN_ROOT, os.W_OK | os.X_OK):
        raise PermissionError("RESEARCH_RUNS_PARENT_DENIED")
    destination = RUN_ROOT / exact_id
    if destination.exists() or shutil.disk_usage(RUN_ROOT).free < SAFETY_FREE_BYTES:
        raise RuntimeError("TARGET_ALREADY_EXISTS_OR_DISK_BELOW_8GIB_RESERVE")
    days = [start + dt.timedelta(days=i) for i in range(args.days)]
    for date in days:
        file = RAW_ROOT / args.symbol / "public_trades" / f"{args.symbol}{date.isoformat()}.csv.gz"
        if not file.is_file() or file.is_symlink() or not os.access(file, os.R_OK):
            raise FileNotFoundError(str(file))
        if not 0 < file.stat().st_size <= MAX_ARCHIVE_BYTES:
            raise ValueError("SOURCE_ARCHIVE_SIZE_OUT_OF_BOUNDS")
    source_text = globals().get("__snapshot_source__")
    if source_text is None:
        source_text = pathlib.Path(__file__).read_text(encoding="utf-8")
    if not isinstance(source_text, str):
        raise ValueError("SOURCE_SNAPSHOT_NOT_REPRODUCIBLE")
    started = time.monotonic()
    os.umask(0o007)
    destination.mkdir(mode=0o2770)
    snapshots = destination / "source_snapshot"
    snapshots.mkdir(mode=0o2770)
    write_once(snapshots / "historical_trade_minute_mart_v1.py", source_text.encode("utf-8"))
    source_checksum = hash_file(snapshots / "historical_trade_minute_mart_v1.py")
    write_once(destination / "source_sha256.txt", (source_checksum + "  source_snapshot/historical_trade_minute_mart_v1.py\n").encode())
    write_once(destination / "command.txt", json_bytes(vars(args)))
    write_once(destination / "environment.txt", json_bytes({
        "uid": os.geteuid(), "actor": "cripta", "python": sys.version,
        "run_pid": os.getpid(), "data_disk_only": True,
    }))
    write_once(destination / "run_manifest.json", json_bytes({
        "status": "RUNNING", "algorithm": VERSION, "date_start": args.start,
        "days": args.days, "symbol": args.symbol,
        "source_sha256": source_checksum, "raw_purge_allowed": False,
        "source_binding": "NOT_CANONICAL_INSTRUMENT_IDENTITY_BOUND",
    }))
    results = []
    for i, date in enumerate(days):
        print(json.dumps({"stage": "START_DAY", "day": str(date), "i": i + 1, "total": len(days)}), flush=True)
        if shutil.disk_usage(RUN_ROOT).free < SAFETY_FREE_BYTES:
            raise RuntimeError("DISK_RESERVE_FELL_BELOW_8GIB")
        result = one_day(args.symbol, date, destination)
        write_once(destination / f"{date.isoformat()}_qc.json", json_bytes(result))
        results.append(result)
        print(json.dumps({"stage": "DAY_QC_PASS", "date": str(date),
                          "output_bytes": result["output_bytes"],
                          "qc_cells": result["independent_qc"]["independent_minute_cells"]}), flush=True)
    summary = {
        "status": "TRADE_ONLY_RESEARCH_MART_QC_PASS_NOT_FULL_MARKET_MART",
        "symbol": args.symbol, "days": len(results), "algorithm": VERSION,
        "source_script_sha256": source_checksum,
        "rows": len(results) * 1440,
        "total_trade_rows": sum(r["primary_stats"]["source_trade_rows"] for r in results),
        "source_archive_bytes": sum(r["source_archive_bytes"] for r in results),
        "mart_bytes": sum(r["output_bytes"] for r in results),
        "independent_qc_cells": sum(r["independent_qc"]["independent_minute_cells"] for r in results),
        "independent_mismatches": 0,
        "per_day": results, "elapsed_seconds": round(time.monotonic() - started, 2),
        "source_instrument_identity_bound": False,
        "native_exchange_closed_candles": False,
        "native_trade_sequence_preserved": False,
        "orderbook_oi_liquidations_covered": False,
        "raw_purge_allowed": False,
    }
    write_once(destination / "result.json", json_bytes(summary))
    write_once(destination / "final_manifest.json", json_bytes({
        "status": summary["status"],
        "result_sha256": hash_file(destination / "result.json"),
        "source_sha256": source_checksum, "raw_purge_allowed": False,
    }))
    print(json.dumps({"stage": "COMPLETE", "run": exact_id,
                      "minute_rows": summary["rows"],
                      "qc_cells": summary["independent_qc_cells"],
                      "raw_deleted_bytes": 0}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
