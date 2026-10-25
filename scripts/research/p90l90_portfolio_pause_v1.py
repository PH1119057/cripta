#!/usr/bin/env python3
"""Research-only P90L90 five-coin cross-portfolio Hard Stop flatten/pause replay.

Source of truth: GitHub main; experimental extension to the immutable 90-day
P90L90 PREV7D+TRAIL035 Entry/Exit. No runtime/DB/exchange connection.

FS/actor contract:
- effective writer: cripta uid=107, not SentinelX/root;
- read: immutable P90L90 inputs, frozen states, Entry/Exit artifacts and source;
- create: one NEW results/p90l90_portfolio_pause_20261025_v1 directory;
- write: only its result.json and trade_ledger.jsonl; no rename/delete;
- parent results owned by cripta, no systemd sandbox (research job).
All code executes from exact GitHub-main source mirror; no deployment.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import heapq
import json
import os
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path
from typing import Iterator

from scripts.research import p90l90_exit_90d_v2 as base
from scripts.research.p90l90_entry_90d_v2 import (
    START_TS, END_TS, RAW_DEFAULT, FROZEN_DEFAULT, SYMBOLS, load_frozen, sha256,
)

ROOT = Path("/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024")
SCENARIOS = {"NO_PAUSE": None, "PAUSE06": 6, "PAUSE12": 12,
             "PAUSE24": 24, "PAUSE36": 36, "PAUSE48": 48}
RESEARCH = "P90L90_PORTFOLIO_HARDSTOP_PAUSE_V1"
COHORT, KIND = "PREV7D", "TRAIL035"


def preflight(output: Path) -> None:
    if os.geteuid() != 107:
        raise PermissionError("research must execute as cripta uid=107")
    parent = ROOT / "results"
    if not output.is_absolute() or output.parent.resolve() != parent.resolve():
        raise ValueError("output must be a new direct child of P90L90 data-disk results")
    if output.exists():
        raise FileExistsError("immutable output leaf already exists: " + str(output))
    if parent.stat().st_uid != 107 or not os.access(parent, os.W_OK | os.X_OK):
        raise PermissionError("exact research writer cannot create result child")
    if not (ROOT / "source_snapshot").is_dir():
        raise FileNotFoundError("P90L90 frozen source snapshot missing")


def reference(symbol: str, path: Path):
    p = path / "result.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    ledger_path = path / ("trade_ledger_" + symbol + ".jsonl")
    if sha256(ledger_path) != d["trade_ledger_sha256"]:
        raise ValueError("reference Exit ledger SHA mismatch " + symbol)
    rows = []
    with ledger_path.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row["scenario"] == "PREV7D_TRAIL035":
                rows.append(row)
    return d["scenarios"]["PREV7D_TRAIL035"], rows, d["trade_ledger_sha256"]


def inputs(symbol: str, raw_root: Path, frozen_root: Path):
    short = symbol.removesuffix("USDT")
    entry_dir = ROOT / "results" / ("entry_" + short + "_FULL90")
    manifest = json.loads((entry_dir / "manifest.json").read_text(encoding="utf-8"))
    if (manifest["research"] != "P90L90_ENTRY_90D_V2"
            or manifest["symbol"] != symbol or not manifest["complete_90d_input"]
            or manifest["frozen_l53"]["states"] != 25920):
        raise ValueError("invalid complete Entry provenance " + symbol)
    cand = entry_dir / ("candidates_" + symbol + ".jsonl")
    states_path = frozen_root / ("states_" + symbol + ".csv.gz")
    if sha256(cand) != manifest["events_sha256"]:
        raise ValueError("immutable candidate SHA changed " + symbol)
    if sha256(states_path) != manifest["frozen_l53_sha256"]:
        raise ValueError("immutable L5 state SHA changed " + symbol)
    states, _, med7, stat = load_frozen(states_path)
    if len(states) != 25920 or len(med7) != 90:
        raise ValueError("incomplete frozen PREV7D timeline " + symbol)
    events, _ = base.read_events(cand, symbol)
    original_stat, original_rows, original_sha = reference(
        symbol, ROOT / "results" / ("exit_" + short + "_FULL90"))
    return {
        "manifest": manifest, "events": events, "states": states,
        "orig_stat": original_stat, "orig_rows": original_rows,
        "orig_sha": original_sha, "raw_root": raw_root, "symbol": symbol,
        "seen_candidate": 0, "read_count": 0,
    }


def ticks(data: dict) -> Iterator[tuple[float, int, D, str]]:
    """Native Bybit row order within coin, including warmup source-order offset."""
    symbol = data["symbol"]
    seq, prev_t = 0, None
    events = data["events"]
    for item in data["manifest"]["inputs"]:
        file_path = data["raw_root"] / symbol / "public_trades" / item["file"]
        if sha256(file_path) != item["sha256"]:
            raise ValueError("public-trade file SHA mismatch " + str(file_path))
        num = 0
        with gzip.open(file_path, "rt", newline="") as fh:
            for row in csv.DictReader(fh):
                seq += 1
                num += 1
                t = float(row["timestamp"])
                if prev_t is not None and t < prev_t:
                    raise ValueError("Bybit raw timestamps regressed: " + symbol)
                prev_t = t
                if t < START_TS:
                    continue
                if t >= END_TS:
                    raise ValueError("raw out of study end: " + symbol)
                px = D(row["price"])
                incoming = events.get(seq, ())
                for e in incoming:
                    if (e["trade_id"] != row["trdMatchID"] or
                            float(e["ts"]) != t or D(e["price"]) != px):
                        raise ValueError("candidate tick source-order mismatch: " + symbol)
                data["seen_candidate"] += len(incoming)
                yield t, seq, px, row["trdMatchID"]
        if num != int(item["rows"]):
            raise ValueError("raw day count mismatch " + symbol + " " + item["file"])
    data["read_count"] = seq
    if seq != sum(int(f["rows"]) for f in data["manifest"]["inputs"]):
        raise ValueError("raw total count mismatch " + symbol)
    if data["seen_candidate"] != sum(map(len, events.values())):
        raise ValueError("unprocessed candidate ticks " + symbol)


class Portfolio:
    """Isolated portfolio state: Entry and Exit do not read other scenarios."""

    def __init__(self, hours: int | None):
        self.hours = hours
        self.pos = {symbol: None for symbol in SYMBOLS}
        self.pending = set()  # flatten at first observed tick >= triggering stop
        self.pause_until = float("-inf")
        self.count = Counter()
        self.money = defaultdict(lambda: D(0))
        self.trades = []
        self.stops = []

    def handle(self, symbol: str, t: float, seq: int, px: D,
               candidates: tuple, state: tuple | None) -> None:
        bucket = int(t // 300) * 300
        current = self.pos[symbol]
        closed_here = False
        if current is not None:
            if symbol in self.pending:
                self.pending.remove(symbol)
                nxt, ex = None, ("PORTFOLIO_FLATTEN", px, False)
            else:
                tp = None if state is None else (state[1] if current.side == "LONG" else state[0])
                nxt, ex = base.on_trade(current, px, seq, bucket, tp, KIND)
            self.pos[symbol] = nxt
            if ex is not None:
                reason, close_px, maker = ex
                gross, entry_fee, exit_fee, net = base.gross_and_net(current, close_px, maker)
                row = base.trade_record(symbol, COHORT, KIND, current, seq, t,
                                        close_px, reason, maker, gross, entry_fee, exit_fee, net)
                self.trades.append(row)
                for met, val in (("gross_usdt", gross), ("entry_fees_usdt", entry_fee),
                                 ("exit_fees_usdt", exit_fee), ("net_usdt", net),
                                 ("positive_net_usdt", max(net, D(0))),
                                 ("negative_net_usdt", min(net, D(0)))):
                    self.money[met] += val
                self.count["closed"] += 1
                self.count["close_" + reason] += 1
                self.count["win" if net > 0 else "loss" if net < 0 else "flat"] += 1
                closed_here = True
                if reason == "HARD_STOP" and self.hours is not None:
                    self.count["global_triggers"] += 1
                    self.pause_until = max(self.pause_until, t + self.hours * 3600)
                    targets = sorted(s for s, p in self.pos.items() if p is not None)
                    self.pending.update(targets)
                    self.stops.append({
                        "symbol": symbol, "ts": repr(t), "cooldown_end_ts": repr(self.pause_until),
                        "target_positions": len(targets),
                    })
        for ev in candidates:
            if not ev[COHORT]:
                continue
            self.count["qualified_candidate"] += 1
            if self.pos[symbol] is not None or closed_here:
                self.count["busy_or_same_tick"] += 1
                continue
            if t < self.pause_until or self.pending:
                self.count["blocked_global_pause"] += 1
                continue
            if state is None:
                raise ValueError("accepted Entry missing frozen L5 state: " + symbol)
            pos = base.initial_position(ev, seq, bucket, COHORT, KIND)
            tp = state[1] if pos.side == "LONG" else state[0]
            if base.take_profit_eligible(pos.side, px, tp):
                pos.tp = tp
                pos.tp_live_after_seq = seq
            self.pos[symbol] = pos
            self.count["accepted"] += 1

    def finish(self) -> dict:
        self.count["censored_open"] = sum(p is not None for p in self.pos.values())
        if self.count["qualified_candidate"] != (self.count["accepted"]
                    + self.count["busy_or_same_tick"] + self.count["blocked_global_pause"]):
            raise ValueError("candidate accounting mismatch")
        if self.count["accepted"] != self.count["closed"] + self.count["censored_open"]:
            raise ValueError("position accounting mismatch")
        if self.count["closed"] != len(self.trades):
            raise ValueError("ledger count mismatch")
        if self.count["close_PORTFOLIO_FLATTEN"] != sum(
                int(s["target_positions"]) for s in self.stops):
            raise ValueError("global trigger did not flatten exactly all positions")
        if self.money["gross_usdt"] - self.money["entry_fees_usdt"] - self.money["exit_fees_usdt"] != self.money["net_usdt"]:
            raise ValueError("fee/net mismatch")
        if self.money["positive_net_usdt"] + self.money["negative_net_usdt"] != self.money["net_usdt"]:
            raise ValueError("positive+negative mismatch")
        if sum((D(row["net_usdt"]) for row in self.trades), D(0)) != self.money["net_usdt"]:
            raise ValueError("ledger PnL sum mismatch")
        return {
            "hours": self.hours,
            "counts": dict(self.count),
            "money": {k: str(v) for k, v in self.money.items()},
            "hardstop_triggers": self.stops,
            "censored": [
                {"symbol": s, "side": p.side, "entry_ts": repr(p.entry_ts),
                 "entry_event_id": p.entry_event_id}
                for s, p in self.pos.items() if p is not None
            ],
        }


def run(output: Path, raw_root: Path = RAW_DEFAULT,
        frozen_root: Path = FROZEN_DEFAULT) -> dict:
    preflight(output)
    data = {symbol: inputs(symbol, raw_root, frozen_root) for symbol in SYMBOLS}
    books = {key: Portfolio(hours) for key, hours in SCENARIOS.items()}
    generators = {symbol: ticks(data[symbol]) for symbol in SYMBOLS}
    ordered = []
    for priority, symbol in enumerate(SYMBOLS):
        tick = next(generators[symbol], None)
        if tick is None:
            raise ValueError("empty public-trade input " + symbol)
        heapq.heappush(ordered, (tick[0], priority, tick))
    last_date = None
    while ordered:
        t, priority, (timestamp, seq, px, trade_id) = heapq.heappop(ordered)
        symbol = SYMBOLS[priority]
        bucket = int(t // 300) * 300
        event_list = data[symbol]["events"].get(seq, ())
        state = data[symbol]["states"].get(bucket)
        for book in books.values():
            # All portfolios consume the same frozen event stream, but maintain
            # independent position occupancy and global pause state.
            if event_list or book.pos[symbol] is not None:
                book.handle(symbol, t, seq, px, event_list, state)
        day = int(t // 86400)
        if day != last_date:
            last_date = day
            print(json.dumps({"status": "RUNNING", "utc_day_epoch": day,
                               "raw_tick_time": repr(t)}), flush=True)
        nxt = next(generators[symbol], None)
        if nxt is not None:
            heapq.heappush(ordered, (nxt[0], priority, nxt))

    reports = {key: book.finish() for key, book in books.items()}
    ref_rows = {symbol: data[symbol]["orig_rows"] for symbol in SYMBOLS}
    actual = {symbol: [] for symbol in SYMBOLS}
    for row in books["NO_PAUSE"].trades:
        actual[row["symbol"]].append(row)
    for symbol in SYMBOLS:
        if actual[symbol] != ref_rows[symbol]:
            raise ValueError("NO_PAUSE exact trade-by-trade baseline mismatch " + symbol)
        old_money = data[symbol]["orig_stat"]["money"]
        if sum((D(r["net_usdt"]) for r in actual[symbol]), D(0)) != D(old_money["net_usdt"]):
            raise ValueError("NO_PAUSE monetary mismatch " + symbol)
    if D(reports["NO_PAUSE"]["money"]["net_usdt"]) != sum(
            (D(data[s]["orig_stat"]["money"]["net_usdt"]) for s in SYMBOLS), D(0)):
        raise ValueError("five-coin baseline aggregate mismatch")
    report = {
        "research": RESEARCH,
        "status": "HISTORICAL_TICK_PROXY_NO_EXCHANGE_FILL",
        "period_utc": ["2026-05-18T00:00:00Z", "2026-08-16T00:00:00Z"],
        "coins": list(SYMBOLS), "cohort": COHORT, "exit": KIND,
        "notional_usdt_per_entry": "10",
        "model": "Independent multiway native Bybit public-trade tick merge; one slot per coin. A HARD_STOP on any coin triggers first-available-market-tick portfolio flatten with taker fee and a 6/12/24/36/48h global Entry embargo. One tick cannot both close and open. Cross-symbol equal timestamps ordered by SYMBOLS priority. Existing TP/protection preserved up to the triggering tick. No holding-time cap.",
        "unmodeled": ["bid_ask_spread", "slippage", "latency", "funding"],
        "all_source_rows_by_symbol": {s: data[s]["read_count"] for s in SYMBOLS},
        "entry_hash_by_symbol": {s: data[s]["manifest"]["events_sha256"] for s in SYMBOLS},
        "reference_exit_hash_by_symbol": {s: data[s]["orig_sha"] for s in SYMBOLS},
        "baseline_trade_by_trade_parity": True,
        "scenarios": reports,
    }
    output.mkdir(mode=0o770)
    ledger_path = output / "trade_ledger.jsonl"
    with ledger_path.open("x", encoding="utf-8") as out:
        for key, book in books.items():
            for rec in book.trades:
                row = {**rec, "portfolio_scenario": key}
                out.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
    report["ledger_sha256"] = sha256(ledger_path)
    report["ledger_rows"] = sum(len(book.trades) for book in books.values())
    with (output / "result.json").open("x", encoding="utf-8") as out:
        json.dump(report, out, indent=2, sort_keys=True)
        out.write("\n")
    return report


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    out = run(args.output)
    print(json.dumps({"status": "COMPLETE", "baseline_parity": True,
                      "result": str(args.output / "result.json"),
                      "scenario_results": {
                          k: {"counts": v["counts"], "money": v["money"]}
                          for k, v in out["scenarios"].items()
                      }}, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
