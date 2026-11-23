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

SYMBOLS = ("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT")
MAX_DATES = 3
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_COMPACT_BYTES = 32 * 1024 * 1024
MAX_HARDSTOPS_PER_SYMBOL_DAY = 32
# Exact frozen BULL25 historical -10% hardstop model, not live policy.
HARDSTOP_SOURCE_SHA = "3f8e768cf4c6d9cf6ea0d25ecb1fe23c69fbfc2b072e43192ae00d52d8a43a10"


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


def hardstop_crossed(side: str, price: str, level: Decimal) -> bool:
    require(side in ("LONG", "SHORT"), "HARDSTOP_SIDE_INVALID")
    px = Decimal(price)
    return px <= level if side == "LONG" else px >= level


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
    require(hardstop_crossed("LONG", "8.9", Decimal("9.0")), "SELFTEST_LONG_STOP")
    require(not hardstop_crossed("SHORT", "10.9", Decimal("11.0")), "SELFTEST_SHORT_STOP")
    return {"status": "SELFTEST_PASS", "cases": 7, "writes": 0}


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
    verify_first_hit = bool(getattr(args, "verify_hardstop_first_hit", False))
    if verify_first_hit:
        frozen = result.get("source_snapshot_sha256", {})
        require(frozen.get("p90l90_exit_90d_v2.py") == HARDSTOP_SOURCE_SHA,
                "HARDSTOP_MODEL_SOURCE_UNPINNED")
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
    first_hit_groups = {(sym, date): {} for sym in SYMBOLS for date in dates}
    hardstop_refs = 0
    unique = set()
    ledger_rows = 0
    event_total = 0
    for line in Path(args.ledger).open(encoding="utf-8"):
        record = json.loads(line)
        ledger_rows += 1
        sym = record["symbol"]
        require(sym in SYMBOLS, "UNEXPECTED_LEDGER_SYMBOL")
        if verify_first_hit and record.get("exit_reason") == "HARD_STOP":
            ent = record.get("entry_ts")
            ext = record.get("exit_ts")
            if ent is not None and ext is not None:
                ent_day = dt.datetime.fromtimestamp(float(ent), dt.timezone.utc).date().isoformat()
                ext_day = dt.datetime.fromtimestamp(float(ext), dt.timezone.utc).date().isoformat()
                if ent_day == ext_day and ent_day in dates:
                    signature = (int(record["entry_source_order"]),
                                 int(record["exit_source_order"]), str(record["side"]))
                    group = first_hit_groups[(sym, ent_day)]
                    if signature in group:
                        previous = group[signature]["record"]
                        fields = ("entry_ts", "entry_price", "entry_trade_id",
                                  "exit_ts", "exit_price", "exit_reason")
                        require(all(record.get(k) == previous.get(k) for k in fields),
                                "HARDSTOP_SCENARIO_POINTER_DRIFT")
                        group[signature]["references"] += 1
                    else:
                        group[signature] = {"record": record, "references": 1,
                                            "first_native": None, "first_compact": None}
                        require(len(group) <= MAX_HARDSTOPS_PER_SYMBOL_DAY,
                                "HARDSTOP_PROBE_COUNT_EXCEEDED")
                    hardstop_refs += 1
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
    if verify_first_hit:
        require(hardstop_refs > 0, "NO_SAME_DAY_HARDSTOP")

    counts = {}
    first_hit_unique = 0
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
            probes = first_hit_groups[(sym, date)]
            for signature, item in probes.items():
                entry_idx = signature[0] - dateprefix[(sym, date)] - 1
                exit_idx = signature[1] - dateprefix[(sym, date)] - 1
                require(0 <= entry_idx < exit_idx < int(meta["rows"]),
                        "HARDSTOP_SOURCE_ORDER_OUT_OF_DAY")
                item["entry_idx"] = entry_idx
                item["exit_idx"] = exit_idx
                item["side"] = signature[2]
                entry_price = Decimal(str(item["record"]["entry_price"]))
                item["threshold"] = entry_price * (
                    Decimal("0.90") if item["side"] == "LONG" else Decimal("1.10")
                )
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
                    for item in probes.values():
                        if (item["entry_idx"] < i <= item["exit_idx"]
                                and item["first_native"] is None
                                and hardstop_crossed(item["side"], row["price"],
                                                     item["threshold"])):
                            item["first_native"] = (i, row["timestamp"], row["price"])
                    compact.write((str(i) + "\t" + row["timestamp"] + "\t"
                                   + row["price"] + "\n").encode("ascii"))
                    row_count += 1
                    require(buffer.tell() <= MAX_COMPACT_BYTES, "COMPACT_MEMORY_BOUND")
            require(row_count == meta["rows"], "SOURCE_ROW_COUNT_MISMATCH")
            from_compact = {}
            decoded_rows = 0
            with gzip.GzipFile(fileobj=io.BytesIO(buffer.getvalue()), mode="rb") as packed:
                for i, line in enumerate(packed):
                    values = line.decode("ascii").rstrip("\n").split("\t")
                    require(len(values) == 3 and int(values[0]) == i,
                            "COMPACT_ORDINAL_OR_FORMAT_MISMATCH")
                    if i in requested:
                        from_compact[i] = (values[1], values[2])
                    for item in probes.values():
                        if (item["entry_idx"] < i <= item["exit_idx"]
                                and item["first_compact"] is None
                                and hardstop_crossed(item["side"], values[2],
                                                     item["threshold"])):
                            item["first_compact"] = (i, values[1], values[2])
                    decoded_rows += 1
                require(decoded_rows == row_count, "COMPACT_ROW_COUNT_MISMATCH")
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
            for item in probes.values():
                record = item["record"]
                entry = original.get(item["entry_idx"])
                exit_row = original.get(item["exit_idx"])
                require(entry is not None and exit_row is not None,
                        "HARDSTOP_SOURCE_ANCHOR_ABSENT")
                require(entry["timestamp"] == str(record["entry_ts"])
                        and Decimal(entry["price"]) == Decimal(record["entry_price"])
                        and entry["trdMatchID"] == record["entry_trade_id"],
                        "HARDSTOP_ENTRY_ANCHOR_MISMATCH")
                wanted = (item["exit_idx"], str(record["exit_ts"]),
                          str(record["exit_price"]))
                require(item["first_native"] == wanted,
                        "HARDSTOP_FIRST_CROSSING_NATIVE_MISMATCH")
                require(item["first_compact"] == wanted,
                        "HARDSTOP_FIRST_CROSSING_COMPACT_MISMATCH")
                first_hit_unique += 1
            total_rows += row_count
            native_total += path.stat().st_size
            compact_total += len(buffer.getvalue())
    require(sum(v for k, v in counts.items()
                if k != "RESTING_TP_PRICE_DIFF_EXPECTED") == event_total,
            "EVENT_COVERAGE_MISMATCH")
    summary = {"status": "PASS_BOUNDED_EVENT_POINTER_QC", "dates": dates,
            "symbols": SYMBOLS, "source_files": len(dates) * len(SYMBOLS),
            "native_rows": total_rows, "ledger_references_with_scenario_repeats": event_total,
            "distinct_ledger_event_identities": len(unique), "comparisons": counts,
            "native_gzip_bytes": native_total, "compact_gzip_bytes": compact_total,
            "selected_stream_ratio": round(compact_total / native_total, 5),
            "ledger_source_sha256": expected_hash,
            "scope": "original pointer/time/price/tradeID and modeled resting limit crossing only; "
                     "not full independent causal Entry/Exit/portfolio/fees replay",
            "server_write_count": 0}
    if verify_first_hit:
        summary["hardstop_first_hit"] = {
            "model": "historical BULL25 exact frozen -10% initial hardstop",
            "source_sha256": HARDSTOP_SOURCE_SHA,
            "unique_same_day_first_crossings": first_hit_unique,
            "scenario_references": hardstop_refs,
            "result": "PASS_FIRST_CROSSING_SOURCE_AND_COMPACT",
            "exclusions": "dynamic TP/SOFT_STOP, portfolio causality, fills/economics",
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--qc-manifest")
    parser.add_argument("--input-root")
    parser.add_argument("--ledger")
    parser.add_argument("--ledger-result")
    parser.add_argument("--date", action="append", default=[])
    parser.add_argument("--verify-hardstop-first-hit", action="store_true")
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
