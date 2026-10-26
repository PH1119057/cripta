#!/usr/bin/env python3
"""Research only: original P90L90 L5-3 TP versus RAW opposite H9 TP.

Issue #218. Own independent original five-coin 90d and 29d Entry streams,
TRAIL035 + initial -10% hard stop remain unmodified. H9 union extrema
computed causally from closed 5m+15m native Bybit public-trade candles,
with original source/warmup rows and immutable manifest SHAs. No DB, LIVE,
production runtime or policy changes.

Exact server writer cripta UID107/gid109 via approved Python research rail.
Read only source, raw market files and immutable past research results.
Create/write only fresh output child under each run's /data/.../results
parent; no rename/delete, no persistent systemd sandbox or DB role.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import shutil
from collections import Counter, defaultdict
from decimal import Decimal as D, ROUND_HALF_EVEN
from pathlib import Path

from scripts.research import p90l90_29d_exit_replay_v1 as old29
from scripts.research import p90l90_entry_90d_v2 as oldentry90
from scripts.research import p90l90_entry_sep29_v1 as oldentry29
from scripts.research import p90l90_exit_90d_v2 as oldexit
from scripts.research import p90l90_portfolio_pause_v1 as oldportfolio

RESEARCH = "P90L90_H9_RAW_OPPOSITE_TP_V1"
LEAF = "p90l90_h9_raw_opposite_tp_20261026_v1"
SCENARIOS = ("L53_DYNAMIC_BASELINE", "H9_RAW_DYNAMIC", "H9_RAW_FIXED_ENTRY")
ROOTS = {
    "90d": oldportfolio.ROOT,
    "29d": old29.ROOT,
}


class TPBook(oldportfolio.Portfolio):
    """Identical Entry/trailing/native Stop; ONLY TP source varies."""

    def __init__(self, mode: str):
        if mode not in SCENARIOS:
            raise ValueError("unknown TP research mode")
        super().__init__(None)
        self.mode = mode
        self.entry_h9 = {}

    def handle_tp(self, symbol, t, seq, px, events, l53_state, h9):
        if self.mode == "L53_DYNAMIC_BASELINE":
            return super().handle(symbol, t, seq, px, events, l53_state)
        prior = self.pos[symbol]
        if prior is None and any(ev["PREV7D"] for ev in events):
            if h9 is None:
                raise ValueError("qualified H9 Entry without causal H9 state")
            self.entry_h9[symbol] = h9
        bound = (self.entry_h9.get(symbol) if self.mode == "H9_RAW_FIXED_ENTRY"
                 and prior is not None else h9)
        pseudo = (bound[0], bound[1], l53_state[2]) if (
            bound is not None and l53_state is not None) else None
        super().handle(symbol, t, seq, px, events, pseudo)
        if self.pos[symbol] is None:
            self.entry_h9.pop(symbol, None)


def sha256(path: Path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def preflight(period: str, output: Path, smoke_hours: int):
    if period not in ROOTS:
        raise ValueError("period must be 90d or 29d")
    if os.geteuid() != 107:
        raise PermissionError("effective writer must be cripta UID 107")
    if smoke_hours < 0 or smoke_hours > 24:
        raise ValueError("smoke hours 0..24")
    parent = ROOTS[period] / "results"
    if (not output.is_absolute() or output.name != LEAF
            or output.parent.resolve() != parent.resolve()):
        raise ValueError("new output must be exact approved data-disk child")
    if output.exists() or output.is_symlink():
        raise FileExistsError("immutable research output already exists")
    if parent.stat().st_uid != 107 or not os.access(parent, os.W_OK | os.X_OK):
        raise PermissionError("output parent not writable by cripta")
    return parent


def read_input(period: str, symbol: str):
    if period == "90d":
        return oldportfolio.inputs(symbol, oldentry90.RAW_DEFAULT,
                                   oldentry90.FROZEN_DEFAULT)
    return old29.read_inputs(symbol)


def h9_native_ticks(period, data):
    """Consume every raw warmup and evaluation tick, preserving source order.

    Yield H9 price only after both timeframes are causally CLOSED.
    Live H9 state changes on new 5m candle boundaries, never intrabar.
    """
    sym = data["symbol"]
    if period == "90d":
        first, end = oldentry90.START_TS, oldentry90.END_TS
        raw_base = oldentry90.RAW_DEFAULT / sym / "public_trades"
    else:
        first, end = oldentry29.START_TS, oldentry29.END_TS
        raw_base = old29.ROOT / "inputs" / sym / "public_trades"
    bars5 = oldentry90.RollingClosedBars(300, 108)
    bars15 = oldentry90.RollingClosedBars(900, 36)
    last_bucket = None
    now = None
    prev_t = None
    seq = 0
    events_seen = 0
    full_rows = 0
    for spec in data["manifest"]["inputs"]:
        path = raw_base / spec["file"]
        if sha256(path) != spec["sha256"]:
            raise ValueError("raw source SHA drift: " + str(path))
        n = 0
        with gzip.open(path, "rt", newline="") as stream:
            reader = csv.DictReader(stream)
            if not {"timestamp", "price", "trdMatchID"}.issubset(reader.fieldnames or ()):
                raise ValueError("missing native trade schema: " + str(path))
            for tick in reader:
                n += 1
                seq += 1
                t = float(tick["timestamp"])
                if prev_t is not None and t < prev_t:
                    raise ValueError("native timestamp regressed")
                prev_t = t
                px = D(tick["price"])
                if px <= 0 or not px.is_finite():
                    raise ValueError("invalid native price")
                bars5.update(t, px)
                bars15.update(t, px)
                bucket = int(t // 300) * 300
                if bucket != last_bucket:
                    last_bucket = bucket
                    five = bars5.extrema()
                    fifteen = bars15.extrema()
                    now = None
                    if five is not None and fifteen is not None:
                        if bars5.last_close_ts != bucket or (
                            bars15.last_close_ts is None or bars15.last_close_ts > bucket):
                            raise ValueError("future/incomplete H9 candle")
                        now = (min(five[0], fifteen[0]),
                               max(five[1], fifteen[1]), five, fifteen)
                if t < first:
                    continue
                if t >= end:
                    raise ValueError("outside study period")
                if now is None:
                    raise ValueError("H9 not ready for evaluation at " + str(t))
                candidates = data["events"].get(seq, ())
                for ev in candidates:
                    if (ev["trade_id"] != tick["trdMatchID"]
                            or float(ev["ts"]) != t or D(ev["price"]) != px):
                        raise ValueError("frozen Entry source-order mismatch")
                    for field, value in (
                        ("h9_5m_low", now[2][0]), ("h9_5m_high", now[2][1]),
                        ("h9_15m_low", now[3][0]), ("h9_15m_high", now[3][1])):
                        if D(ev[field]) != value:
                            raise ValueError("H9 Entry geometry parity failed " + field)
                    events_seen += 1
                yield t, seq, px, tick["trdMatchID"], now[:2], candidates
        if n != int(spec["rows"]):
            raise ValueError("source row mismatch " + str(path))
        full_rows += n
    if seq != full_rows or events_seen != sum(
            len(z) for z in data["events"].values()):
        raise ValueError("unconsumed native ticks or frozen candidates")
    data["read_count"] = full_rows
    data["seen_candidate"] = events_seen


def reference_baseline(period):
    r = ROOTS[period] / "results" / (
        "entry_only_embargo_90d_20261026_v1" if period == "90d"
        else "entry_only_embargo_29d_20261026_v1"
    )
    result = json.loads((r / "result.json").read_text(encoding="utf-8"))
    ledger = r / "trade_ledger.jsonl"
    if sha256(ledger) != result["ledger_sha256"]:
        raise ValueError("immutable baseline reference SHA drift")
    d = defaultdict(list)
    with ledger.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.pop("portfolio_scenario") == "BASELINE":
                d[row["symbol"]].append(row)
    return d, result["ledger_sha256"]


def conservative_net(row):
    """Conservative taker/taker research sensitivity, original model separate."""
    entry_fee = D(row["entry_fee_usdt"])
    exit_notional = oldexit.NOMINAL * D(row["exit_price"]) / D(row["entry_price"])
    exit_fee = (exit_notional * oldexit.TAKER).quantize(
        oldexit.MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)
    return D(row["gross_usdt"]) - entry_fee - exit_fee


def summarize(rows, counters, censor):
    by_side = {}
    for side in ("LONG", "SHORT"):
        rr = [x for x in rows if x["side"] == side]
        rs = Counter(x["exit_reason"] for x in rr)
        by_side[side] = {
            "closed": len(rr),
            "resting_tp": rs["RESTING_TP"],
            "marketable_tp": rs["MARKETABLE_TP"],
            "protected_stop": rs["PROTECTED_STOP"],
            "hard_stop": rs["HARD_STOP"],
            "net_original_fees": str(sum((D(x["net_usdt"]) for x in rr), D(0))),
            "net_taker_taker": str(sum((conservative_net(x) for x in rr), D(0))),
            "holding_hours_median": str(
                sorted(D(x["holding_seconds"]) / D(3600) for x in rr)[len(rr)//2]
            ) if rr else None,
        }
    return {"by_side": by_side, "counters": dict(counters),
            "censored_open": censor,
            "net_original_fees": str(sum((D(x["net_usdt"]) for x in rows), D(0))),
            "net_taker_taker": str(sum((conservative_net(x) for x in rows), D(0)))}


def run(period: str, output: Path, smoke_hours: int = 0):
    preflight(period, output, smoke_hours)
    first = oldentry90.START_TS if period == "90d" else oldentry29.START_TS
    end = oldentry90.END_TS if period == "90d" else oldentry29.END_TS
    cutoff = end if not smoke_hours else min(end, first + 3600 * smoke_hours)
    symbols = tuple(oldportfolio.SYMBOLS)
    books_by_symbol = {}
    report = {}
    ticks_by_symbol = {}
    for sym in symbols:
        data = read_input(period, sym)
        books = {name: TPBook(name) for name in SCENARIOS}
        ticks = 0
        last_day = None
        for t, seq, px, tid, h9, candidates in h9_native_ticks(period, data):
            if t >= cutoff:
                break
            state = data["states"].get(int(t // 300) * 300)
            for book in books.values():
                if candidates or book.pos[sym] is not None:
                    book.handle_tp(sym, t, seq, px, candidates, state, h9)
            ticks += 1
            day = int(t // 86400)
            if day != last_day:
                last_day = day
                print(json.dumps({"phase": "REPLAY", "period": period,
                                  "symbol": sym, "utc_day_epoch": day,
                                  "native_ticks": ticks}), flush=True)
        ticks_by_symbol[sym] = ticks
        books_by_symbol[sym] = books
        report[sym] = {k: book.finish() for k, book in books.items()}
    if smoke_hours:
        return {"status": "SMOKE_PASS", "period": period,
                "ticks": ticks_by_symbol,
                "counts": {s: {k: dict(v["counts"]) for k, v in res.items()}
                           for s, res in report.items()}}

    reference, ref_sha = reference_baseline(period)
    for sym, books in books_by_symbol.items():
        if books["L53_DYNAMIC_BASELINE"].trades != reference[sym]:
            raise ValueError("exact original baseline trade parity failed " + sym)
        for name in SCENARIOS:
            book = books[name]
            cnt = report[sym][name]["counts"]
            if cnt["accepted"] != cnt["closed"] + cnt["censored_open"]:
                raise ValueError("accepted trade accounting mismatch")
            if cnt.get("close_PORTFOLIO_FLATTEN", 0):
                raise ValueError("unexpected forced flatten")
    summary = {
        "research": RESEARCH,
        "status": "COMPLETE_HISTORICAL_PROXY_NOT_LIVE",
        "period": period,
        "symbols": symbols,
        "scenarios": SCENARIOS,
        "starting_capital_usdt": "50",
        "notional_usdt_per_entry": "10",
        "baseline_reference_sha256": ref_sha,
        "exact_baseline_ledger_parity": True,
        "model": ("Unchanged PREV7D candidate stream; original TRAIL035 and "
                  "-10% hard stop. L53 raw dynamic baseline, H9 opposite RAW "
                  "union max(high5,high15)/min(low5,low15) recomputed every "
                  "5m or frozen at actual Entry. Native Bybit trade ticks; "
                  "resting maker only if placed before trade. "
                  "Original maker-on-resting PnL and conservative taker/taker "
                  "PnL both reported. No slippage/funding/latency/spread."),
        "input_rows_by_coin": {
            s: books_by_symbol[s]["L53_DYNAMIC_BASELINE"].count["qualified_candidate"]
            for s in symbols
        },
        "evaluated_native_ticks": ticks_by_symbol,
        "per_symbol": {},
    }
    all_books = []
    for sym, books in books_by_symbol.items():
        summary["per_symbol"][sym] = {}
        for name, book in books.items():
            rows = book.trades
            summary["per_symbol"][sym][name] = summarize(
                rows, report[sym][name]["counts"], report[sym][name]["counts"]["censored_open"])
            all_books.append((sym, name, rows))
    summary["aggregate"] = {}
    for name in SCENARIOS:
        rows = [x for _, k, rr in all_books if k == name for x in rr]
        summary["aggregate"][name] = summarize(rows, Counter(), sum(
            report[s][name]["counts"]["censored_open"] for s in symbols))
        summary["aggregate"][name]["accepted"] = sum(
            report[s][name]["counts"]["accepted"] for s in symbols)
    # Immutable new data-disk output, written only after full source and parity QC.
    output.mkdir(mode=0o2770)
    snapshot = output / "source_snapshot"
    snapshot.mkdir(mode=0o2770)
    snapshots = (Path(__file__), Path(oldportfolio.__file__),
                 Path(oldexit.__file__), Path(oldentry90.__file__),
                 Path(oldentry29.__file__), Path(old29.__file__))
    summary["source_snapshot_sha256"] = {}
    for p in snapshots:
        dest = snapshot / p.name
        shutil.copyfile(p, dest)
        summary["source_snapshot_sha256"][p.name] = sha256(dest)
    ledger = output / "trade_ledger.jsonl"
    with ledger.open("x", encoding="utf-8") as stream:
        for sym, name, rows in all_books:
            for row in rows:
                stream.write(json.dumps({**row, "tp_scenario": name},
                                        sort_keys=True, separators=(",", ":")) + "\n")
    summary["ledger_sha256"] = sha256(ledger)
    summary["ledger_rows"] = sum(len(x) for _, _, x in all_books)
    with (output / "result.json").open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return {"status": "COMPLETE", "period": period, "output": str(output),
            "baseline_parity": True,
            "net_taker_taker": {name: summary["aggregate"][name]["net_taker_taker"]
                                for name in SCENARIOS}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=tuple(ROOTS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--smoke-hours", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(run(args.period, args.output, args.smoke_hours),
                     sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
