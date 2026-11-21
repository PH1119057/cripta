#!/usr/bin/env python3
"""Bounded RESEARCH-only check of original RAW L5-3 SOFT_STOP first events.

Checks historical #866 H9CONT_18H_S48_OFF positions with same-day exits
STRICTLY before 18 hours. Accepted Entry is a frozen ledger anchor, not
an independently regenerated Entry signal. Replays local frozen TPOnly
precedence and raw opposite L5-3 adverse / width SOFT_STOP on native prints,
then independently on an in-memory ordinal/timestamp/price compressed copy.
Nothing is saved to disk. Never use for production order decisions.
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
SCENARIO = "H9CONT_18H_S48_OFF"
CAUSE = "L53_RAW_MINUS4_WIDTH4"
H9_SOURCE_SHA = "7354de1b4174d88ac53da74d0a27f886054325838e227a1cf05fc85c47372187"
SOFT_SOURCE_SHA = "a9ea1d19defce039fdb02d7465cdc39930ed949616c41ee95fa30bc6fb5fea48"
TP_SOURCE_SHA = "b25210dbd9977fe85f568a52867b3673aea65c17308e19612cb216efae68164e"
BASE_SOURCE_SHA = "3f8e768cf4c6d9cf6ea0d25ecb1fe23c69fbfc2b072e43192ae00d52d8a43a10"
MAX_CASES = 2
MAX_HOLD_SECONDS = 18 * 3600 - 1
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_FROZEN_BYTES = 16 * 1024 * 1024
MAX_INTERVAL_ROWS = 250000
MAX_COMPACT_BYTES = 12 * 1024 * 1024


def need(ok: bool, reason: str) -> None:
    if not ok:
        raise ValueError(reason)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def positive_range(side: str, entry: Decimal, opposite: Decimal) -> Decimal:
    return ((opposite / entry - 1) * 100 if side == "LONG"
            else (1 - opposite / entry) * 100)


def state_for(side: str,
              states: dict[int, tuple[Decimal, Decimal, Decimal]],
              bucket: int) -> tuple[Decimal, Decimal]:
    need(bucket in states, "CAUSAL_L53_STATE_MISSING")
    low, high, width = states[bucket]
    return (high if side == "LONG" else low), width


def first_exit(record: dict,
               states: dict[int, tuple[Decimal, Decimal, Decimal]],
               tape: list[tuple[int, str, str]]) -> tuple[dict | None, dict]:
    """Independently encode frozen historical TPOnly→RAW-SOFT branch precedence."""
    need(bool(tape), "EMPTY_NATIVE_INTERVAL")
    side = record["side"]
    need(side in ("LONG", "SHORT"), "SIDE_NOT_SUPPORTED")
    entry = Decimal(record["entry_price"])
    need(entry > 0, "ENTRY_PRICE_INVALID")
    entry_seq = int(record["entry_source_order"])
    need(tape[0][0] == entry_seq and tape[0][1] == str(record["entry_ts"])
         and Decimal(tape[0][2]) == entry, "ENTRY_TAPE_ANCHOR_MISMATCH")
    bucket = int(float(record["entry_ts"]) // 300) * 300
    initial, _ = state_for(side, states, bucket)
    tp = initial if positive_range(side, entry, initial) >= Decimal("0.5") else None
    tp_live_after = entry_seq
    stop = entry * (Decimal("0.90") if side == "LONG" else Decimal("1.10"))
    transitions = changes = 0
    previous_seq, previous_ts = entry_seq, float(record["entry_ts"])

    for seq, stamp, raw_price in tape[1:]:
        t = float(stamp)
        need(seq > previous_seq and t >= previous_ts,
             "NATIVE_ORDER_OR_TIME_REGRESSION")
        previous_seq, previous_ts = seq, t
        current_bucket = int(t // 300) * 300
        need(current_bucket >= bucket, "CAUSAL_BUCKET_REGRESSION")
        price = Decimal(raw_price)
        need(price.is_finite() and price > 0, "NATIVE_PRICE_INVALID")
        if price <= stop if side == "LONG" else price >= stop:
            return {"reason": "HARD_STOP", "source_order": seq,
                    "timestamp": stamp, "price": raw_price}, {
                        "bucket_transitions": transitions,
                        "tp_target_changes": changes,
                    }
        if tp is not None and seq > tp_live_after:
            if price >= tp if side == "LONG" else price <= tp:
                return {"reason": "RESTING_TP", "source_order": seq,
                        "timestamp": stamp, "price": str(tp)}, {
                            "bucket_transitions": transitions,
                            "tp_target_changes": changes,
                        }
        if current_bucket != bucket:
            bucket = current_bucket
            transitions += 1
            candidate, _ = state_for(side, states, bucket)
            if positive_range(side, entry, candidate) >= Decimal("0.5"):
                if price >= candidate if side == "LONG" else price <= candidate:
                    return {"reason": "MARKETABLE_TP", "source_order": seq,
                            "timestamp": stamp, "price": raw_price}, {
                                "bucket_transitions": transitions,
                                "tp_target_changes": changes,
                            }
                if candidate != tp:
                    changes += 1
                tp = candidate
                tp_live_after = seq
        opposite, width = state_for(side, states, current_bucket)
        if (positive_range(side, entry, opposite) <= Decimal("-4")
                and width >= Decimal("4")):
            return {"reason": "SOFT_STOP", "source_order": seq,
                    "timestamp": stamp, "price": raw_price}, {
                        "bucket_transitions": transitions,
                        "tp_target_changes": changes,
                    }
    return None, {"bucket_transitions": transitions, "tp_target_changes": changes}


def self_test() -> dict:
    entry = {"side": "LONG", "entry_price": "10.0",
             "entry_source_order": 1, "entry_ts": "300.25"}
    states = {
        300: (Decimal("8.5"), Decimal("11.0"), Decimal("4.2")),
        600: (Decimal("8.5"), Decimal("9.5"), Decimal("4.5")),
    }
    prices = [(1, "300.25", "10.0"), (2, "599.75", "10.2"),
              (3, "600.25", "9.6")]
    got, stat = first_exit(entry, states, prices)
    need(got is not None and got["reason"] == "SOFT_STOP"
         and got["source_order"] == 3
         and stat["bucket_transitions"] == 1, "SELFTEST_CAUSAL_LONG_RAW")
    short = {**entry, "side": "SHORT"}
    short_states = {
        300: (Decimal("9.0"), Decimal("11.5"), Decimal("4.2")),
        600: (Decimal("10.5"), Decimal("11.5"), Decimal("4.5")),
    }
    print_short = [(1, "300.25", "10.0"), (2, "599.75", "10.2"),
                   (3, "600.25", "10.4")]
    got, _ = first_exit(short, short_states, print_short)
    need(got is not None and got["reason"] == "SOFT_STOP",
         "SELFTEST_CAUSAL_SHORT_RAW")
    got, _ = first_exit(entry, states, [prices[0], (2, "599.75", "8.9")])
    need(got is not None and got["reason"] == "HARD_STOP",
         "SELFTEST_HARDSTOP_PRIORITY")
    got, _ = first_exit(entry, states, [prices[0], (2, "599.75", "11.1")])
    need(got is not None and got["reason"] == "RESTING_TP",
         "SELFTEST_OLD_RESTING_PRIORITY")
    try:
        first_exit(entry, {300: states[300]}, prices)
    except ValueError as exc:
        need(str(exc) == "CAUSAL_L53_STATE_MISSING", "SELFTEST_MISSING_STATE_REASON")
    else:
        raise ValueError("SELFTEST_MISSING_STATE_ACCEPTED")
    return {"status": "SELFTEST_PASS", "cases": 5, "server_writes": 0}


def parse_case(raw: str) -> tuple[str, int, int]:
    parts = raw.split(":")
    need(len(parts) == 3 and parts[0] in SYMBOLS, "CASE_SELECTOR_INVALID")
    try:
        entry, exit_order = int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise ValueError("CASE_SOURCE_ORDER_INVALID") from exc
    need(entry > 0 and exit_order > entry, "CASE_SOURCE_ORDER_INVALID")
    return parts[0], entry, exit_order


def load_states(path: Path, buckets: set[int]) -> dict[int, tuple[Decimal, Decimal, Decimal]]:
    states: dict[int, tuple[Decimal, Decimal, Decimal]] = {}
    with gzip.open(path, "rt", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"state_effective_ts", "source_bar_end_ts",
                    "range_low", "range_high", "working_width_pct_lower"}
        need(required.issubset(reader.fieldnames or ()), "FROZEN_STATE_SCHEMA_INVALID")
        for row in reader:
            b = int(row["state_effective_ts"])
            if b not in buckets:
                continue
            need(int(row["source_bar_end_ts"]) == b and b % 300 == 0,
                 "NONCAUSAL_FROZEN_L53_STATE")
            need(b not in states, "DUPLICATE_FROZEN_STATE")
            low, high, width = (Decimal(row["range_low"]),
                                Decimal(row["range_high"]),
                                Decimal(row["working_width_pct_lower"]))
            need(all(x.is_finite() for x in (low, high, width))
                 and low > 0 and high >= low and width >= 0,
                 "INVALID_FROZEN_STATE_VALUES")
            states[b] = (low, high, width)
    need(set(states) == buckets, "FROZEN_L53_STATE_COVERAGE_MISSING")
    return states


def run(args: argparse.Namespace) -> dict:
    case_keys = [parse_case(x) for x in args.case]
    need(0 < len(case_keys) <= MAX_CASES and len(set(case_keys)) == len(case_keys),
         "CASE_COUNT_OR_DUPLICATE_INVALID")
    metadata = json.loads(Path(args.qc_manifest).read_text())
    need(metadata.get("stage") == "ACQUISITION_QC_PASS", "SOURCE_QC_NOT_PASS")
    table = metadata["by_day"]
    report = json.loads(Path(args.ledger_result).read_text())
    need(report.get("status") == "QC_PASS", "LEDGER_RESULT_NOT_QC_PASS")
    source_versions = report.get("source_snapshot_sha256", {})
    for path, fingerprint in (
        (args.h9_source, H9_SOURCE_SHA), (args.soft_source, SOFT_SOURCE_SHA)
    ):
        source = Path(path)
        need(source.is_file() and sha256(source) == fingerprint,
             "FROZEN_SOFT_SOURCE_FILE_MISMATCH")
        need(source_versions.get(source.name) == fingerprint,
             "FROZEN_SOFT_SOURCE_UNPINNED")
    for filename, hashvalue in (
        ("p90l90_tp_only_second48_v1.py", TP_SOURCE_SHA),
        ("p90l90_exit_90d_v2.py", BASE_SOURCE_SHA),
    ):
        need(source_versions.get(filename) == hashvalue,
             "FROZEN_TP_MODEL_SOURCE_UNPINNED")
    need(sha256(Path(args.ledger)) == report["ledger_sha256"],
         "REFERENCE_LEDGER_SHA_MISMATCH")
    need(int(report["ledger_rows"]) > 0, "EMPTY_REFERENCE_LEDGER")
    requested = set(case_keys)
    selected = {}
    n = 0
    for line in Path(args.ledger).open(encoding="utf-8"):
        row = json.loads(line)
        n += 1
        key = (row["symbol"], int(row["entry_source_order"]),
               int(row["exit_source_order"]))
        if key not in requested or row.get("portfolio_scenario") != SCENARIO:
            continue
        need(key not in selected, "DUPLICATE_SELECTED_SCENARIO")
        selected[key] = row
    need(n == int(report["ledger_rows"]), "REFERENCE_LEDGER_ROW_COUNT_MISMATCH")
    need(set(selected) == requested, "SELECTED_SCENARIO_NOT_FOUND")
    results = []
    full_rows = compressed_bytes = compact_bytes = 0

    for symbol, entry_seq, exit_seq in case_keys:
        record = selected[symbol, entry_seq, exit_seq]
        need(record.get("exit_reason") == "SOFT_STOP"
             and record.get("soft_stop_cause") == CAUSE, "SOFTSTOP_CAUSE_UNSUPPORTED")
        need(record.get("side") in ("LONG", "SHORT"), "SIDE_NOT_SUPPORTED")
        entry_time, exit_time = float(record["entry_ts"]), float(record["exit_ts"])
        day = dt.datetime.fromtimestamp(entry_time, dt.timezone.utc).date().isoformat()
        need(day == dt.datetime.fromtimestamp(exit_time, dt.timezone.utc).date().isoformat()
             and 0 < exit_time - entry_time <= MAX_HOLD_SECONDS,
             "SOFTSTOP_18H_TIMER_NOT_EXCLUDED")
        source = Path(args.input_root) / symbol / "public_trades" / (
            symbol + day + ".csv.gz")
        key = symbol + "/public_trades/" + source.name
        need(key in table and source.is_file(), "ORIGINAL_SOURCE_ABSENT")
        m = table[key]
        need(0 < source.stat().st_size <= MAX_SOURCE_BYTES,
             "ORIGINAL_SOURCE_SIZE_BOUND")
        need(source.stat().st_size == m["size_bytes"]
             and sha256(source) == m["sha256"], "ORIGINAL_SOURCE_SHA_MISMATCH")
        prefix = sum(int(i["rows"]) for key0, i in table.items()
                     if key0.startswith(symbol + "/public_trades/")
                     and key0.rsplit("/", 1)[-1][len(symbol):len(symbol) + 10] < day)
        lo, hi = entry_seq - prefix - 1, exit_seq - prefix - 1
        need(0 <= lo < hi < int(m["rows"]), "GLOBAL_SOURCE_ORDER_OUT_OF_DAY")

        manifest = json.loads((Path(args.entry_manifest_root) / symbol
                               / "manifest.json").read_text())
        need(manifest["symbol"] == symbol
             and manifest.get("PREV7D_WIDTH_LOOKAHEAD") is False,
             "ENTRY_MANIFEST_PROVENANCE_MISMATCH")
        frozen = Path(args.frozen_root) / ("states_" + symbol + ".csv.gz")
        need(frozen.is_file() and 0 < frozen.stat().st_size <= MAX_FROZEN_BYTES,
             "FROZEN_L53_FILE_SIZE_BOUND")
        need(sha256(frozen) == manifest["frozen_l53_sha256"],
             "FROZEN_L53_SHA_MISMATCH")
        first_bucket, last_bucket = (int(entry_time // 300) * 300,
                                     int(exit_time // 300) * 300)
        buckets = set(range(first_bucket, last_bucket + 300, 300))
        need(0 < len(buckets) <= MAX_HOLD_SECONDS // 300 + 2,
             "CAUSAL_BUCKET_COUNT_BOUND")
        states = load_states(frozen, buckets)
        interval = []
        previous_time = None
        entry_row = exit_row = None
        nrows = 0
        day_start = dt.datetime.fromisoformat(day).replace(
            tzinfo=dt.timezone.utc).timestamp()
        with gzip.open(source, "rt", newline="") as stream:
            reader = csv.DictReader(stream)
            need({"timestamp", "price", "trdMatchID", "symbol"}.issubset(
                reader.fieldnames or ()), "SOURCE_SCHEMA_INVALID")
            for i, row in enumerate(reader):
                stamp = float(row["timestamp"])
                need(previous_time is None or stamp >= previous_time,
                     "ORIGINAL_TIMESTAMP_REGRESSION")
                previous_time = stamp
                need(day_start <= stamp < day_start + 86400
                     and row["symbol"] == symbol, "ORIGINAL_TRADE_TIME_OR_SYMBOL_DRIFT")
                if i == lo:
                    entry_row = row
                if lo <= i <= hi:
                    interval.append((prefix + i + 1,
                                     row["timestamp"], row["price"]))
                    need(len(interval) <= MAX_INTERVAL_ROWS, "INTERVAL_ROW_COUNT_BOUND")
                if i == hi:
                    exit_row = row
                nrows += 1
        need(nrows == m["rows"], "ORIGINAL_TRADE_COUNT_MISMATCH")
        need(entry_row is not None and exit_row is not None,
             "ORIGINAL_ENTRY_EXIT_ANCHOR_ABSENT")
        need(entry_row["timestamp"] == str(record["entry_ts"])
             and Decimal(entry_row["price"]) == Decimal(record["entry_price"])
             and entry_row["trdMatchID"] == record["entry_trade_id"],
             "ORIGINAL_ENTRY_ANCHOR_MISMATCH")
        need(exit_row["timestamp"] == str(record["exit_ts"])
             and Decimal(exit_row["price"]) == Decimal(record["exit_price"]),
             "ORIGINAL_EXIT_ANCHOR_MISMATCH")

        compact = io.BytesIO()
        with gzip.GzipFile(fileobj=compact, mode="wb", filename="",
                           mtime=0, compresslevel=3) as stream:
            for ordinal, stamp, price in interval:
                stream.write((str(ordinal) + "\t" + stamp
                              + "\t" + price + "\n").encode("ascii"))
        need(len(compact.getvalue()) <= MAX_COMPACT_BYTES, "COMPACT_MEMORY_BOUND")
        unpacked = []
        with gzip.GzipFile(fileobj=io.BytesIO(compact.getvalue()), mode="rb") as stream:
            for line in stream:
                cells = line.decode("ascii").rstrip("\n").split("\t")
                need(len(cells) == 3, "COMPACT_SCHEMA_INVALID")
                unpacked.append((int(cells[0]), cells[1], cells[2]))
        need(interval == unpacked, "COMPACT_NATIVE_ORDER_MISMATCH")
        native, audit = first_exit(record, states, interval)
        packed, audit2 = first_exit(record, states, unpacked)
        need(native is not None and native == packed and audit == audit2,
             "NATIVE_COMPACT_CAUSAL_EXIT_MISMATCH")
        need(native["reason"] == "SOFT_STOP"
             and native["source_order"] == exit_seq
             and native["timestamp"] == str(record["exit_ts"])
             and Decimal(native["price"]) == Decimal(record["exit_price"]),
             "HISTORICAL_FIRST_SOFTSTOP_MISMATCH")
        results.append({
            "symbol": symbol, "day": day, "side": record["side"],
            "entry_order": entry_seq, "exit_order": exit_seq,
            "reason": native["reason"], "exit_price": native["price"],
            "exit_timestamp": native["timestamp"],
            "closed_5m_bucket_transitions": audit["bucket_transitions"],
            "dynamic_tp_target_changes": audit["tp_target_changes"],
            "selected_interval_rows": len(interval),
        })
        full_rows += nrows
        compressed_bytes += source.stat().st_size
        compact_bytes += len(compact.getvalue())
    return {
        "status": "PASS_BOUNDED_RAW_L53_FIRST_SOFTSTOP",
        "cases": results, "cases_count": len(results),
        "whole_native_day_rows_scanned": full_rows,
        "native_full_day_compressed_bytes": compressed_bytes,
        "compact_selected_interval_bytes": compact_bytes,
        "historical_soft_source_sha256": SOFT_SOURCE_SHA,
        "writes": 0,
        "limits": "same-day <18h original ledger accepted Entries only; no H9 timer, "
                  "independent Entry, portfolio, geometry construction, money, venue fills",
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
    parser.add_argument("--h9-source")
    parser.add_argument("--soft-source")
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()
    try:
        if args.self_test:
            value = self_test()
        else:
            need(all((args.qc_manifest, args.input_root, args.entry_manifest_root,
                      args.frozen_root, args.ledger, args.ledger_result,
                      args.h9_source, args.soft_source)),
                 "EXPLICIT_RESEARCH_SOURCE_PATHS_REQUIRED")
            value = run(args)
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_class": type(exc).__name__,
                          "reason": str(exc)[:300]}, sort_keys=True))
        return 1
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
