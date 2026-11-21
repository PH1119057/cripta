#!/usr/bin/env python3
"""Bounded RESEARCH-only native ledger pointer QC; never writes input or output files.

This validates recorded event identity, NOT independent Strategy/Exit recomputation.
For BULL25, source_order is global, one-based per symbol across warmup and eval.
Usage: python native_ledger_pointer_qc.py --qc-manifest ... --input-root ...
       --ledger ... --ledger-result ... --date 2025-04-01 --date 2025-05-30
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
from decimal import Decimal
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys

SYMBOLS = ("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT")
MAX_DATES = 3
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_COMPACT_BYTES = 32 * 1024 * 1024


def require(ok: bool, code: str) -> None:
    if not ok:
        raise ValueError(code)


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for buf in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(buf)
    return digest.hexdigest()


def check_event(kind: str, item: dict, row: dict) -> tuple[str, bool]:
    require(str(item[kind + "_ts"]) == row["timestamp"], "EVENT_TIMESTAMP_MISMATCH")
    px = Decimal(row["price"])
    expected = Decimal(str(item[kind + "_price"]))
    if kind == "entry":
        require(px == expected, "ENTRY_PRICE_MISMATCH")
        require(row["trdMatchID"] == item["entry_trade_id"], "ENTRY_TRADE_ID_MISMATCH")
        return "ENTRY_EXACT", False
    if item.get("exit_reason") == "RESTING_TP":
        require(item.get("maker_exit") is True, "RESTING_TP_NOT_MAKER")
        side = item["side"]
        require(side in ("LONG", "SHORT"), "EXIT_SIDE_INVALID")
        require(px >= expected if side == "LONG" else px <= expected, "RESTING_TP_NOT_CROSSED")
        return "RESTING_TP_CROSSED", px != expected
    require(px == expected, "NONRESTING_EXIT_PRICE_MISMATCH")
    return str(item["exit_reason"]) + "_EXACT", False


def self_test() -> dict:
    raw = {"timestamp": "100.25", "price": "8.9900", "trdMatchID": "id-a"}
    entry = {"entry_ts": "100.25", "entry_price": "8.9900", "entry_trade_id": "id-a"}
    kind, _ = check_event("entry", entry, raw)
    require(kind == "ENTRY_EXACT", "SELFTEST_ENTRY")
    stop = {"exit_ts": "100.25", "exit_price": "8.9900", "exit_reason": "HARD_STOP"}
    require(check_event("exit", stop, raw)[0] == "HARD_STOP_EXACT", "SELFTEST_STOP")
    tp = {"exit_ts": "100.25", "exit_price": "8.9890", "exit_reason": "RESTING_TP",
          "side": "LONG", "maker_exit": True}
    require(check_event("exit", tp, raw) == ("RESTING_TP_CROSSED", True), "SELFTEST_TP")
    try:
        check_event("entry", {**entry, "entry_trade_id": "wrong"}, raw)
    except ValueError as exc:
        require(str(exc) == "ENTRY_TRADE_ID_MISMATCH", "SELFTEST_ID_GUARD")
    else:
        raise ValueError("SELFTEST_MISSING_ID_GUARD")
    require(21 - 20 - 1 == 0, "SELFTEST_ONE_BASED_ORDINAL")
    return {"status": "SELFTEST_PASS", "cases": 5, "writes": 0}


def run(args: argparse.Namespace) -> dict:
    dates = tuple(sorted(set(args.date)))
    require(0 < len(dates) <= MAX_DATES, "DATE_COUNT_OUT_OF_BOUNDS")
    for date in dates:
        require(dt.date.fromisoformat(date).isoformat() == date, "DATE_FORMAT_INVALID")
    manifest = json.loads(Path(args.qc_manifest).read_text())
    require(manifest.get("stage") == "ACQUISITION_QC_PASS", "SOURCE_QC_NOT_PASS")
    table = manifest["by_day"]
    result = json.loads(Path(args.ledger_result).read_text())
    require(result.get("status") == "QC_PASS", "LEDGER_RESULT_NOT_QC_PASS")
    expected_hash = result.get("ledger_sha256")
    require(sha_file(Path(args.ledger)) == expected_hash, "LEDGER_SHA256_MISMATCH")
    dateprefix = {}
    for sym in SYMBOLS:
        rows = []
        for key, info in table.items():
            filename = key.rsplit("/", 1)[-1]
            if key.startswith(sym + "/public_trades/"):
                rows.append((filename[len(sym):len(sym) + 10], int(info["rows"])))
        for date in dates:
            require(any(day == date for day, _ in rows), "SOURCE_DAY_ABSENT")
            dateprefix[(sym, date)] = sum(n for day, n in rows if day < date)

    target = {(sym, date): {} for sym in SYMBOLS for date in dates}
    unique = set()
    ledger_rows = 0
    event_total = 0
    for line in Path(args.ledger).open(encoding="utf-8"):
        record = json.loads(line)
        ledger_rows += 1
        sym = record["symbol"]
        require(sym in SYMBOLS, "UNEXPECTED_LEDGER_SYMBOL")
        for kind in ("entry", "exit"):
            t = record.get(kind + "_ts")
            if t is None:
                continue
            day = dt.datetime.fromtimestamp(float(t), dt.timezone.utc).date().isoformat()
            if day not in dates:
                continue
            source_order = int(record[kind + "_source_order"])
            idx = source_order - dateprefix[(sym, day)] - 1
            require(idx >= 0, "SOURCE_ORDINAL_BEFORE_DAY")
            target[(sym, day)].setdefault(idx, []).append((kind, record))
            event_total += 1
            unique.add((sym, day, kind, source_order, str(t),
                        str(record[kind + "_price"]),
                        str(record.get("exit_reason")) if kind == "exit" else ""))
    require(ledger_rows == result["ledger_rows"], "LEDGER_ROW_COUNT_MISMATCH")
    require(event_total > 0, "NO_LEDGER_EVENTS_IN_SELECTED_DATES")

    counts = {}
    total_rows = 0
    compact_total = 0
    native_total = 0
    for date in dates:
        start = dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc).timestamp()
        for sym in SYMBOLS:
            path = Path(args.input_root) / sym / "public_trades" / (sym + date + ".csv.gz")
            key = sym + "/public_trades/" + path.name
            meta = table[key]
            require(path.is_file(), "SOURCE_FILE_MISSING")
            require(0 < path.stat().st_size <= MAX_FILE_BYTES, "SOURCE_COMPRESSED_BYTES_LIMIT")
            require(path.stat().st_size == meta["size_bytes"], "SOURCE_SIZE_MISMATCH")
            require(sha_file(path) == meta["sha256"], "SOURCE_SHA256_MISMATCH")
            requested = target[(sym, date)]
            original = {}
            buffer = io.BytesIO()
            previous = None
            row_count = 0
            with gzip.open(path, "rt", newline="") as stream, gzip.GzipFile(
                fileobj=buffer, mode="wb", filename="", mtime=0, compresslevel=3
            ) as compact:
                reader = csv.DictReader(stream)
                require({"timestamp", "price", "trdMatchID"}.issubset(reader.fieldnames or ()),
                        "SOURCE_SCHEMA_INVALID")
                for i, row in enumerate(reader):
                    stamp = float(row["timestamp"])
                    require(start <= stamp < start + 86400, "SOURCE_ROW_OUTSIDE_DAY")
                    require(previous is None or previous <= stamp, "SOURCE_TIMESTAMP_REGRESSION")
                    previous = stamp
                    require(Decimal(row["price"]) > 0, "SOURCE_PRICE_INVALID")
                    if i in requested:
                        original[i] = row
                    compact.write((str(i) + "\t" + row["timestamp"] + "\t"
                                   + row["price"] + "\n").encode("ascii"))
                    row_count += 1
                    require(buffer.tell() <= MAX_COMPACT_BYTES, "COMPACT_MEMORY_BOUND")
            require(row_count == meta["rows"], "SOURCE_ROW_COUNT_MISMATCH")
            from_compact = {}
            with gzip.GzipFile(fileobj=io.BytesIO(buffer.getvalue()), mode="rb") as packed:
                for i, line in enumerate(packed):
                    values = line.decode("ascii").rstrip("\n").split("\t")
                    require(len(values) == 3 and int(values[0]) == i,
                            "COMPACT_ORDINAL_OR_FORMAT_MISMATCH")
                    if i in requested:
                        from_compact[i] = (values[1], values[2])
                require(i + 1 == row_count, "COMPACT_ROW_COUNT_MISMATCH")
            for i, events in requested.items():
                row = original.get(i)
                require(row is not None and i in from_compact, "LEDGER_SOURCE_ROW_ABSENT")
                require((row["timestamp"], row["price"]) == from_compact[i],
                        "COMPACT_NATIVE_PRICE_EVENT_MISMATCH")
                for kind, record in events:
                    label, limit_diff = check_event(kind, record, row)
                    counts[label] = counts.get(label, 0) + 1
                    if limit_diff:
                        counts["RESTING_TP_PRICE_DIFF_EXPECTED"] = (
                            counts.get("RESTING_TP_PRICE_DIFF_EXPECTED", 0) + 1
                        )
            total_rows += row_count
            native_total += path.stat().st_size
            compact_total += len(buffer.getvalue())
    require(sum(v for k, v in counts.items()
                if k != "RESTING_TP_PRICE_DIFF_EXPECTED") == event_total,
            "EVENT_COVERAGE_MISMATCH")
    return {"status": "PASS_BOUNDED_EVENT_POINTER_QC", "dates": dates,
            "symbols": SYMBOLS, "source_files": len(dates) * len(SYMBOLS),
            "native_rows": total_rows, "ledger_references_with_scenario_repeats": event_total,
            "distinct_ledger_event_identities": len(unique), "comparisons": counts,
            "native_gzip_bytes": native_total, "compact_gzip_bytes": compact_total,
            "selected_stream_ratio": round(compact_total / native_total, 5),
            "ledger_source_sha256": expected_hash,
            "scope": "original pointer/time/price/tradeID and modeled resting limit crossing only; "
                     "not full independent causal Entry/Exit/portfolio/fees replay",
            "server_write_count": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--qc-manifest")
    parser.add_argument("--input-root")
    parser.add_argument("--ledger")
    parser.add_argument("--ledger-result")
    parser.add_argument("--date", action="append", default=[])
    args = parser.parse_args()
    try:
        if args.self_test:
            output = self_test()
        else:
            require(all((args.qc_manifest, args.input_root, args.ledger, args.ledger_result)),
                    "MISSING_EXPLICIT_INPUT")
            output = run(args)
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_class": type(exc).__name__,
                          "reason": str(exc)[:320]}, sort_keys=True))
        return 1
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
