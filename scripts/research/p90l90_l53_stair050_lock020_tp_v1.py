#!/usr/bin/env python3
"""Issue #241 research-only P90L90: causal step locks at +0.50pp MFE increments.

Exactly two independent 90-day portfolios: unchanged L53 SECOND48 TRAIL035
reference and owner-hypothesis stair-step locks. First reached +0.50 percent
locks +0.20 percent, reached +1.0 locks +0.5, +1.5 locks +1.0,
+2.0 locks +1.5, thereafter each additional 0.5 percent of favorable
MFE locks an extra 0.5 percent. Stops never loosen, update only on
observed trades and fill no earlier than the following native trade.
No continuous trailing between steps. READ immutable Bybit public trades,
Entry candidates, causal frozen L53 state and reference #224. Only CREATE
a new immutable research result child under approved /data volume as cripta107.
Never imports/changes trading runtime, live policy, PostgreSQL or exchange.
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

RESEARCH = "P90L90_L53_STAIR050_LOCK020_TP_V1"
LEAF = "p90l90_l53_stair050_lock020_tp_20261027_v1"
ROOT = old.previous.ROOT
BASELINE = "LEGACY_L53_SECOND48"
VARIANT = "STAIR050_LOCK020_DYNAMIC_TP"
SYMBOLS = tuple(old.SYMBOLS)
COHORT = old.previous.COHORT
HARDSTOP_WINDOW_S = 48 * 3600
FIRST_TRIGGER_PCT = D("0.5")
FIRST_LOCK_PCT = D("0.20")
STEP_PCT = D("0.5")


def achieved_step_index(mfe_pct: D) -> int:
    """The highest causal 0.5pp step; jumping over steps is allowed."""
    if mfe_pct < FIRST_TRIGGER_PCT:
        return 0
    return int(mfe_pct // STEP_PCT)


def locked_profit_pct(mfe_pct: D) -> D | None:
    """Quantized stair-stop in % of Entry, never a continuous trailing."""
    level = achieved_step_index(mfe_pct)
    if level == 0:
        return None
    return FIRST_LOCK_PCT if level == 1 else D(level - 1) * STEP_PCT
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


def stair_lock_on_trade(pos, px: D, seq: int, bucket: int,
                       candidate_tp: D | None):
    """Original causal native TP plus discrete, monotone protected stops.

    Existing stop / resting TP take precedence at tick entry. 5m closed
    boundary update can create marketable TP. Activation updates the stop
    for subsequent raw trades; no same-tick protected fill.
    """
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
    favorable = base.favorable(pos.side, pos.entry, px)
    if favorable > pos.best_favorable:
        pos.best_favorable = favorable
    lock_pct = locked_profit_pct(pos.best_favorable)
    if lock_pct is not None:
        proposed = base.threshold_price(pos.side, pos.entry, lock_pct)
        tighter = (proposed > pos.stop if pos.side == "LONG"
                   else proposed < pos.stop)
        if tighter:
            pos.stop = proposed
            pos.protected = True
            pos.protection_seq = seq
    return pos, None


class StairLockBook(old.EntryEmbargo):
    """Same original global second-HardStop/48h ENTRY ONLY embargo."""

    def __init__(self):
        super().__init__(2, 48)
        self.diagnostics = []
        self.protection_activations = 0
        self.stop_step_updates = 0
        self.step_reached_count = Counter()
        self.max_step_index = 0

    def handle(self, symbol, t, seq, px, candidates, state):
        # Replicate baseline event precedence without mutating shared functions.
        bucket = int(t // 300) * 300
        current = self.pos[symbol]
        closed_here = False
        hardstops_before = self.count["close_HARD_STOP"]
        if current is not None:
            prior_protected = current.protected
            prior_stop = current.stop
            prior_step = achieved_step_index(current.best_favorable)
            if current.tp is None and state is None:
                candidate_tp = None
            else:
                candidate_tp = (None if state is None else
                                (state[1] if current.side == "LONG" else state[0]))
            nxt, ex = stair_lock_on_trade(
                current, px, seq, bucket, candidate_tp
            )
            self.pos[symbol] = nxt
            if current.stop != prior_stop:
                assert current.protected
                self.stop_step_updates += 1
                reached = achieved_step_index(current.best_favorable)
                assert reached > prior_step
                self.step_reached_count[reached] += 1
                self.max_step_index = max(self.max_step_index, reached)
            if not prior_protected and current.protected:
                self.protection_activations += 1
            if ex is not None:
                reason, close_px, maker = ex
                gross, entry_fee, exit_fee, net = old.base.gross_and_net(
                    current, close_px, maker
                )
                row = old.base.trade_record(
                    symbol, COHORT, "STAIR050", current, seq, t,
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
                    "stair_stop_armed": current.protected,
                    "last_step_update_seq": current.protection_seq,
                    "protection_stop": str(current.stop),
                    "achieved_stair_index": achieved_step_index(
                        current.best_favorable
                    ),
                    "locked_profit_pct": (
                        None if not current.protected else str(
                            locked_profit_pct(current.best_favorable)
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
        # Original EntryEmbargo handles the new pause AFTER super().handle,
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
            pos = old.base.initial_position(ev, seq, bucket, COHORT, "STAIR050")
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
        report["model"] = "STAIR_050_PERCENT_MFE_LOCK_020_INITIAL"
        report["protection_activations"] = self.protection_activations
        report["stop_step_updates"] = self.stop_step_updates
        report["step_reached_count"] = {
            str(k): v for k, v in sorted(self.step_reached_count.items())
        }
        report["max_step_index"] = self.max_step_index
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
        VARIANT: StairLockBook(),
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
            "Original LEGACY PREV7D TRAIL035 L53_SECOND48 exact control vs "
            "STAIRED protected stop: favorable >=+0.5 percent locks +0.2 "
            "percent of Entry, >=+1.0 locks +0.5, >=+1.5 locks +1.0, "
            ">=+2.0 locks +1.5, etc. every +0.5pp; never loosens and "
            "stays fixed between increments (no continuous trailing). "
            "Original dynamic native L53 target priority unchanged; "
            "stop updates only effective on later raw tick. Initial -10 "
            "percent Hard Stop and 2 Hard Stops within 48h pause only new "
            "Entry 48h, never flatten other positions"
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
