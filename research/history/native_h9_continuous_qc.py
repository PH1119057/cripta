#!/usr/bin/env python3
"""Bounded RESEARCH-only cross-day native first H9_LOSS_CONTINUOUS event QC.

Historical #866 H9CONT_18H_S48_OFF, accepted Entry is fixed to an immutable
ledger row. Rebuilds rolling RAW 108x5m and 15m anchor union from closed frozen
L5-3 bar inputs; applies local TPOnly/HARD_STOP/RAW_SOFT/H9 timer precedence to
the original Bybit public print stream, then to an in-memory compressed copy.
No new Entry, portfolio/economics, live trading, server file writes or downloads.
"""
from __future__ import annotations

import argparse
from collections import deque
import csv
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal as D
import gzip
import hashlib
import io
import json
from pathlib import Path

SYMBOLS = ("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT")
SCENARIO = "H9CONT_18H_S48_OFF"
CAUSE = "H9_LOSS_CONTINUOUS"
H9_SOURCE_SHA = "7354de1b4174d88ac53da74d0a27f886054325838e227a1cf05fc85c47372187"
SOFT_SOURCE_SHA = "a9ea1d19defce039fdb02d7465cdc39930ed949616c41ee95fa30bc6fb5fea48"
TP_SOURCE_SHA = "b25210dbd9977fe85f568a52867b3673aea65c17308e19612cb216efae68164e"
BASE_SOURCE_SHA = "3f8e768cf4c6d9cf6ea0d25ecb1fe23c69fbfc2b072e43192ae00d52d8a43a10"

MAX_CASES = 2
MAX_DAY_GZIP = 32 * 1024 * 1024
MAX_FROZEN_GZIP = 16 * 1024 * 1024
MAX_INTERVAL_ROWS = 300000
MAX_COMPACT_BYTES = 16 * 1024 * 1024
MAX_FROZEN_STATES = 70000
H9_HORIZON_SECONDS = 18 * 3600
MAX_HOLD_SECONDS = 36 * 3600


