#!/usr/bin/env python3
"""Research-only P90L90 causal global Entry embargo; never flatten other positions.

Owner-approved hypothesis (Issue #210): first Hard Stop, or the second within
a rolling 48 hours, pauses only NEW Entry for 6/12/24/48 hours. Existing
positions continue unchanged TRAIL035 / native TP / -10% HARD_STOP exits.

Writer: cripta uid 107. Reads immutable five-coin Bybit data and reference
artifacts. Creates only a new, direct child of the selected data-disk
results directory and files inside that child. No runtime/DB/exchange action.
"""
from __future__ import annotations

import argparse
import heapq
import json
import os
import shutil
from collections import Counter, deque
from decimal import Decimal as D
from pathlib import Path

from scripts.research import p90l90_portfolio_pause_v1 as previous
from scripts.research import p90l90_29d_exit_replay_v1 as sep
from scripts.research import p90l90_exit_90d_v2 as base
from scripts.research import p90l90_entry_90d_v2 as entry90
from scripts.research import p90l90_entry_sep29_v1 as entry29
from scripts.research import p90l90_29d_sources_v1 as sources29

RESEARCH = "P90L90_ENTRY_ONLY_HARDSTOP_EMBARGO_V1"
FIRST_TO_SECOND_WINDOW_S = 48 * 3600
HOURS = (6, 12, 24, 48)
SYMBOLS = tuple(previous.SYMBOLS)
SCENARIOS = {"BASELINE": (0, None)}
SCENARIOS.update({f"FIRST_{h:02d}H": (1, h) for h in HOURS})
SCENARIOS.update({f"SECOND_{h:02d}H": (2, h) for h in HOURS})


class EntryEmbargo(previous.Portfolio):
    """Per-scenario Hard Stop history, portfolio pause; no cross-position close."""

    def __init__(self, threshold: int, hours: int | None):
        if threshold not in (0, 1, 2) or ((threshold == 0) != (hours is None)):
            raise ValueError("invalid embargo specification")
        if hours is not None and hours not in HOURS:
            raise ValueError("unapproved pause duration")
        # None explicitly disables the legacy global PORTFOLIO_FLATTEN.
        super().__init__(None)
        self.threshold = threshold
        self.embargo_hours = hours
        self.last_stops = deque()
        self.all_stops = []

    def handle(self, symbol, t, seq, px, candidates, state):
        before = self.count["close_HARD_STOP"]
        # Base handles exit before any entry, suppresses candidates while
        # pause_until is active, and enforces same-tick no-reentry.
        super().handle(symbol, t, seq, px, candidates, state)
        if self.count["close_HARD_STOP"] == before:
            return
        self.all_stops.append({"ts": repr(t), "symbol": symbol})
        self.last_stops.append(t)
        while self.last_stops and t - self.last_stops[0] > FIRST_TO_SECOND_WINDOW_S:
            self.last_stops.popleft()
        if self.threshold and len(self.last_stops) >= self.threshold:
            self.pause_until = max(self.pause_until, t + self.embargo_hours * 3600)
            self.count["pause_triggers"] += 1
            # Legacy base.finish() checks that stops' target_positions
            # equal flatten counts: zero explicitly means NO FLATTEN.
            self.stops.append({
                "symbol": symbol, "ts": repr(t),
                "cooldown_end_ts": repr(self.pause_until),
                "prior_stops_in_48h_including_current": len(self.last_stops),
                "target_positions": 0,
            })

    def finish(self):
        report = super().finish()
        if self.count["close_PORTFOLIO_FLATTEN"] != 0 or self.pending:
            raise ValueError("Entry-only research must never flatten")
        report["hours"] = self.embargo_hours
        report["trigger_threshold"] = self.threshold
        report["trigger_lookback_hours"] = 48 if self.threshold == 2 else None
        report["observed_hardstop_events"] = self.all_stops
        return report


