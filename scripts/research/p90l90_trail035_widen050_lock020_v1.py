#!/usr/bin/env python3
"""Research-only global five-symbol P90L90: widen 0.35pp trail to 0.50pp
once its protected stop reaches +0.20% gross from Entry. Never loosen
the previously attained stop. All raw events causally ordered, exact
historical PREV7D, L5-3 opposite TP and global SECOND48. Neither
Exchange-native reconfiguration nor a REAL Strategy equivalence is
claimed. No production/DB/Exchange mutation.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
import shutil
from collections import Counter, deque
from decimal import Decimal as D
from pathlib import Path

from scripts.research import p90l90_entry_only_hardstop_embargo_v1 as old

RESEARCH = "P90L90_TRAIL035_WIDEN050_LOCK020_V1"
LEAF = "p90l90_trail035_widen050_lock020_20261107_v1"
ROOT = old.previous.ROOT
BASELINE = "LEGACY_L53_SECOND48"
VARIANT = "TRAIL035_WIDEN050_LOCK020"
SYMBOLS = tuple(old.SYMBOLS)
COHORT = old.previous.COHORT
HARDSTOP_WINDOW_S = 48 * 3600
ACTIVATION_PCT = D("0.5")
TRAIL_INITIAL_PCT = D("0.35")
TRAIL_WIDE_PCT = D("0.50")
STOP_SWITCH_PCT = D("0.20")

NET_HISTOGRAM = (
    ("LOSS", None, D("0")),
    ("0_TO_0P5", D("0"), D("0.5")),
    ("0P5_TO_1", D("0.5"), D("1")),
    ("1_TO_1P5", D("1"), D("1.5")),
    ("1P5_TO_2", D("1.5"), D("2")),
    ("2_TO_2P5", D("2"), D("2.5")),
    ("GE_2P5", D("2.5"), None),
)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def widen_trail_on_trade(pos, px: D, seq: int, bucket: int,
                        candidate_tp: D | None):
    """First-event causal Exit priority; a stop update acts on later ticks."""
    base = old.base
    if seq <= pos.entry_seq:
        return pos, None
    if base.hits_stop(pos.side, px, pos.stop):
        return None, ("PROTECTED_STOP" if pos.protected else "HARD_STOP", px, False)
    if (pos.tp is not None and seq > pos.tp_live_after_seq
            and base.hits_tp(pos.side, px, pos.tp)):
        return None, ("RESTING_TP", pos.tp, True)
    if bucket != pos.state_bucket:
        pos.state_bucket = bucket
        if candidate_tp is not None and base.take_profit_eligible(
                pos.side, pos.entry, candidate_tp):
            if base.hits_tp(pos.side, px, candidate_tp):
                return None, ("MARKETABLE_TP", px, False)
            pos.tp = candidate_tp
            pos.tp_live_after_seq = seq
    current_favorable = base.favorable(pos.side, pos.entry, px)
    if current_favorable > pos.best_favorable:
        pos.best_favorable = current_favorable
    if pos.best_favorable < ACTIVATION_PCT:
        return pos, None
    hwm = pos.best_favorable
    # Before the switch use 0.35pp even when a single tick jumps over +0.20 lock.
    # A later reconfiguration must not relax the already reached stop.
    distance = TRAIL_WIDE_PCT if pos.widened else TRAIL_INITIAL_PCT
    proposed_pct = hwm - distance
    proposed = base.threshold_price(pos.side, pos.entry, proposed_pct)
    tighter = (proposed > pos.stop if pos.side == "LONG" else proposed < pos.stop)
    if tighter:
        pos.stop = proposed
        pos.protected = True
        pos.protection_seq = seq
    if not pos.widened:
        actual_locked = base.favorable(pos.side, pos.entry, pos.stop)
        if actual_locked >= STOP_SWITCH_PCT:
            pos.widened = True
    return pos, None

class WidenTrailBook(old.EntryEmbargo):
    """Same original global second-HardStop/48h ENTRY ONLY embargo."""

    def __init__(self):
        super().__init__(2, 48)
        self.diagnostics = []
        self.protection_activations = 0
        self.stop_updates = 0
        self.widen_activations = 0

    def handle(self, symbol, t, seq, px, candidates, state):
        # Replicate baseline event precedence without mutating shared functions.
        bucket = int(t // 300) * 300
        current = self.pos[symbol]
        closed_here = False
        hardstops_before = self.count["close_HARD_STOP"]
        if current is not None:
            prior_protected = current.protected
            prior_stop = current.stop
            prior_widened = current.widened
            if current.tp is None and state is None:
                candidate_tp = None
            else:
                candidate_tp = (None if state is None else
                                (state[1] if current.side == "LONG" else state[0]))
            nxt, ex = widen_trail_on_trade(
                current, px, seq, bucket, candidate_tp
            )
            self.pos[symbol] = nxt
            if current.stop != prior_stop:
                assert current.protected
                self.stop_updates += 1
            if not prior_widened and current.widened:
                assert current.protected
                assert old.base.favorable(current.side, current.entry, current.stop) >= STOP_SWITCH_PCT
                self.widen_activations += 1
            if not prior_protected and current.protected:
                self.protection_activations += 1
            if ex is not None:
                reason, close_px, maker = ex
                gross, entry_fee, exit_fee, net = old.base.gross_and_net(
                    current, close_px, maker
                )
                row = old.base.trade_record(
                    symbol, COHORT, "WIDEN035050", current, seq, t,
                    close_px, reason, maker,
                    gross, entry_fee, exit_fee, net
                )
                self.trades.append(row)
                self.diagnostics.append({
                    "symbol": symbol,
                    "side": current.side,
                    "candidate_event_id": current.entry_event_id,
                    "entry_ts": repr(current.entry_ts),
                    "exit_ts": repr(t),
                    "exit_reason": reason,
                    "max_favorable_observed_pct": str(max(
                        current.best_favorable,
                        old.base.favorable(current.side, current.entry, px)
                    )),
                    "trailing_armed": current.protected,
                    "widened": current.widened,
                    "last_trail_update_seq": current.protection_seq,
                    "protection_stop": str(current.stop),
                    "protected_profit_at_exit_pct": (
                        None if not current.protected else str(
                            old.base.favorable(current.side, current.entry, current.stop)
                        )
                    ),
                    "tp_target_at_exit": (
                        None if current.tp is None else str(current.tp)
                    ),
                })
                for met, value in (
                    ("gross_usdt", gross),
                    ("entry_fees_usdt", entry_fee),
                    ("exit_fees_usdt", exit_fee),
                    ("net_usdt", net),
                    ("positive_net_usdt", max(net, D(0))),
                    ("negative_net_usdt", min(net, D(0))),
                ):
                    self.money[met] += value
                self.count["closed"] += 1
                self.count["close_" + reason] += 1
                self.count[
                    "win" if net > 0 else "loss" if net < 0 else "flat"
                ] += 1
                closed_here = True
        # Global hard-stop pause applies AFTER same-tick candidates,
        # i.e. after processing same-tick candidates. Preserve that ordering.
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
                raise ValueError("accepted Entry missing frozen L53 state")
            pos = old.base.initial_position(ev, seq, bucket, COHORT, "WIDEN035050")
            pos.widened = False
            target = state[1] if pos.side == "LONG" else state[0]
            if old.base.take_profit_eligible(pos.side, px, target):
                pos.tp = target
                pos.tp_live_after_seq = seq
            self.pos[symbol] = pos
            self.count["accepted"] += 1
        if self.count["close_HARD_STOP"] != hardstops_before:
            self.all_stops.append({"ts": repr(t), "symbol": symbol})
            self.last_stops.append(t)
            while (self.last_stops and
                   t - self.last_stops[0] > HARDSTOP_WINDOW_S):
                self.last_stops.popleft()
            if len(self.last_stops) >= 2:
                self.pause_until = max(self.pause_until, t + 48 * 3600)
                self.count["pause_triggers"] += 1
                self.stops.append({
                    "symbol": symbol, "ts": repr(t),
                    "cooldown_end_ts": repr(self.pause_until),
                    "prior_stops_in_48h_including_current": len(self.last_stops),
                    "target_positions": 0,
                })

    def finish(self):
        report = super().finish()
        assert not self.pending
        assert self.count.get("close_PORTFOLIO_FLATTEN", 0) == 0
        report["model"] = "TRAIL035_WIDEN050_AT_PROTECTED_PLUS020_NO_LOOSEN"
        report["protection_activations"] = self.protection_activations
        report["stop_updates"] = self.stop_updates
        report["widen_activations"] = self.widen_activations
        assert len(self.diagnostics) == self.count["closed"]
        return report


def preflight(output: Path, smoke_hours: int) -> None:
    if os.geteuid() != 107:
        raise PermissionError("effective actor must be cripta uid107")
    if not 0 <= smoke_hours <= 24:
        raise ValueError("bounded 0..24 smoke hours")
    parent = ROOT / "results"
    if (not output.is_absolute() or output.name != LEAF or
            output.parent.resolve() != parent.resolve()):
        raise ValueError("unapproved result location")
    if output.exists() or output.is_symlink():
        raise FileExistsError("immutable output collision")
    if parent.stat().st_uid != 107 or not os.access(
            parent, os.W_OK | os.X_OK):
        raise PermissionError("research data-disk parent not writable")


def reference_224():
    ref = ROOT / "results/p90l90_h9_second48_embargo_20261026_v1"
    manifest = json.loads((ref / "result.json").read_text())
    path = ref / "trade_ledger.jsonl"
    digest = sha256(path)
    if digest != manifest["ledger_sha256"]:
        raise ValueError("reference #224 SHA drift")
    matched = {sym: [] for sym in SYMBOLS}
    for line in path.open():
        row = json.loads(line)
        if row.pop("portfolio_scenario") == "L53_SECOND48":
            matched[row["symbol"]].append(row)
    return matched, digest


def histogram(rows):
    out = {}
    for symbol in SYMBOLS:
        group = [x for x in rows if x["symbol"] == symbol]
        bins = {name: 0 for name, _, _ in NET_HISTOGRAM}
        for rec in group:
            pct = D(rec["net_usdt"]) / D("10") * D(100)
            for name, low, upper in NET_HISTOGRAM:
                if (low is None or pct >= low) and (upper is None or pct < upper):
                    bins[name] += 1
                    break
            else:
                raise ValueError("histogram bin unreachable")
        if sum(bins.values()) != len(group):
            raise ValueError("histogram count mismatch")
        out[symbol] = {
            "closed": len(group), "bins": bins,
            "net_usdt": str(sum((D(r["net_usdt"]) for r in group), D(0))),
            "best_exit_pct": (
                str(max(D(r["net_usdt"]) for r in group) * D(10))
                if group else None
            ),
            "tp_closes": sum(r["exit_reason"] in
                             ("RESTING_TP", "MARKETABLE_TP") for r in group),
            "protected_closes": sum(r["exit_reason"] == "PROTECTED_STOP"
                                    for r in group),
            "hardstops": sum(r["exit_reason"] == "HARD_STOP" for r in group),
        }
    return out


def run(output: Path, smoke_hours: int = 0):
    preflight(output, smoke_hours)
    data = {
        sym: old.previous.inputs(
            sym, old.previous.RAW_DEFAULT, old.previous.FROZEN_DEFAULT
        ) for sym in SYMBOLS
    }
    streams = {sym: old.previous.ticks(data[sym]) for sym in SYMBOLS}
    books = {
        BASELINE: old.EntryEmbargo(2, 48),
        VARIANT: WidenTrailBook(),
    }
    heap = []
    for idx, sym in enumerate(SYMBOLS):
        item = next(streams[sym], None)
        if item is None:
            raise ValueError("empty source " + sym)
        heapq.heappush(heap, (item[0], idx, item))
    first, end = old.entry90.START_TS, old.entry90.END_TS
    cutoff = end if smoke_hours == 0 else min(end, first + 3600 * smoke_hours)
    n_ticks = 0
    day = None
    while heap:
        t, idx, (ts, seq, px, trade_id) = heapq.heappop(heap)
        if t >= cutoff:
            break
        sym = SYMBOLS[idx]
        cand = data[sym]["events"].get(seq, ())
        state = data[sym]["states"].get(int(t // 300) * 300)
        for name, book in books.items():
            if cand or book.pos[sym] is not None:
                book.handle(sym, t, seq, px, cand, state)
        n_ticks += 1
        date = int(t // 86400)
        if date != day:
            day = date
            print(json.dumps({
                "phase": "REPLAY", "day_epoch_utc": day,
                "evaluated_native_ticks": n_ticks
            }), flush=True)
        next_tick = next(streams[sym], None)
        if next_tick is not None:
            heapq.heappush(heap, (next_tick[0], idx, next_tick))
    reports = {name: book.finish() for name, book in books.items()}
    if smoke_hours:
        return {
            "status": "SMOKE_PASS", "hours": smoke_hours,
            "ticks": n_ticks, "scenarios": {
                name: report["counts"] for name, report in reports.items()
            }
        }
    for sym in SYMBOLS:
        d = data[sym]
        if d["seen_candidate"] != sum(map(len, d["events"].values())):
            raise ValueError("candidate source count mismatch " + sym)
    old_rows, reference_sha = reference_224()
    for sym in SYMBOLS:
        generated = [r for r in books[BASELINE].trades
                     if r["symbol"] == sym]
        if generated != old_rows[sym]:
            raise ValueError("EXACT #224 LEGACY parity failed " + sym)
    fin = {}
    for name, book in books.items():
        rows = book.trades
        net = sum((D(r["net_usdt"]) for r in rows), D(0))
        if net != D(reports[name]["money"]["net_usdt"]):
            raise ValueError("financial net drift " + name)
        fin[name] = {
            "net_usdt": str(net),
            "counts": reports[name]["counts"],
            "histogram": histogram(rows),
        }
    summary = {
        "research": RESEARCH, "status": "COMPLETE_HISTORICAL_PROXY_NOT_LIVE",
        "period": "90d",
        "symbols": list(SYMBOLS),
        "starting_theoretical_capital_usdt": "50",
        "nominal_usdt_per_coin": "10",
        "evaluated_native_ticks": n_ticks,
        "baseline_parity": True,
        "reference_224_ledger_sha256": reference_sha,
        "model": (
            "Five-symbol global-SECOND48 PREV7D control TRAIL035 versus "
            "TRAIL035_WIDEN050: after first +0.5% favorable use continuous "
            "0.35 percentage point trail, and once the stop itself reaches "
            "+0.20% gross relative to Entry switch distance once to 0.50pp "
            "while preserving the previous stop floor (never loosen). "
            "Switch is triggered at favorable MFE approximately +0.55%, "
            "not after a price retracement to +0.2%. Recomputed trail "
            "effective from NEXT native tick only, original raw L5-3 TP "
            "and -10% hard stop; one global five-symbol SECOND48 pauses "
            "new entries 48h without closing open positions. Historical "
            "TP activation reference differs from current REAL attached TP."
        ),
        "unmodeled": [
            "spread", "slippage", "funding", "latency",
            "historical_ticksize_changes", "true_exchange_fills"
        ],
        "input_manifest_sha256": {
            s: data[s]["manifest"]["events_sha256"] for s in SYMBOLS
        },
        "scenarios": reports,
        "financial_breakdown": fin,
    }
    output.mkdir(mode=0o2770)
    snapshot = output / "source_snapshot"
    snapshot.mkdir(mode=0o2770)
    paths = (
        Path(__file__), Path(old.__file__), Path(old.previous.__file__),
        Path(old.base.__file__), Path(old.entry90.__file__),
        Path(old.entry29.__file__), Path(old.sep.__file__),
        Path(old.sources29.__file__),
    )
    summary["source_snapshot_sha256"] = {}
    for source in paths:
        dest = snapshot / source.name
        shutil.copyfile(source, dest)
        summary["source_snapshot_sha256"][source.name] = sha256(dest)
    files = {
        "trade_ledger.jsonl": (
            {**r, "portfolio_scenario": name} for name, b in books.items()
            for r in b.trades
        ),
        "position_diagnostics.jsonl": (
            {**r, "portfolio_scenario": VARIANT}
            for r in books[VARIANT].diagnostics
        ),
    }
    for name, records in files.items():
        dest = output / name
        count = 0
        with dest.open("x", encoding="utf-8") as stream:
            for row in records:
                stream.write(json.dumps(
                    row, sort_keys=True, separators=(",", ":")) + "\n")
                count += 1
        summary[name] = {"rows": count, "sha256": sha256(dest)}
    with (output / "result.json").open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return {
        "status": "COMPLETE", "period": "90d",
        "baseline_parity": True, "output": str(output),
        "native_ticks": n_ticks,
        "net": {k: v["net_usdt"] for k, v in fin.items()},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--smoke-hours", type=int, default=0)
    opts = ap.parse_args()
    print("RESULT", json.dumps(run(opts.output, opts.smoke_hours),
                               sort_keys=True), flush=True)
