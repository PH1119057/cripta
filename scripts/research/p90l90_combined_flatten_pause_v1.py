#!/usr/bin/env python3
"""Research-only native-tick hybrid Hard Stop: portfolio flatten + Entry pause.

Issue #212: independent 90d & 29d replay, 4 causal hybrid scenarios
(1st/2nd Hard Stop in 48h x 24/48h pause), 4 frozen Entry-only comparators
and a no-pause reference. No Strategy/DB/Execution/LIVE operations.

Filesystem/actor: ONLY cripta UID107 writes only immutable *new* child
of existing results directories on the research data disk. Original
Bybit raw tape, frozen Entry and previous results remain read-only.
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

from scripts.research import p90l90_entry_only_hardstop_embargo_v1 as embargo
from scripts.research import p90l90_portfolio_pause_v1 as earlier
from scripts.research import p90l90_29d_exit_replay_v1 as holdout
from scripts.research import p90l90_exit_90d_v2 as exit_impl
from scripts.research import p90l90_entry_90d_v2 as entry90
from scripts.research import p90l90_entry_sep29_v1 as entry29
from scripts.research import p90l90_29d_sources_v1 as sources29

RESEARCH = "P90L90_HYBRID_HARDSTOP_FLATTEN_PAUSE_V1"
SYMBOLS = tuple(earlier.SYMBOLS)
LOOKBACK_S = 48 * 3600
SCENARIOS = {
    "BASELINE": ("baseline", 0, 0),
    "ENTRY_ONLY_FIRST_24H": ("entry", 1, 24),
    "ENTRY_ONLY_FIRST_48H": ("entry", 1, 48),
    "ENTRY_ONLY_SECOND_24H": ("entry", 2, 24),
    "ENTRY_ONLY_SECOND_48H": ("entry", 2, 48),
    "FLATTEN_FIRST_24H": ("flatten", 1, 24),
    "FLATTEN_FIRST_48H": ("flatten", 1, 48),
    "FLATTEN_SECOND_24H": ("flatten", 2, 24),
    "FLATTEN_SECOND_48H": ("flatten", 2, 48),
}
ENTRY_REFERENCE = {
    "BASELINE": "BASELINE",
    "ENTRY_ONLY_FIRST_24H": "FIRST_24H",
    "ENTRY_ONLY_FIRST_48H": "FIRST_48H",
    "ENTRY_ONLY_SECOND_24H": "SECOND_24H",
    "ENTRY_ONLY_SECOND_48H": "SECOND_48H",
}
FLATTEN_REFERENCE_90D = {
    "FLATTEN_FIRST_24H": "PAUSE24",
    "FLATTEN_FIRST_48H": "PAUSE48",
}


class Hybrid(earlier.Portfolio):
    """HARD_STOP threshold evaluated in *this* replay's preceding 48 hours."""

    def __init__(self, threshold: int, hours: int):
        if threshold not in (1, 2) or hours not in (24, 48):
            raise ValueError("unapproved combination")
        # Disable old unconditional global trigger but reuse exact native-tick
        # pending flatten execution, native Exit and Entry occupation logic.
        super().__init__(None)
        self.threshold = threshold
        self.embargo_hours = hours
        self.recent_hardstops = deque()
        self.all_hardstops = []

    def handle(self, symbol, t, seq, px, candidates, state):
        before = self.count["close_HARD_STOP"]
        # After any prior trigger, pending close is executed before same-tick
        # Entry at the target coin's FIRST native trade tick. No synthetic fill.
        super().handle(symbol, t, seq, px, candidates, state)
        if self.count["close_HARD_STOP"] == before:
            return
        if self.count["close_HARD_STOP"] != before + 1:
            raise AssertionError("one symbol/tick cannot close two Hard Stops")
        self.recent_hardstops.append(t)
        while self.recent_hardstops and t - self.recent_hardstops[0] > LOOKBACK_S:
            self.recent_hardstops.popleft()
        self.all_hardstops.append({"symbol": symbol, "ts": repr(t)})
        if len(self.recent_hardstops) < self.threshold:
            return
        self.pause_until = max(self.pause_until, t + self.embargo_hours * 3600)
        targets = sorted(s for s, pos in self.pos.items() if pos is not None)
        self.pending.update(targets)
        self.count["global_triggers"] += 1
        self.stops.append({
            "symbol": symbol,
            "ts": repr(t),
            "cooldown_end_ts": repr(self.pause_until),
            "prior_stops_in_48h_including_current": len(self.recent_hardstops),
            "target_positions": len(targets),
        })

    def finish(self):
        report = super().finish()
        if self.pending:
            raise ValueError("unresolved pending flatten at study end; cannot claim complete")
        report["trigger_threshold"] = self.threshold
        report["trigger_lookback_hours"] = 48 if self.threshold == 2 else None
        report["embargo_hours"] = self.embargo_hours
        report["all_hardstops"] = self.all_hardstops
        return report