def preflight(period: str, output: Path, smoke_hours: int):
    if os.geteuid() != 107:
        raise PermissionError("research actor must be cripta uid107")
    if period not in ("90d", "29d"):
        raise ValueError("period must be 90d or 29d")
    root = previous.ROOT if period == "90d" else sources29.ROOT
    parent = root / "results"
    if not output.is_absolute() or output.parent.resolve() != parent.resolve():
        raise ValueError("output must be a direct results child for selected period")
    if output.exists() or output.is_symlink():
        raise FileExistsError("immutable output collision: " + str(output))
    if parent.stat().st_uid != 107 or not os.access(parent, os.W_OK | os.X_OK):
        raise PermissionError("exact output parent not writable as cripta")
    if smoke_hours < 0 or smoke_hours > 24:
        raise ValueError("bounded smoke must be 0..24 hours")
    return root


def read_reference_29():
    folder = sources29.ROOT / "results" / "compare_exit_sep29_v1"
    result = json.loads((folder / "result.json").read_text())
    ledger = folder / "trade_ledger.jsonl"
    if sources29.hash_file(ledger) != result["ledger_sha256"]:
        raise ValueError("29d baseline trade ledger SHA drift")
    original = {sym: [] for sym in SYMBOLS}
    with ledger.open() as fh:
        for line in fh:
            row = json.loads(line)
            if row["portfolio_scenario"] == "BASELINE":
                sym = row["symbol"]
                original[sym].append({k: v for k, v in row.items()
                                       if k != "portfolio_scenario"})
    if sum(map(len, original.values())) != result["scenarios"]["BASELINE"]["counts"]["closed"]:
        raise ValueError("29d reference trade count mismatch")
    return original, result


