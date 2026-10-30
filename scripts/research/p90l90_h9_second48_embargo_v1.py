#!/usr/bin/env python3
"""Research #224: causal H9 dynamic TP with second Hard Stop 48h Entry embargo.

Combines exact frozen P90L90 native-tick Exit logic from #210 and #218.
Writer cripta UID107 creates only a NEW child below a research results parent;
no live/DB/exchange/runtime writes, no rename/delete, never forced flatten.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
import shutil
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path

from scripts.research import p90l90_entry_only_hardstop_embargo_v1 as pause
from scripts.research import p90l90_h9_raw_opposite_tp_v1 as h9

RESEARCH = "P90L90_H9_DYNAMIC_SECOND48_ENTRY_ONLY_V1"
LEAF = "p90l90_h9_second48_embargo_20261026_v1"
SYMBOLS = tuple(pause.SYMBOLS)
SCENARIOS = {
    "L53_NO_PAUSE": ("L53", 0, None),
    "L53_SECOND48": ("L53", 2, 48),
    "H9_DYNAMIC_NO_PAUSE": ("H9", 0, None),
    "H9_DYNAMIC_SECOND48": ("H9", 2, 48),
}
REFERENCES = {
    "L53_NO_PAUSE": ("entry_only", "BASELINE"),
    "L53_SECOND48": ("entry_only", "SECOND_48H"),
    "H9_DYNAMIC_NO_PAUSE": ("h9_tp", "H9_RAW_DYNAMIC"),
}
OLD_RESULTS = {
    "entry_only": {
        "90d": "entry_only_embargo_90d_20261026_v1",
        "29d": "entry_only_embargo_29d_20261026_v1",
    },
    "h9_tp": {"90d": h9.LEAF, "29d": h9.LEAF},
}
REF_FIELD = {"entry_only": "portfolio_scenario", "h9_tp": "tp_scenario"}


class CombinationBook(pause.EntryEmbargo):
    """Each TP/embargo pair has its OWN stop history, slots, and global pause."""

    def __init__(self, tp_type, threshold, hours):
        if tp_type not in ("L53", "H9"):
            raise ValueError("unsupported TP type")
        super().__init__(threshold, hours)
        self.tp_type = tp_type

    def handle_combined(self, symbol, t, seq, px, events, l53_state, h9_bounds):
        if self.tp_type == "L53":
            return self.handle(symbol, t, seq, px, events, l53_state)
        if h9_bounds is None and (
                self.pos[symbol] is not None or
                any(event["PREV7D"] for event in events)):
            raise ValueError("missing causally closed H9 TP geometry")
        state = (
            (h9_bounds[0], h9_bounds[1], l53_state[2])
            if h9_bounds is not None and l53_state is not None else None
        )
        return self.handle(symbol, t, seq, px, events, state)


def sha256(path):
    with path.open("rb") as reader:
        return hashlib.file_digest(reader, "sha256").hexdigest()


def preflight(period, output, smoke_hours):
    if period not in h9.ROOTS:
        raise ValueError("period must be 90d or 29d")
    if os.geteuid() != 107:
        raise PermissionError("effective actor must be cripta UID107")
    if smoke_hours < 0 or smoke_hours > 24:
        raise ValueError("smoke_hours must be 0..24")
    parent = h9.ROOTS[period] / "results"
    if (not output.is_absolute() or output.name != LEAF
            or output.parent.resolve() != parent.resolve()):
        raise ValueError("not exact approved new research results path")
    if output.exists() or output.is_symlink():
        raise FileExistsError("immutable result path already exists")
    if parent.stat().st_uid != 107 or not os.access(parent, os.W_OK | os.X_OK):
        raise PermissionError("data disk output parent unavailable to cripta")
    return parent


def read_references(period):
    root = h9.ROOTS[period] / "results"
    ledgers = {}
    provenance = {}
    for family in ("entry_only", "h9_tp"):
        folder = root / OLD_RESULTS[family][period]
        result = json.loads((folder / "result.json").read_text())
        ledger = folder / "trade_ledger.jsonl"
        digest = sha256(ledger)
        if digest != result["ledger_sha256"]:
            raise ValueError("immutable reference ledger SHA mismatch " + family)
        by_scenario_symbol = defaultdict(list)
        row_count = 0
        for line in ledger.open(encoding="utf-8"):
            row = json.loads(line)
            old_name = row.pop(REF_FIELD[family])
            by_scenario_symbol[old_name, row["symbol"]].append(row)
            row_count += 1
        if row_count != result["ledger_rows"]:
            raise ValueError("immutable reference ledger row count drift")
        ledgers[family] = by_scenario_symbol
        provenance[family] = digest
    return ledgers, provenance


def validate_ledger(books, reports, period):
    originals, provenance = read_references(period)
    for mode, (family, old_mode) in REFERENCES.items():
        actual = defaultdict(list)
        for row in books[mode].trades:
            actual[row["symbol"]].append(row)
        for symbol in SYMBOLS:
            if actual[symbol] != originals[family][old_mode, symbol]:
                raise ValueError("exact preexisting trace parity failed "
                                 + period + " " + mode + " " + symbol)
    for mode, book in books.items():
        c = reports[mode]["counts"]
        if (c["accepted"] != c["closed"] + c["censored_open"]
                or c.get("close_PORTFOLIO_FLATTEN", 0) != 0 or book.pending):
            raise ValueError("entry-only invariant violated " + mode)
        actual_money = reports[mode]["money"]
        gross = sum((D(r["gross_usdt"]) for r in book.trades), D(0))
        entry_fee = sum((D(r["entry_fee_usdt"]) for r in book.trades), D(0))
        exit_fee = sum((D(r["exit_fee_usdt"]) for r in book.trades), D(0))
        net = sum((D(r["net_usdt"]) for r in book.trades), D(0))
        if (gross - entry_fee - exit_fee != net
                or net != D(actual_money["net_usdt"])
                or gross != D(actual_money["gross_usdt"])
                or entry_fee != D(actual_money["entry_fees_usdt"])
                or exit_fee != D(actual_money["exit_fees_usdt"])
                or len(book.trades) != c["closed"]):
            raise ValueError("financial or trade-count ledger parity failed " + mode)
    return provenance


def money_breakdown(books):
    summary = {}
    for name, book in books.items():
        rows = book.trades
        by_coin = {}
        for symbol in SYMBOLS:
            sub = [r for r in rows if r["symbol"] == symbol]
            reasons = Counter(r["exit_reason"] for r in sub)
            by_side = {}
            for side in ("LONG", "SHORT"):
                sub_side = [r for r in sub if r["side"] == side]
                by_side[side] = {
                    "closed": len(sub_side),
                    "net_original_fees": str(sum(
                        (D(r["net_usdt"]) for r in sub_side), D(0))),
                    "net_conservative_taker_taker": str(sum(
                        (h9.conservative_net(r) for r in sub_side), D(0))),
                }
            by_coin[symbol] = {
                "closed": len(sub),
                "reasons": dict(reasons),
                "net_original_fees": str(sum(
                    (D(r["net_usdt"]) for r in sub), D(0))),
                "net_conservative_taker_taker": str(sum(
                    (h9.conservative_net(r) for r in sub), D(0))),
                "by_side": by_side,
            }
        summary[name] = {
            "by_coin": by_coin,
            "net_original_fees": str(sum(
                (D(r["net_usdt"]) for r in rows), D(0))),
            "net_conservative_taker_taker": str(sum(
                (h9.conservative_net(r) for r in rows), D(0))),
        }
    return summary


def run(period, output, smoke_hours=0):
    preflight(period, output, smoke_hours)
    first = h9.oldentry90.START_TS if period == "90d" else h9.oldentry29.START_TS
    end = h9.oldentry90.END_TS if period == "90d" else h9.oldentry29.END_TS
    data = {symbol: h9.read_input(period, symbol) for symbol in SYMBOLS}
    streams = {symbol: h9.h9_native_ticks(period, data[symbol])
               for symbol in SYMBOLS}
    books = {name: CombinationBook(*params)
             for name, params in SCENARIOS.items()}
    pending = []
    for priority, symbol in enumerate(SYMBOLS):
        tick = next(streams[symbol], None)
        if tick is None:
            raise ValueError("empty causal H9 trade stream " + symbol)
        heapq.heappush(pending, (tick[0], priority, tick))
    limit = end if smoke_hours == 0 else min(end, first + smoke_hours * 3600)
    ticks = 0
    last_day = None
    while pending:
        t, priority, row = heapq.heappop(pending)
        if t >= limit:
            break
        _, seq, px, trade_id, h9_bounds, events = row
        symbol = SYMBOLS[priority]
        l53_state = data[symbol]["states"].get(int(t // 300) * 300)
        for book in books.values():
            if events or book.pos[symbol] is not None:
                book.handle_combined(
                    symbol, t, seq, px, events, l53_state, h9_bounds
                )
        ticks += 1
        day = int(t // 86400)
        if day != last_day:
            last_day = day
            print(json.dumps({"phase": "REPLAY", "period": period,
                              "day_epoch_utc": day, "ticks": ticks}), flush=True)
        next_tick = next(streams[symbol], None)
        if next_tick is not None:
            heapq.heappush(pending, (next_tick[0], priority, next_tick))
    reports = {name: book.finish() for name, book in books.items()}
    if smoke_hours:
        return {"status": "SMOKE_PASS", "ticks": ticks, "period": period,
                "scenario_counts": {name: report["counts"]
                                    for name, report in reports.items()}}
    for symbol in SYMBOLS:
        expected_candidates = sum(len(ev) for ev in data[symbol]["events"].values())
        if data[symbol]["seen_candidate"] != expected_candidates:
            raise ValueError("not all qualified source events consumed " + symbol)
    refs = validate_ledger(books, reports, period)
    summary = {
        "research": RESEARCH,
        "status": "COMPLETE_HISTORICAL_PROXY_NOT_LIVE",
        "period": period,
        "symbols": list(SYMBOLS),
        "starting_theoretical_capital_usdt": "50",
        "notional_usdt_per_coin": "10",
        "scenarios": reports,
        "reference_ledgers_sha256": refs,
        "exact_original_reference_parity": True,
        "source_rows": {s: data[s]["read_count"] for s in SYMBOLS},
        "eval_native_ticks": ticks,
        "model": ("P90L90 PREV7D historical Entry; original TRAIL035, hard stop "
                  "-10%; H9 opposite RAW causal 5m/15m union dynamic TP "
                  "vs L53 dynamic TP; second observed hard stop within rolling "
                  "48h extends global NEW Entry-only pause 48h; never flatten."),
        "fee_note": ("Original resting-maker/taker and independent conservative "
                     "taker/taker from exact closed trades; slippage/funding/"
                     "spread/latency not modeled; censored positions unmarked."),
        "financial_breakdown": money_breakdown(books),
    }
    # Writes begin ONLY after complete source/parity and financial validation.
    output.mkdir(mode=0o2770)
    snapshot = output / "source_snapshot"
    snapshot.mkdir(mode=0o2770)
    snapshot_files = (
        Path(__file__), Path(pause.__file__), Path(h9.__file__),
        Path(pause.previous.__file__), Path(pause.base.__file__),
        Path(pause.entry90.__file__), Path(pause.entry29.__file__),
        Path(pause.sep.__file__), Path(pause.sources29.__file__),
    )
    summary["source_snapshot_sha256"] = {}
    for original in snapshot_files:
        copy = snapshot / original.name
        shutil.copyfile(original, copy)
        summary["source_snapshot_sha256"][original.name] = sha256(copy)
    ledger = output / "trade_ledger.jsonl"
    with ledger.open("x", encoding="utf-8") as fh:
        for name, book in books.items():
            for row in book.trades:
                fh.write(json.dumps({**row, "portfolio_scenario": name},
                                    sort_keys=True, separators=(",", ":")) + "\n")
    summary["ledger_rows"] = sum(len(book.trades) for book in books.values())
    summary["ledger_sha256"] = sha256(ledger)
    with (output / "result.json").open("x", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return {"status": "COMPLETE", "period": period, "output": str(output),
            "parity": True,
            "net_original_fee": {
                name: details["net_original_fees"]
                for name, details in summary["financial_breakdown"].items()
            }}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=("90d", "29d"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--smoke-hours", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(run(args.period, args.output, args.smoke_hours),
                     sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
