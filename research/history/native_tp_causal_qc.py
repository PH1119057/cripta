#!/usr/bin/env python3
"""Bounded, read-only historical CLASSIC TPOnly first-event parity pilot.

Given accepted Entry from the immutable historical research ledger, independently
walk original public Bybit trades and closed-only frozen L5-3 states to find the
first HARD_STOP / RESTING_TP / MARKETABLE_TP. Does NOT reproduce Entry selection,
portfolio competition, exchange maker fills or trading production behavior.
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
FROZEN_EXIT_SHA = "3f8e768cf4c6d9cf6ea0d25ecb1fe23c69fbfc2b072e43192ae00d52d8a43a10"
FROZEN_TP_SHA = "b25210dbd9977fe85f568a52867b3673aea65c17308e19612cb216efae68164e"
FROZEN_PORTFOLIO_SHA = "ebbf6d79bca9d9353b717833becd64eb7f36c88351ec1e77fe41ff152d3f4ee1"
MAX_CASES = 4
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_FROZEN_BYTES = 16 * 1024 * 1024
MAX_INTERVAL_ROWS = 30000
MAX_INTERVAL_COMPACT_BYTES = 4 * 1024 * 1024
MAX_HOLD_SECONDS = 6 * 3600


def need(ok: bool, reason: str) -> None:
    if not ok:
        raise ValueError(reason)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as inp:
        for block in iter(lambda: inp.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def hits_stop(side: str, price: Decimal, level: Decimal) -> bool:
    return price <= level if side == "LONG" else price >= level


def hits_tp(side: str, price: Decimal, level: Decimal) -> bool:
    return price >= level if side == "LONG" else price <= level


def target_eligible(side: str, entry: Decimal, level: Decimal) -> bool:
    move = (level / entry - 1) * 100 if side == "LONG" else (1 - level / entry) * 100
    return move >= Decimal("0.5")


def state_target(side: str, states: dict[int, tuple[Decimal, Decimal]], bucket: int) -> Decimal:
    need(bucket in states, "CAUSAL_L53_STATE_MISSING")
    return states[bucket][1 if side == "LONG" else 0]


def first_exit(record: dict, states: dict[int, tuple[Decimal, Decimal]],
               tape: list[tuple[int, str, str]]) -> tuple[dict | None, dict]:
    """Reimplementation of only the verified historical TPOnly close path."""
    need(bool(tape), "EMPTY_NATIVE_INTERVAL")
    side = record["side"]
    need(side in ("LONG", "SHORT"), "SIDE_NOT_SUPPORTED")
    entry = Decimal(record["entry_price"])
    need(entry > 0, "INVALID_ENTRY_PRICE")
    first_seq = int(record["entry_source_order"])
    need(tape[0][0] == first_seq
         and tape[0][1] == str(record["entry_ts"])
         and Decimal(tape[0][2]) == entry, "ENTRY_TAPE_ANCHOR_MISMATCH")
    bucket = int(float(record["entry_ts"]) // 300) * 300
    initial = state_target(side, states, bucket)
    tp = initial if target_eligible(side, entry, initial) else None
    tp_live_after = first_seq
    stop = entry * (Decimal("0.90") if side == "LONG" else Decimal("1.10"))
    stats = {"target_changes": 0, "bucket_transitions": 0}
    previous_seq = first_seq
    previous_ts = float(record["entry_ts"])

    for seq, stamp, raw_price in tape[1:]:
        t = float(stamp)
        need(seq > previous_seq and t >= previous_ts, "NATIVE_ORDER_OR_TIME_REGRESSION")
        previous_seq, previous_ts = seq, t
        current_bucket = int(t // 300) * 300
        need(current_bucket >= bucket, "CAUSAL_BUCKET_REGRESSION")
        price = Decimal(raw_price)
        need(price > 0, "INVALID_NATIVE_PRICE")
        if hits_stop(side, price, stop):
            return {"reason": "HARD_STOP", "source_order": seq,
                    "timestamp": stamp, "exit_price": raw_price,
                    "crossing_print": raw_price, "maker_exit": False}, stats
        if tp is not None and seq > tp_live_after and hits_tp(side, price, tp):
            return {"reason": "RESTING_TP", "source_order": seq,
                    "timestamp": stamp, "exit_price": str(tp),
                    "crossing_print": raw_price, "maker_exit": True}, stats
        if current_bucket != bucket:
            bucket = current_bucket
            stats["bucket_transitions"] += 1
            candidate = state_target(side, states, bucket)
            if target_eligible(side, entry, candidate):
                if hits_tp(side, price, candidate):
                    return {"reason": "MARKETABLE_TP", "source_order": seq,
                            "timestamp": stamp, "exit_price": raw_price,
                            "crossing_print": raw_price, "maker_exit": False,
                            "new_closed_bucket_target": str(candidate)}, stats
                if candidate != tp:
                    stats["target_changes"] += 1
                tp = candidate
                tp_live_after = seq
    return None, stats


def self_test() -> dict:
    base = {"side": "LONG", "entry_price": "10.0",
            "entry_source_order": 1, "entry_ts": "300.25"}
    s1 = {300: (Decimal("9"), Decimal("11")),
          600: (Decimal("9"), Decimal("10.6"))}
    # Neither the future 600 state nor its TP is available at t=599.75.
    market, values = first_exit(
        base, s1, [(1, "300.25", "10.0"), (2, "599.75", "10.4"),
                   (3, "600.25", "10.7")])
    need(market is not None and market["reason"] == "MARKETABLE_TP"
         and market["source_order"] == 3, "SELFTEST_DYNAMIC_MARKETABLE")
    need(values["bucket_transitions"] == 1, "SELFTEST_CAUSAL_BUCKET")
    rested, _ = first_exit(
        base, {300: (Decimal("9"), Decimal("10.7"))},
        [(1, "300.25", "10.0"), (2, "301.25", "10.8")])
    need(rested is not None and rested["reason"] == "RESTING_TP"
         and rested["exit_price"] == "10.7", "SELFTEST_RESTING_LIMIT")
    stopped, _ = first_exit(
        base, s1, [(1, "300.25", "10.0"), (2, "301.25", "8.9")])
    need(stopped is not None and stopped["reason"] == "HARD_STOP",
         "SELFTEST_HARDSTOP_PRECEDENCE")
    short = {**base, "side": "SHORT"}
    short_states = {300: (Decimal("9.0"), Decimal("11.0")),
                    600: (Decimal("9.6"), Decimal("11.0"))}
    got, _ = first_exit(
        short, short_states,
        [(1, "300.25", "10.0"), (2, "599.75", "9.8"),
         (3, "600.25", "9.5")])
    need(got is not None and got["reason"] == "MARKETABLE_TP",
         "SELFTEST_SHORT_MARKETABLE")
    try:
        first_exit(base, {300: s1[300]},
                   [(1, "300.25", "10.0"), (2, "600.25", "10.4")])
    except ValueError as exc:
        need(str(exc) == "CAUSAL_L53_STATE_MISSING", "SELFTEST_MISSING_STATE_REASON")
    else:
        raise ValueError("SELFTEST_MISSING_CAUSAL_STATE_GATE")
    return {"status": "SELFTEST_PASS", "cases": 5, "server_writes": 0}


def parse_case(raw: str) -> tuple[str, int, int]:
    bits = raw.split(":")
    need(len(bits) == 3 and bits[0] in SYMBOLS, "CASE_SELECTOR_INVALID")
    try:
        en, ex = int(bits[1]), int(bits[2])
    except ValueError as exc:
        raise ValueError("CASE_SOURCE_ORDER_NOT_INTEGER") from exc
    need(0 < en < ex, "CASE_SOURCE_ORDER_INVALID")
    return bits[0], en, ex


def load_states(path: Path, required: set[int]) -> dict[int, tuple[Decimal, Decimal]]:
    result: dict[int, tuple[Decimal, Decimal]] = {}
    with gzip.open(path, "rt", newline="") as stream:
        reader = csv.DictReader(stream)
        need({"symbol", "state_effective_ts", "source_bar_end_ts",
              "range_low", "range_high"}.issubset(reader.fieldnames or ()),
             "FROZEN_L53_SCHEMA_MISSING")
        for row in reader:
            stamp = int(row["state_effective_ts"])
            if stamp not in required:
                continue
            need(stamp % 300 == 0
                 and stamp == int(row["source_bar_end_ts"]), "NONCAUSAL_L53_STATE")
            need(stamp not in result, "DUPLICATE_L53_STATE")
            lo, hi = Decimal(row["range_low"]), Decimal(row["range_high"])
            need(lo > 0 and hi >= lo, "INVALID_L53_RAW_RANGE")
            result[stamp] = lo, hi
    need(set(result) == required, "FROZEN_L53_BUCKET_COVERAGE_MISSING")
    return result


def run(args: argparse.Namespace) -> dict:
    cases = [parse_case(s) for s in args.case]
    need(0 < len(cases) <= MAX_CASES and len(set(cases)) == len(cases),
         "CASE_COUNT_OR_DUPLICATE_INVALID")
    meta = json.loads(Path(args.qc_manifest).read_text())
    need(meta.get("stage") == "ACQUISITION_QC_PASS", "SOURCE_QC_NOT_PASS")
    table = meta["by_day"]
    report = json.loads(Path(args.ledger_result).read_text())
    need(report.get("status") == "QC_PASS", "LEDGER_RESULT_NOT_QC_PASS")
    snapshots = report.get("source_snapshot_sha256", {})
    for filename, expected in (
        ("p90l90_tp_only_second48_v1.py", FROZEN_TP_SHA),
        ("p90l90_exit_90d_v2.py", FROZEN_EXIT_SHA),
        ("p90l90_bull2025_portfolio_v1.py", FROZEN_PORTFOLIO_SHA),
    ):
        need(snapshots.get(filename) == expected, "FROZEN_TP_MODEL_SOURCE_UNPINNED")
    need(digest(Path(args.ledger)) == report["ledger_sha256"],
         "ORIGINAL_LEDGER_SHA_MISMATCH")
    selected = {}
    n = 0
    wanted = set(cases)
    for line in Path(args.ledger).open(encoding="utf-8"):
        record = json.loads(line)
        n += 1
        key = (record["symbol"], int(record["entry_source_order"]),
               int(record["exit_source_order"]))
        if key not in wanted or record["portfolio_scenario"] != "CLASSIC_S48_OFF":
            continue
        need(key not in selected, "DUPLICATE_SELECTED_CLASSIC_LEDGER_ROW")
        selected[key] = record
    need(n == report["ledger_rows"], "LEDGER_ROWS_MISMATCH")
    need(set(selected) == wanted, "SELECTED_CLASSIC_LEDGER_MISSING")

    all_results = []
    total_gzip_bytes = total_day_rows = interval_bytes = 0
    for symbol, entry_order, exit_order in cases:
        record = selected[symbol, entry_order, exit_order]
        need(record.get("exit_reason") in
             ("HARD_STOP", "RESTING_TP", "MARKETABLE_TP"), "EXIT_REASON_UNSUPPORTED")
        need(record.get("side") in ("LONG", "SHORT"), "SIDE_NOT_SUPPORTED")
        entry_ts = float(record["entry_ts"])
        exit_ts = float(record["exit_ts"])
        entry_day = dt.datetime.fromtimestamp(entry_ts, dt.timezone.utc).date().isoformat()
        exit_day = dt.datetime.fromtimestamp(exit_ts, dt.timezone.utc).date().isoformat()
        need(entry_day == exit_day and 0 < exit_ts - entry_ts <= MAX_HOLD_SECONDS,
             "CASE_NOT_SAME_DAY_BOUNDED")
        source = Path(args.input_root) / symbol / "public_trades" / (
            symbol + entry_day + ".csv.gz")
        source_key = symbol + "/public_trades/" + source.name
        need(source_key in table and source.is_file(), "ORIGINAL_SOURCE_MISSING")
        source_meta = table[source_key]
        need(0 < source.stat().st_size <= MAX_SOURCE_BYTES, "SOURCE_FILE_SIZE_BOUND")
        need(source.stat().st_size == source_meta["size_bytes"]
             and digest(source) == source_meta["sha256"], "ORIGINAL_SOURCE_SHA_MISMATCH")
        prefix = sum(int(info["rows"]) for key, info in table.items()
                     if key.startswith(symbol + "/public_trades/")
                     and key.rsplit("/", 1)[-1][len(symbol):len(symbol) + 10] < entry_day)
        start_index, end_index = entry_order - prefix - 1, exit_order - prefix - 1
        need(0 <= start_index < end_index < int(source_meta["rows"]),
             "ONE_BASED_SOURCE_ORDER_OUT_OF_DATE")

        manifest = json.loads((Path(args.entry_manifest_root) / symbol
                               / "manifest.json").read_text())
        need(manifest["symbol"] == symbol
             and manifest.get("PREV7D_WIDTH_LOOKAHEAD") is False,
             "ENTRY_MANIFEST_PROVENANCE_INVALID")
        frozen = Path(args.frozen_root) / ("states_" + symbol + ".csv.gz")
        need(frozen.is_file() and 0 < frozen.stat().st_size <= MAX_FROZEN_BYTES,
             "FROZEN_FILE_SIZE_BOUND")
        need(digest(frozen) == manifest["frozen_l53_sha256"],
             "FROZEN_L53_SHA_MISMATCH")
        first_bucket = int(entry_ts // 300) * 300
        last_bucket = int(exit_ts // 300) * 300
        required = set(range(first_bucket, last_bucket + 300, 300))
        need(len(required) <= MAX_HOLD_SECONDS // 300 + 1,
             "CAUSAL_BUCKET_COUNT_BOUND")
        states = load_states(frozen, required)

        tape = []
        entry_row = exit_row = None
        row_count = 0
        previous_time = None
        with gzip.open(source, "rt", newline="") as stream:
            reader = csv.DictReader(stream)
            need({"timestamp", "price", "trdMatchID", "symbol"}.issubset(
                reader.fieldnames or ()), "ORIGINAL_SOURCE_SCHEMA_MISSING")
            day_start = dt.datetime.fromisoformat(entry_day).replace(
                tzinfo=dt.timezone.utc).timestamp()
            for i, row in enumerate(reader):
                stamp = float(row["timestamp"])
                need(previous_time is None or previous_time <= stamp,
                     "ORIGINAL_SOURCE_TIMESTAMP_REGRESSION")
                previous_time = stamp
                need(day_start <= stamp < day_start + 86400,
                     "ORIGINAL_SOURCE_OUTSIDE_UTC_DAY")
                need(row["symbol"] == symbol, "ORIGINAL_SOURCE_SYMBOL_DRIFT")
                if i == start_index:
                    entry_row = row
                if start_index <= i <= end_index:
                    tape.append((prefix + i + 1, row["timestamp"], row["price"]))
                    need(len(tape) <= MAX_INTERVAL_ROWS, "TAPE_INTERVAL_ROW_BOUND")
                if i == end_index:
                    exit_row = row
                row_count += 1
        need(row_count == source_meta["rows"], "ORIGINAL_SOURCE_ROW_COUNT_MISMATCH")
        need(entry_row is not None and exit_row is not None,
             "ORIGINAL_ENTRY_EXIT_ANCHOR_ABSENT")
        need(entry_row["timestamp"] == str(record["entry_ts"])
             and Decimal(entry_row["price"]) == Decimal(record["entry_price"])
             and entry_row["trdMatchID"] == record["entry_trade_id"],
             "ORIGINAL_ENTRY_ANCHOR_MISMATCH")
        need(exit_row["timestamp"] == str(record["exit_ts"]),
             "ORIGINAL_EXIT_TIME_MISMATCH")

        compact = io.BytesIO()
        with gzip.GzipFile(fileobj=compact, mode="wb", filename="",
                           mtime=0, compresslevel=3) as stream:
            for ordinal, stamp, price in tape:
                stream.write((str(ordinal) + "\t" + stamp + "\t"
                              + price + "\n").encode("ascii"))
        need(len(compact.getvalue()) <= MAX_INTERVAL_COMPACT_BYTES,
             "COMPACT_INTERVAL_MEMORY_BOUND")
        decoded = []
        with gzip.GzipFile(fileobj=io.BytesIO(compact.getvalue()), mode="rb") as stream:
            for line in stream:
                fields = line.decode("ascii").rstrip("\n").split("\t")
                need(len(fields) == 3, "COMPACT_EVENT_SCHEMA_MISMATCH")
                decoded.append((int(fields[0]), fields[1], fields[2]))
        need(tape == decoded, "COMPACT_TAPE_SOURCE_ORDER_MISMATCH")
        original_event, audit = first_exit(record, states, tape)
        decoded_event, decoded_audit = first_exit(record, states, decoded)
        need(original_event is not None and original_event == decoded_event
             and audit == decoded_audit, "NATIVE_COMPACT_CAUSAL_EVENT_DRIFT")
        need(original_event["reason"] == record["exit_reason"]
             and original_event["source_order"] == exit_order
             and original_event["timestamp"] == str(record["exit_ts"])
             and Decimal(original_event["exit_price"]) == Decimal(record["exit_price"])
             and original_event["maker_exit"] is record["maker_exit"],
             "HISTORICAL_FIRST_EXIT_EVENT_MISMATCH")
        all_results.append({
            "symbol": symbol, "day": entry_day, "side": record["side"],
            "reason": original_event["reason"], "source_order": exit_order,
            "first_exit_timestamp": original_event["timestamp"],
            "first_exit_price": original_event["exit_price"],
            "bucket_transitions": audit["bucket_transitions"],
            "tp_target_changes": audit["target_changes"],
            "source_day_rows": row_count, "selected_interval_rows": len(tape),
        })
        total_gzip_bytes += source.stat().st_size
        total_day_rows += row_count
        interval_bytes += len(compact.getvalue())
    return {
        "status": "PASS_BOUNDED_CAUSAL_TPONLY_FIRST_EXIT",
        "cases": all_results, "cases_count": len(all_results),
        "full_native_day_rows_scanned": total_day_rows,
        "full_native_gzip_bytes_checked": total_gzip_bytes,
        "compact_selected_interval_bytes": interval_bytes,
        "model_source_sha256": FROZEN_TP_SHA,
        "server_writes": 0,
        "limits": "Accepted historical Entry fixed from ledger, no new candidate "
                  "generation, no portfolio/SOFT_STOP, fee/slippage/exchange replay",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--qc-manifest")
    parser.add_argument("--input-root")
    parser.add_argument("--entry-manifest-root")
    parser.add_argument("--frozen-root")
    parser.add_argument("--ledger")
    parser.add_argument("--ledger-result")
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()
    try:
        if args.self_test:
            out = self_test()
        else:
            need(all((args.qc_manifest, args.input_root, args.entry_manifest_root,
                      args.frozen_root, args.ledger, args.ledger_result)),
                 "EXPLICIT_SOURCE_PATHS_REQUIRED")
            out = run(args)
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_class": type(exc).__name__,
                          "reason": str(exc)[:300]}, sort_keys=True))
        return 1
    print(json.dumps(out, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