def run(period: str, output: Path, smoke_hours: int = 0):
    root = preflight(period, output, smoke_hours)
    if period == "90d":
        first, end = entry90.START_TS, entry90.END_TS
        data = {s: previous.inputs(s, previous.RAW_DEFAULT, previous.FROZEN_DEFAULT)
                for s in SYMBOLS}
        generators = {s: previous.ticks(data[s]) for s in SYMBOLS}
        expected = {s: data[s]["orig_rows"] for s in SYMBOLS}
        reference = None
    else:
        first, end = entry29.START_TS, entry29.END_TS
        data = {s: sep.read_inputs(s) for s in SYMBOLS}
        generators = {s: sep.native_ticks(data[s]) for s in SYMBOLS}
        expected, reference = read_reference_29()

    books = {name: EntryEmbargo(threshold, hours)
             for name, (threshold, hours) in SCENARIOS.items()}
    ordered = []
    for priority, symbol in enumerate(SYMBOLS):
        tick = next(generators[symbol], None)
        if tick is None:
            raise ValueError("empty Bybit tick source " + symbol)
        heapq.heappush(ordered, (tick[0], priority, tick))

    limit = end if smoke_hours == 0 else min(end, first + smoke_hours * 3600)
    previous_day = None
    ticks_processed = 0
    while ordered:
        t, priority, (timestamp, seq, px, trade_id) = heapq.heappop(ordered)
        if t >= limit:
            break
        symbol = SYMBOLS[priority]
        event = data[symbol]["events"].get(seq, ())
        state = data[symbol]["states"].get(int(t // 300) * 300)
        for book in books.values():
            if event or book.pos[symbol] is not None:
                book.handle(symbol, t, seq, px, event, state)
        ticks_processed += 1
        day = int(t // 86400)
        if day != previous_day:
            previous_day = day
            print(json.dumps({"phase": "REPLAY", "period": period,
                              "utc_day_epoch": day, "ticks_so_far": ticks_processed}),
                  flush=True)
        nxt = next(generators[symbol], None)
        if nxt is not None:
            heapq.heappush(ordered, (nxt[0], priority, nxt))

    reports = {name: book.finish() for name, book in books.items()}
    if smoke_hours:
        return {"status": "SMOKE_PASS", "period": period,
                "hours": smoke_hours, "ticks": ticks_processed,
                "scenarios": {k: v["counts"] for k, v in reports.items()}}

    for symbol in SYMBOLS:
        d = data[symbol]
        if period == "90d":
            if d["seen_candidate"] != sum(map(len, d["events"].values())):
                raise ValueError("90d missing Entry event " + symbol)
        else:
            if d["seen_events"] != sum(map(len, d["events"].values())):
                raise ValueError("29d missing Entry event " + symbol)
            if d["seen_rows"] != sum(x["rows"] for x in d["manifest"]["inputs"]):
                raise ValueError("29d raw row accounting " + symbol)

    actual = {symbol: [] for symbol in SYMBOLS}
    for row in books["BASELINE"].trades:
        actual[row["symbol"]].append(row)
    for symbol in SYMBOLS:
        if actual[symbol] != expected[symbol]:
            raise ValueError("BASELINE exact trade-by-trade parity failed " + symbol)
    if period == "90d":
        expected_net = sum((D(data[s]["orig_stat"]["money"]["net_usdt"])
                            for s in SYMBOLS), D(0))
    else:
        expected_net = D(reference["scenarios"]["BASELINE"]["money"]["net_usdt"])
    if D(reports["BASELINE"]["money"]["net_usdt"]) != expected_net:
        raise ValueError("BASELINE aggregate money parity failed")

    result = {
        "research": RESEARCH, "period": period, "status": "COMPLETE_HISTORICAL_PROXY_NOT_LIVE",
        "period_utc": (["2026-05-18T00:00:00Z", "2026-08-16T00:00:00Z"]
                       if period == "90d" else
                       ["2026-09-21T00:00:00Z", "2026-10-20T00:00:00Z"]),
        "symbols": list(SYMBOLS), "notional_usdt_per_coin": "10",
        "starting_theoretical_capital_usdt": "50", "entry": "P90L90 PREV7D",
        "original_exit": "TRAIL035, native opposite TP, hardstop -10%",
        "model": ("Independent native Bybit tick merge, one slot per coin/scenario; "
                  "an actual scenario HARD_STOP triggers only a global new-Entry "
                  "embargo, never forcibly closes remaining positions; first stop "
                  "or >=2 in preceding 48h; durations 6/12/24/48h."),
        "unmodeled": ["bid_ask_spread", "slippage", "funding", "latency"],
        "baseline_trade_by_trade_parity": True,
        "scenarios": reports,
        "net_pnl_usdt": {k: v["money"]["net_usdt"] for k, v in reports.items()},
        "source_rows": {s: (data[s]["read_count"] if period == "90d"
                            else data[s]["seen_rows"]) for s in SYMBOLS},
        "candidate_sha256": {s: data[s]["manifest"]["events_sha256"] for s in SYMBOLS},
    }
    output.mkdir(mode=0o2770)
    snapshot = output / "source_snapshot"
    snapshot.mkdir(mode=0o2770)
    source_files = [Path(__file__), Path(previous.__file__), Path(base.__file__),
                    Path(entry90.__file__), Path(entry29.__file__),
                    Path(sep.__file__), Path(sources29.__file__)]
    result["source_snapshot_sha256"] = {}
    hash_file = previous.sha256 if period == "90d" else sources29.hash_file
    for path in source_files:
        dst = snapshot / path.name
        shutil.copyfile(path, dst)
        result["source_snapshot_sha256"][path.name] = hash_file(dst)
    ledger = output / "trade_ledger.jsonl"
    with ledger.open("x") as fh:
        for name, book in books.items():
            for row in book.trades:
                fh.write(json.dumps({**row, "portfolio_scenario": name},
                                    sort_keys=True, separators=(",", ":")) + "\n")
    result["ledger_sha256"] = hash_file(ledger)
    result["ledger_rows"] = sum(len(b.trades) for b in books.values())
    with (output / "result.json").open("x") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return {"status": "COMPLETE", "period": period, "result": str(output / "result.json"),
            "net_pnl_usdt": result["net_pnl_usdt"], "baseline_parity": True}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--period", required=True, choices=("90d", "29d"))
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--smoke-hours", type=int, default=0)
    a = p.parse_args()
    print(json.dumps(run(a.period, a.output, a.smoke_hours),
                     sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