def exact_reference(path: Path, scenarios):
    result = json.loads((path / "result.json").read_text(encoding="utf-8"))
    ledger = path / "trade_ledger.jsonl"
    if earlier.sha256(ledger) != result["ledger_sha256"]:
        raise ValueError("immutable reference ledger SHA changed: " + str(path))
    out = {key: [] for key in scenarios}
    with ledger.open(encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            scenario = record.pop("portfolio_scenario")
            if scenario in out:
                out[scenario].append(record)
    for key, rows in out.items():
        if not rows or len(rows) != result["scenarios"][key]["counts"]["closed"]:
            raise ValueError("incomplete reference " + key)
    return out, result


def preflight(period: str, output: Path, smoke_hours: int):
    if os.geteuid() != 107:
        raise PermissionError("exact research writer must be cripta UID107")
    if period not in ("90d", "29d"):
        raise ValueError("unknown research period")
    if smoke_hours < 0 or smoke_hours > 24:
        raise ValueError("smoke-hours must be 0..24")
    root = earlier.ROOT if period == "90d" else sources29.ROOT
    parent = root / "results"
    if not output.is_absolute() or output.parent.resolve() != parent.resolve():
        raise ValueError("write only one child of exact data-disk results parent")
    if output.exists() or output.is_symlink():
        raise FileExistsError("immutable output collision: " + str(output))
    st = parent.stat()
    if st.st_uid != 107 or not os.access(parent, os.W_OK | os.X_OK):
        raise PermissionError("results parent not writable by exact cripta actor")
    if output.name != "combined_hardstop_flatten_pause_20261026_v1":
        raise ValueError("unexpected output leaf name")
    return root


def make_books():
    books = {}
    for name, (method, threshold, hours) in SCENARIOS.items():
        if method == "baseline":
            books[name] = embargo.EntryEmbargo(0, None)
        elif method == "entry":
            books[name] = embargo.EntryEmbargo(threshold, hours)
        else:
            books[name] = Hybrid(threshold, hours)
    return books


def run(period: str, output: Path, smoke_hours: int = 0):
    root = preflight(period, output, smoke_hours)
    if period == "90d":
        first, end = entry90.START_TS, entry90.END_TS
        data = {sym: earlier.inputs(sym, earlier.RAW_DEFAULT, earlier.FROZEN_DEFAULT)
                for sym in SYMBOLS}
        generators = {s: earlier.ticks(data[s]) for s in SYMBOLS}
    else:
        first, end = entry29.START_TS, entry29.END_TS
        data = {sym: holdout.read_inputs(sym) for sym in SYMBOLS}
        generators = {s: holdout.native_ticks(data[s]) for s in SYMBOLS}

    books = make_books()
    ordered = []
    for priority, symbol in enumerate(SYMBOLS):
        tick = next(generators[symbol], None)
        if tick is None:
            raise ValueError("empty native public-trade tape: " + symbol)
        heapq.heappush(ordered, (tick[0], priority, tick))
    last_day = None
    count = 0
    limit = end if not smoke_hours else min(end, first + smoke_hours * 3600)
    while ordered:
        t, priority, (ts, seq, px, trade_id) = heapq.heappop(ordered)
        if t >= limit:
            break
        sym = SYMBOLS[priority]
        events = data[sym]["events"].get(seq, ())
        state = data[sym]["states"].get(int(t // 300) * 300)
        for book in books.values():
            if events or book.pos[sym] is not None:
                book.handle(sym, t, seq, px, events, state)
        count += 1
        day = int(t // 86400)
        if day != last_day:
            last_day = day
            print(json.dumps({"status": "RUNNING", "period": period,
                              "utc_day_epoch": day, "native_ticks": count}), flush=True)
        next_tick = next(generators[sym], None)
        if next_tick is not None:
            heapq.heappush(ordered, (next_tick[0], priority, next_tick))

    reports = {name: book.finish() for name, book in books.items()}
    if smoke_hours:
        return {"status": "SMOKE_PASS", "period": period,
                "ticks": count, "scenarios": {
                    name: report["counts"] for name, report in reports.items()}}

    # Strict source and candidate consumption parity.
    for sym in SYMBOLS:
        actual = data[sym]
        expected_events = sum(map(len, actual["events"].values()))
        if period == "90d" and actual["seen_candidate"] != expected_events:
            raise ValueError("90d candidate coverage mismatch " + sym)
        if period == "29d" and actual["seen_events"] != expected_events:
            raise ValueError("29d candidate coverage mismatch " + sym)

    previous_entry, entry_manifest = exact_reference(
        root / "results" / ("entry_only_embargo_90d_20261026_v1"
                            if period == "90d" else "entry_only_embargo_29d_20261026_v1"),
        set(ENTRY_REFERENCE.values()),
    )
    archived_flatten = {}
    if period == "90d":
        archived_flatten, _ = exact_reference(
            root / "results" / "p90l90_portfolio_pause_20261025_v1",
            set(FLATTEN_REFERENCE_90D.values()),
        )
    for name, reference_name in ENTRY_REFERENCE.items():
        if books[name].trades != previous_entry[reference_name]:
            raise ValueError("exact Entry-only trade ledger parity failed " + name)
    for name, reference_name in FLATTEN_REFERENCE_90D.items():
        if books[name].trades != archived_flatten[reference_name]:
            raise ValueError("original old forced-flatten parity failed " + name)

    for name, report in reports.items():
        counts = report["counts"]
        if name.startswith("ENTRY_ONLY") or name == "BASELINE":
            if counts.get("close_PORTFOLIO_FLATTEN", 0):
                raise AssertionError("Entry-only scenario unexpectedly flattened")
        if counts["accepted"] != counts["closed"] + counts["censored_open"]:
            raise AssertionError("accepted accounting mismatch")

    summary = {
        "research": RESEARCH,
        "status": "COMPLETE_HISTORICAL_PROXY_NOT_LIVE",
        "period": period,
        "period_utc": (["2026-05-18T00:00:00Z", "2026-08-16T00:00:00Z"]
                       if period == "90d" else
                       ["2026-09-21T00:00:00Z", "2026-10-20T00:00:00Z"]),
        "symbols": list(SYMBOLS),
        "notional_usdt_per_coin": "10",
        "theoretical_capital_usdt": "50",
        "model": ("Nine independent causal Bybit native trade tick replays: "
                  "baseline, four Entry-only references, four combined "
                  "portfolio FLATTEN and 24/48h global Entry embargoes. "
                  "Flatten other open positions only at each target's next "
                  "native public trade tick, never synthetic fill. "
                  "Only actual scenario HARD_STOP within preceding 48h "
                  "may trigger the SECOND threshold; first does nothing "
                  "for SECOND variants. Original TRAIL035/TP/-10% stop "
                  "and all original Entry states remain frozen."),
        "limits": ["slippage", "spread", "funding", "latency", "live-fill equivalence"],
        "exact_entry_only_reference_parity": True,
        "exact_first_flatten_90d_reference_parity": period == "90d",
        "reference_entry_sha256": entry_manifest["ledger_sha256"],
        "scenarios": reports,
        "net_pnl_usdt": {k: v["money"]["net_usdt"] for k, v in reports.items()},
        "source_rows_by_symbol": {
            sym: (data[sym]["read_count"] if period == "90d" else data[sym]["seen_rows"])
            for sym in SYMBOLS
        },
        "candidate_sha256": {
            sym: data[sym]["manifest"]["events_sha256"] for sym in SYMBOLS
        },
    }
    # Mutations from here occur ONLY beneath the fresh data-disk output leaf.
    output.mkdir(mode=0o2770)
    snapshot = output / "source_snapshot"
    snapshot.mkdir(mode=0o2770)
    files = [
        Path(__file__), Path(embargo.__file__), Path(earlier.__file__),
        Path(holdout.__file__), Path(exit_impl.__file__),
        Path(entry90.__file__), Path(entry29.__file__), Path(sources29.__file__),
    ]
    summary["source_snapshot_sha256"] = {}
    for path in files:
        dst = snapshot / path.name
        shutil.copyfile(path, dst)
        summary["source_snapshot_sha256"][path.name] = earlier.sha256(dst)
    ledger = output / "trade_ledger.jsonl"
    with ledger.open("x", encoding="utf-8") as f:
        for name, book in books.items():
            for row in book.trades:
                f.write(json.dumps({**row, "portfolio_scenario": name},
                                   sort_keys=True, separators=(",", ":")) + "\n")
    summary["ledger_sha256"] = earlier.sha256(ledger)
    summary["ledger_rows"] = sum(len(book.trades) for book in books.values())
    with (output / "result.json").open("x", encoding="utf-8") as f:
        json.dump(summary, f, sort_keys=True, indent=2)
        f.write("\n")
    return {"status": "COMPLETE", "period": period, "result": str(output / "result.json"),
            "reference_parity": True, "net_pnl_usdt": summary["net_pnl_usdt"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", required=True, choices=("90d", "29d"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--smoke-hours", default=0, type=int)
    args = parser.parse_args()
    print(json.dumps(run(args.period, args.output, args.smoke_hours),
                     sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