def need(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def favorable(side: str, entry: D, target: D) -> D:
    return ((target / entry - 1) * 100 if side == "LONG"
            else (1 - target / entry) * 100)


def reaches_tp(side: str, price: D, target: D) -> bool:
    return price >= target if side == "LONG" else price <= target


def reaches_stop(side: str, price: D, stop: D) -> bool:
    return price <= stop if side == "LONG" else price >= stop


def first_exit(record: dict, states: dict[int, tuple[D, D, D, D, D]],
               tape: list[tuple[int, str, str]]) -> tuple[dict | None, dict]:
    """Independent local event precedence; historical TP first live on later 5m bucket."""
    need(bool(tape), "EMPTY_EVENT_INTERVAL")
    side = record["side"]
    need(side in ("LONG", "SHORT"), "INVALID_DIRECTION")
    entry = D(str(record["entry_price"]))
    need(entry.is_finite() and entry > 0, "INVALID_ENTRY_PRICE")
    entry_seq = int(record["entry_source_order"])
    entry_time = float(record["entry_ts"])
    need(tape[0][0] == entry_seq and tape[0][1] == str(record["entry_ts"])
         and D(tape[0][2]) == entry, "ENTRY_TAPE_ANCHOR_MISMATCH")
    bucket = int(entry_time // 300) * 300
    need(bucket in states, "INITIAL_H9_STATE_MISSING")

    # Exact frozen historical TPOnly Position starts with tp=None.
    tp = None
    tp_live_after = -1
    hard_stop = entry * (D("0.90") if side == "LONG" else D("1.10"))
    h9_first = None
    starts = resets = transitions = tp_changes = 0
    prev_seq = entry_seq
    prev_time = entry_time

    for seq, stamp, price_text in tape[1:]:
        t = float(stamp)
        need(seq > prev_seq and t >= prev_time, "NATIVE_SEQUENCE_OR_TIMESTAMP_REGRESSION")
        prev_seq, prev_time = seq, t
        current_bucket = int(t // 300) * 300
        need(current_bucket >= bucket, "NATIVE_BUCKET_REGRESSION")
        need(current_bucket in states, "MISSING_CAUSAL_H9_L53_STATE")
        need(current_bucket <= t, "FUTURE_H9_STATE_CONSUMED")
        price = D(price_text)
        need(price.is_finite() and price > 0, "INVALID_NATIVE_PRICE")
        if reaches_stop(side, price, hard_stop):
            reason = {"reason": "HARD_STOP", "source_order": seq,
                      "timestamp": stamp, "price": price_text, "maker_exit": False}
            break
        if tp is not None and seq > tp_live_after and reaches_tp(side, price, tp):
            reason = {"reason": "RESTING_TP", "source_order": seq,
                      "timestamp": stamp, "price": str(tp), "maker_exit": True}
            break
        if current_bucket != bucket:
            bucket = current_bucket
            transitions += 1
            low, high, _, _, _ = states[bucket]
            candidate = high if side == "LONG" else low
            if favorable(side, entry, candidate) >= D("0.5"):
                if reaches_tp(side, price, candidate):
                    reason = {"reason": "MARKETABLE_TP", "source_order": seq,
                              "timestamp": stamp, "price": price_text, "maker_exit": False}
                    break
                if candidate != tp:
                    tp_changes += 1
                tp = candidate
                tp_live_after = seq

        low, high, width, h9low, h9high = states[current_bucket]
        boundary = h9high if side == "LONG" else h9low
        adverse = favorable(side, entry, boundary) <= D(0)
        before = h9_first
        if not adverse:
            h9_first = None
        elif h9_first is None:
            h9_first = max(current_bucket, entry_time)
        if before is not None and h9_first is None:
            resets += 1
        if before is None and h9_first is not None:
            starts += 1
        raw_boundary = high if side == "LONG" else low
        raw_soft = (favorable(side, entry, raw_boundary) <= D("-4")
                    and width >= D("4"))
        timer_due = (h9_first is not None and adverse
                     and t >= h9_first + H9_HORIZON_SECONDS)
        if raw_soft or timer_due:
            reason = {"reason": "SOFT_STOP", "source_order": seq,
                      "timestamp": stamp, "price": price_text, "maker_exit": False,
                      "soft_stop_cause": ("L53_RAW_MINUS4_WIDTH4" if raw_soft else CAUSE)}
            break
    else:
        reason = None
    audit = {"first_h9_loss_ts": h9_first, "h9_adverse_starts": starts,
             "h9_recovery_resets": resets, "closed_5m_bucket_transitions": transitions,
             "dynamic_tp_target_changes": tp_changes}
    return reason, audit


def self_test() -> dict:
    """Synthetic state-machine tests; source SHA and original row fidelity are checked in run."""
    record = {"side": "LONG", "entry_price": "10.0", "entry_source_order": 1,
              "entry_ts": "300.25"}
    # (raw_low, raw_high, working_width, h9low, h9high)
    states = {
        300: (D("8.5"), D("10.8"), D("2"), D("8.0"), D("11.0")),
        600: (D("8.5"), D("9.8"), D("2"), D("8.0"), D("9.9")),
        900: (D("8.5"), D("10.1"), D("2"), D("8.0"), D("10.5")),
        1200: (D("8.5"), D("9.8"), D("2"), D("8.0"), D("9.9")),
        66000: (D("8.5"), D("9.8"), D("2"), D("8.0"), D("9.9")),
    }
    tape = [(1, "300.25", "10.0"), (2, "600.25", "9.9"),
            (3, "900.25", "9.9"), (4, "1200.25", "9.9"),
            (5, "66000.25", "9.9")]
    result, audit = first_exit(record, states, tape)
    need(result is not None and result["reason"] == "SOFT_STOP"
         and result["soft_stop_cause"] == CAUSE
         and result["source_order"] == 5, "SELFTEST_LONG_TIMER_FIRST_EXIT")
    need(audit["first_h9_loss_ts"] == 1200
         and audit["h9_adverse_starts"] == 2
         and audit["h9_recovery_resets"] == 1, "SELFTEST_H9_RESET_RESTART")
    result, _ = first_exit(record, states, tape[:-1])
    need(result is None, "SELFTEST_EARLY_H9_TIMER")
    blocked, _ = first_exit(record, states, [tape[0], (2, "600.25", "8.9")])
    need(blocked is not None and blocked["reason"] == "HARD_STOP",
         "SELFTEST_HARD_STOP_PRIORITY")
    short = {**record, "side": "SHORT"}
    short_states = {
        300: (D("9.0"), D("11.5"), D("2"), D("8.8"), D("11.6")),
        600: (D("10.1"), D("11.5"), D("2"), D("10.1"), D("11.6")),
        65400: (D("10.1"), D("11.5"), D("2"), D("10.1"), D("11.6")),
    }
    sresult, saudit = first_exit(short, short_states,
                                [(1, "300.25", "10.0"), (2, "600.25", "10.1"),
                                 (3, "65400.25", "10.1")])
    need(sresult is not None and sresult["soft_stop_cause"] == CAUSE
         and saudit["first_h9_loss_ts"] == 600, "SELFTEST_SHORT_TIMER")
    try:
        first_exit(record, {300: states[300]}, tape[:2])
    except ValueError as exc:
        need(str(exc) == "MISSING_CAUSAL_H9_L53_STATE",
             "SELFTEST_MISSING_CAUSAL_STATE_REASON")
    else:
        raise ValueError("SELFTEST_MISSING_STATE_ALLOWED")
    return {"status": "SELFTEST_PASS", "cases": 6, "server_writes": 0}


def parse_case(raw: str) -> tuple[str, int, int]:
    a = raw.split(":")
    need(len(a) == 3 and a[0] in SYMBOLS, "INVALID_EXPLICIT_CASE")
    try:
        start, end = int(a[1]), int(a[2])
    except ValueError as exc:
        raise ValueError("INVALID_CASE_SOURCE_ORDINAL") from exc
    need(0 < start < end, "INVALID_CASE_SOURCE_ORDINAL")
    return a[0], start, end


def frozen_states(path: Path, wanted: set[int]) -> dict[int, tuple[D, D, D, D, D]]:
    """Reconstruct H9 directly from frozen closed 5m RAW bars; O(N) deques."""
    high = deque()
    low = deque()
    base_5m = {}
    raw = {}
    i = 0
    previous_bucket = None
    with gzip.open(path, "rt", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"state_effective_ts", "source_bar_end_ts", "range_low",
                    "range_high", "working_width_pct_lower", "source_high", "source_low"}
        need(required.issubset(reader.fieldnames or ()), "FROZEN_L53_SCHEMA_INVALID")
        for row in reader:
            bucket = int(row["state_effective_ts"])
            need(bucket == int(row["source_bar_end_ts"]) and bucket % 300 == 0,
                 "UNCLOSED_OR_NONCAUSAL_FROZEN_BAR")
            need(previous_bucket is None or bucket == previous_bucket + 300,
                 "FROZEN_5M_SOURCE_GAP_OR_REORDER")
            previous_bucket = bucket
            barhigh = D(row["source_high"])
            barlow = D(row["source_low"])
            need(all(x.is_finite() for x in (barhigh, barlow))
                 and barlow > 0 and barhigh >= barlow, "INVALID_H9_RAW_BAR")
            while high and high[-1][1] <= barhigh:
                high.pop()
            high.append((i, barhigh))
            while low and low[-1][1] >= barlow:
                low.pop()
            low.append((i, barlow))
            while high and high[0][0] <= i - 108:
                high.popleft()
            while low and low[0][0] <= i - 108:
                low.popleft()
            if i >= 107 and bucket >= min(wanted) - 900 and bucket <= max(wanted):
                base_5m[bucket] = (low[0][1], high[0][1])
            if bucket in wanted:
                rlow, rhigh, width = (D(row["range_low"]), D(row["range_high"]),
                                      D(row["working_width_pct_lower"]))
                need(all(x.is_finite() for x in (rlow, rhigh, width))
                     and rlow > 0 and rhigh >= rlow and width >= 0,
                     "INVALID_FROZEN_L53_RANGE")
                raw[bucket] = rlow, rhigh, width
            i += 1
            need(i <= MAX_FROZEN_STATES, "FROZEN_STATE_ROW_BOUND")
    need(set(raw) == wanted, "MISSING_CAUSAL_FROZEN_L53_BUCKET")
    result = {}
    for bucket, (rlow, rhigh, width) in raw.items():
        anchor = (bucket // 900) * 900
        need(bucket in base_5m and anchor in base_5m,
             "MISSING_H9_15M_CLOSED_ANCHOR")
        low5, high5 = base_5m[bucket]
        low15, high15 = base_5m[anchor]
        result[bucket] = (rlow, rhigh, width,
                          min(low5, low15), max(high5, high15))
    return result


def run(args: argparse.Namespace) -> dict:
    selectors = [parse_case(raw) for raw in args.case]
    need(0 < len(selectors) <= MAX_CASES
         and len(set(selectors)) == len(selectors), "CASE_COUNT_OR_DUPLICATE_INVALID")
    manifest = json.loads(Path(args.qc_manifest).read_text())
    need(manifest.get("stage") == "ACQUISITION_QC_PASS", "SOURCE_QC_NOT_PASS")
    table = manifest["by_day"]
    summary = json.loads(Path(args.ledger_result).read_text())
    need(summary.get("status") == "QC_PASS", "REFERENCE_LEDGER_NOT_QC_PASS")
    pinned = summary.get("source_snapshot_sha256", {})
    for path, sha in ((args.h9_source, H9_SOURCE_SHA),
                      (args.soft_source, SOFT_SOURCE_SHA)):
        source = Path(path)
        need(source.is_file() and digest(source) == sha, "FROZEN_H9_SOURCE_FILE_DRIFT")
        need(pinned.get(source.name) == sha, "FROZEN_H9_SOURCE_UNPINNED")
    for filename, sha in (("p90l90_tp_only_second48_v1.py", TP_SOURCE_SHA),
                          ("p90l90_exit_90d_v2.py", BASE_SOURCE_SHA)):
        need(pinned.get(filename) == sha, "FROZEN_TP_SOURCE_UNPINNED")
    ledger_path = Path(args.ledger)
    need(digest(ledger_path) == summary["ledger_sha256"], "REFERENCE_LEDGER_SHA_DRIFT")
    selected = {}
    total = 0
    for line in ledger_path.open(encoding="utf-8"):
        record = json.loads(line)
        total += 1
        key = (record["symbol"], int(record["entry_source_order"]),
               int(record["exit_source_order"]))
        if key not in selectors or record.get("portfolio_scenario") != SCENARIO:
            continue
        need(key not in selected, "DUPLICATE_SCENARIO_REFERENCE")
        selected[key] = record
    need(total == summary["ledger_rows"], "REFERENCE_LEDGER_ROW_COUNT_DRIFT")
    need(set(selected) == set(selectors), "SELECTED_H9_REFERENCE_MISSING")

    results = []
    total_gzip = total_day_rows = compact_size = 0
    for symbol, entry_order, exit_order in selectors:
        record = selected[symbol, entry_order, exit_order]
        need(record["exit_reason"] == "SOFT_STOP"
             and record["soft_stop_cause"] == CAUSE
             and record["h9_horizon_hours"] == 18,
             "NOT_FROZEN_H9_18H_SOFT_STOP")
        entry_t, exit_t = float(record["entry_ts"]), float(record["exit_ts"])
        day1 = datetime.fromtimestamp(entry_t, timezone.utc).date()
        day2 = datetime.fromtimestamp(exit_t, timezone.utc).date()
        need((day2 - day1).days == 1
             and H9_HORIZON_SECONDS <= exit_t - entry_t <= MAX_HOLD_SECONDS,
             "NOT_BOUNDED_ADJACENT_CROSS_DAY_H9")
        first_bucket = int(entry_t // 300) * 300
        last_bucket = int(exit_t // 300) * 300
        wanted_buckets = set(range(first_bucket, last_bucket + 300, 300))
        need(0 < len(wanted_buckets) <= MAX_HOLD_SECONDS // 300 + 2,
             "H9_BUCKET_COUNT_BOUND")

        frozen = Path(args.frozen_root) / ("states_" + symbol + ".csv.gz")
        entry_manifest = json.loads((Path(args.entry_manifest_root) / symbol
                                     / "manifest.json").read_text())
        need(entry_manifest["symbol"] == symbol
             and entry_manifest.get("PREV7D_WIDTH_LOOKAHEAD") is False,
             "ENTRY_MANIFEST_PROVENANCE_DRIFT")
        need(frozen.is_file() and 0 < frozen.stat().st_size <= MAX_FROZEN_GZIP,
             "FROZEN_STATES_FILE_SIZE_BOUND")
        need(digest(frozen) == entry_manifest["frozen_l53_sha256"],
             "FROZEN_STATES_SHA_DRIFT")
        states = frozen_states(frozen, wanted_buckets)

        prefix = sum(int(v["rows"]) for key, v in table.items()
                     if key.startswith(symbol + "/public_trades/")
                     and key.rsplit("/", 1)[-1][len(symbol):len(symbol) + 10]
                     < day1.isoformat())
        day_files = []
        for day in (day1, day2):
            name = symbol + day.isoformat() + ".csv.gz"
            key = symbol + "/public_trades/" + name
            source = Path(args.input_root) / symbol / "public_trades" / name
            need(key in table and source.is_file(), "ORIGINAL_PUBLIC_TRADE_DAY_MISSING")
            expected = table[key]
            need(0 < source.stat().st_size <= MAX_DAY_GZIP
                 and source.stat().st_size == expected["size_bytes"],
                 "ORIGINAL_PUBLIC_TRADE_DAY_SIZE_DRIFT")
            need(digest(source) == expected["sha256"],
                 "ORIGINAL_PUBLIC_TRADE_DAY_SHA_DRIFT")
            day_files.append((day, source, expected))
        interval = []
        previous_time = None
        first_row = last_row = None
        for day, source, expected in day_files:
            nrows = 0
            begin = datetime.combine(day, datetime.min.time(),
                                     tzinfo=timezone.utc).timestamp()
            with gzip.open(source, "rt", newline="") as stream:
                reader = csv.DictReader(stream)
                need({"timestamp", "price", "trdMatchID", "symbol"}.issubset(
                    reader.fieldnames or ()), "ORIGINAL_NATIVE_SCHEMA_INVALID")
                for row in reader:
                    seq = prefix + nrows + 1
                    t = float(row["timestamp"])
                    need(begin <= t < begin + 86400
                         and (previous_time is None or t >= previous_time)
                         and row["symbol"] == symbol,
                         "NATIVE_TIME_OR_SYMBOL_DRIFT")
                    previous_time = t
                    if seq == entry_order:
                        first_row = row
                    if entry_order <= seq <= exit_order:
                        interval.append((seq, row["timestamp"], row["price"]))
                        need(len(interval) <= MAX_INTERVAL_ROWS,
                             "NATIVE_INTERVAL_ROW_LIMIT")
                    if seq == exit_order:
                        last_row = row
                    nrows += 1
            need(nrows == expected["rows"], "ORIGINAL_DAY_ROW_COUNT_DRIFT")
            prefix += nrows
            total_day_rows += nrows
            total_gzip += source.stat().st_size
        need(first_row is not None and last_row is not None
             and interval[0][0] == entry_order
             and interval[-1][0] == exit_order, "EXACT_LEDGER_ANCHORS_NOT_FOUND")
        need(first_row["timestamp"] == str(record["entry_ts"])
             and D(first_row["price"]) == D(record["entry_price"])
             and first_row["trdMatchID"] == record["entry_trade_id"],
             "ORIGINAL_ENTRY_ANCHOR_MISMATCH")
        need(last_row["timestamp"] == str(record["exit_ts"])
             and D(last_row["price"]) == D(record["exit_price"]),
             "ORIGINAL_EXIT_ANCHOR_MISMATCH")

        compact = io.BytesIO()
        with gzip.GzipFile(fileobj=compact, mode="wb", filename="",
                           mtime=0, compresslevel=3) as stream:
            for seq, stamp, price in interval:
                stream.write((str(seq) + "\t" + stamp
                              + "\t" + price + "\n").encode("ascii"))
        need(len(compact.getvalue()) <= MAX_COMPACT_BYTES, "COMPACT_MEMORY_SIZE_BOUND")
        decoded = []
        with gzip.GzipFile(fileobj=io.BytesIO(compact.getvalue()), mode="rb") as stream:
            for line in stream:
                vals = line.decode("ascii").rstrip("\n").split("\t")
                need(len(vals) == 3, "COMPACT_EVENT_SCHEMA_DRIFT")
                decoded.append((int(vals[0]), vals[1], vals[2]))
        need(decoded == interval, "COMPACT_NATIVE_EVENT_ORDER_DRIFT")
        original_event, original_audit = first_exit(record, states, interval)
        compact_event, compact_audit = first_exit(record, states, decoded)
        need(original_event is not None and original_event == compact_event
             and original_audit == compact_audit, "H9_NATIVE_COMPACT_FIRST_EVENT_DRIFT")
        need(original_event["reason"] == "SOFT_STOP"
             and original_event["soft_stop_cause"] == CAUSE
             and original_event["source_order"] == exit_order
             and original_event["timestamp"] == str(record["exit_ts"])
             and D(original_event["price"]) == D(record["exit_price"])
             and not original_event["maker_exit"], "H9_FIRST_EXIT_LEDGER_MISMATCH")
        need(original_audit["first_h9_loss_ts"] is not None
             and float(original_audit["first_h9_loss_ts"])
             == float(record["first_h9_loss_ts"]),
             "H9_FIRST_ADVERSE_TIMER_START_MISMATCH")

        results.append({
            "symbol": symbol, "side": record["side"],
            "entry_day": day1.isoformat(), "exit_day": day2.isoformat(),
            "first_h9_loss_ts": original_audit["first_h9_loss_ts"],
            "first_exit_reason": original_event["reason"],
            "first_exit_cause": original_event["soft_stop_cause"],
            "entry_source_order": entry_order, "exit_source_order": exit_order,
            "exit_timestamp": original_event["timestamp"],
            "exit_price": original_event["price"],
            "h9_adverse_starts": original_audit["h9_adverse_starts"],
            "h9_recovery_resets": original_audit["h9_recovery_resets"],
            "closed_5m_transitions": original_audit["closed_5m_bucket_transitions"],
            "selected_trade_rows": len(interval),
        })
        compact_size += len(compact.getvalue())
    return {"status": "PASS_BOUNDED_H9_CONTINUOUS_FIRST_EXIT",
            "cases_count": len(results), "cases": results,
            "original_day_files": 2 * len(results),
            "original_full_day_trade_rows": total_day_rows,
            "original_day_gzip_bytes": total_gzip,
            "compressed_selected_intervals_only_bytes": compact_size,
            "source_h9_sha256": H9_SOURCE_SHA,
            "writes": 0,
            "limits": "Accepted historical Entry fixed; up to 2 adjacent-day 18h "
                      "events, independent closed RAW 108x5m+15m H9 union; no "
                      "independent Entry/portfolio/economics/venue fills"}


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
            outcome = self_test()
        else:
            need(all((args.qc_manifest, args.input_root, args.entry_manifest_root,
                      args.frozen_root, args.ledger, args.ledger_result,
                      args.h9_source, args.soft_source)),
                 "EXPLICIT_H9_SOURCE_PATHS_REQUIRED")
            outcome = run(args)
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error_class": type(exc).__name__,
                          "reason": str(exc)[:300]}, sort_keys=True))
        return 1
    print(json.dumps(outcome, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
