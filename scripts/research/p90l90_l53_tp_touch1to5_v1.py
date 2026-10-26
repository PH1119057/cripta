#!/usr/bin/env python3
"""Issue #227: historical L5-3 price TP touch 1..5, boundary motion.

This is a research-only public-trade execution PROXY, not a live Strategy.
Read original immutable PREV7D Entry. Writes new data-disk child ONLY after
all reference/financial checks pass. Never force-flattens open positions.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
import shutil
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal as D
from pathlib import Path

from scripts.research import p90l90_entry_only_hardstop_embargo_v1 as old

SYMBOLS = tuple(old.SYMBOLS)
ROOTS = {"90d": old.previous.ROOT, "29d": old.sources29.ROOT}
LEAF = "p90l90_l53_tp_touch1to5_20261027_v1"
RESEARCH = "P90L90_L53_PRICE_TOUCH_1_TO_5_V1"
BASELINE = "LEGACY_L53_SECOND48"
NAMES = (BASELINE,) + tuple(f"PRICE_TOUCH_{n}" for n in range(1, 6))
# Owner-approved historical PROXY: current Bybit V5 linear priceFilter.tickSize
# assumed unchanged on BOTH separate periods, not verified historically.
TICK_SIZE = {
    "LTCUSDT": D("0.01"),
    "DOTUSDT": D("0.0001"),
    "ARBUSDT": D("0.00001"),
    "APTUSDT": D("0.0001"),
    "INJUSDT": D("0.0010"),
}
BYBIT_INSTRUMENT_SERVER_MS = {
    "LTCUSDT": 1793044179286,
    "DOTUSDT": 1793044179774,
    "ARBUSDT": 1793044180279,
    "APTUSDT": 1793044180784,
    "INJUSDT": 1793044181293,
}


def sha256(path: Path) -> str:
    with path.open("rb") as reader:
        return hashlib.file_digest(reader, "sha256").hexdigest()


def bound_for(side: str, state: tuple | None):
    if state is None:
        return None
    return state[1] if side == "LONG" else state[0]


def before_tp(side: str, px: D, level: D, tick: D) -> bool:
    return px <= level - tick if side == "LONG" else px >= level + tick


def crosses(side: str, prior: D, current: D, level: D, tick: D) -> bool:
    """Price must cross current LEVEL; a moving boundary alone cannot touch."""
    if side == "LONG":
        return prior <= level - tick and current >= level
    return prior >= level + tick and current <= level


def retreated(side: str, touch_px: D, px: D, level: D, tick: D) -> bool:
    if side == "LONG":
        return px <= touch_px - tick and px <= level - tick
    return px >= touch_px + tick and px >= level + tick


@dataclass
class TouchState:
    symbol: str
    side: str
    entry: D
    tick: D
    entry_event_id: str
    last_px: D
    last_bound: D | None
    last_bucket: int
    touches: int = 0
    last_touch_px: D | None = None
    last_touch_seq: int | None = None
    armed: bool = True
    resting_bound: D | None = None
    resting_from_seq: int = -1
    boundary_motion: str = "STATIONARY"

    def observe(self, timestamp, seq, px, trade_id, bucket, bound, pos):
        """Return (touch_event or None, boundary_event or None).

        Full native tick sequence. The 5m RAW bound never moves intrabar.
        A new target crossed by only geometry must not fabricate a contact.
        """
        boundary = None
        if bucket != self.last_bucket:
            self.last_bucket = bucket
            if bound is not None and self.last_bound is not None:
                prior = self.last_bound
                prior_distance = abs(prior - self.entry)
                new_distance = abs(bound - self.entry)
                motion = ("AWAY_FROM_ENTRY" if new_distance > prior_distance
                          else "TOWARD_ENTRY" if new_distance < prior_distance
                          else "STATIONARY")
                self.boundary_motion = motion
                if bound != prior:
                    # A rolling maximum cannot increase without a newly
                    # completed candle high. A rolling minimum cannot
                    # decrease without a newly completed candle low.
                    extended = (bound > prior if self.side == "LONG"
                                else bound < prior)
                    cause = ("NEW_CLOSED_CANDLE_EXTREME" if extended
                             else "OLD_EXTREME_EXPIRED_OR_ROLLING_REPLACED")
                    geometry_only = (
                        self.last_px < prior and self.last_px >= bound
                        if self.side == "LONG" else
                        self.last_px > prior and self.last_px <= bound
                    )
                    boundary = {
                        "scenario": None, "symbol": self.symbol,
                        "entry_event_id": self.entry_event_id,
                        "ts": repr(timestamp), "effective_bucket": bucket,
                        "side": self.side, "entry": str(self.entry),
                        "old_bound": str(prior), "new_bound": str(bound),
                        "delta_price": str(bound - prior),
                        "delta_ticks": str((bound - prior) / self.tick),
                        "motion": motion, "cause": cause,
                        "crossed_entry": ((prior - self.entry) *
                                          (bound - self.entry) < 0),
                        "touches_before_change": self.touches,
                        "boundary_only_crossed_last_price": geometry_only,
                    }
            self.last_bound = bound

        contact = None
        if bound is not None and old.base.take_profit_eligible(
                self.side, self.entry, bound):
            if self.touches and not self.armed and self.last_touch_px is not None:
                # Retest requires an ACTUAL price retreat, not a zone move.
                if retreated(self.side, self.last_touch_px, px,
                             bound, self.tick):
                    self.armed = True
            crossed = crosses(self.side, self.last_px, px, bound, self.tick)
            if crossed and (self.touches == 0 or self.armed):
                maker = (self.resting_bound == bound
                         and self.resting_from_seq < seq
                         and self.resting_from_seq >= 0)
                self.touches += 1
                self.last_touch_px = px
                self.last_touch_seq = seq
                self.armed = False
                contact = {
                    "scenario": None, "symbol": self.symbol,
                    "entry_event_id": self.entry_event_id,
                    "side": self.side, "index": self.touches,
                    "ts": repr(timestamp), "seq": seq,
                    "trade_id": trade_id, "observed_price": str(px),
                    "entry_price": str(self.entry),
                    "target_bound": str(bound), "tick_size": str(self.tick),
                    "movement_pct": str(old.base.favorable(
                        self.side, self.entry, px)),
                    "prior_tick_price": str(self.last_px),
                    "boundary_motion": self.boundary_motion,
                    "protected_at_touch": pos.protected,
                    "stop_at_touch": str(pos.stop),
                    "resting_maker_eligible": maker,
                }
                # No new order can already be resting on the CONTACT tick.
                self.resting_bound = None
                self.resting_from_seq = -1
                contact["maker"] = maker
            elif before_tp(self.side, px, bound, self.tick):
                # A limit could be placed after observing this tick, valid
                # only on a LATER ordered raw trade at the same target.
                if self.resting_bound != bound:
                    self.resting_bound = bound
                    self.resting_from_seq = seq
        else:
            self.resting_bound = None
            self.resting_from_seq = -1

        self.last_px = px
        return contact, boundary


class TouchBook(old.EntryEmbargo):
    """Independent portfolio, live protection, hardstop history and pause."""

    def __init__(self, n):
        super().__init__(2, 48)
        if n not in range(1, 6):
            raise ValueError("TP touch n must be 1..5")
        self.n = n
        self.trackers = {}
        self.touch_events = []
        self.boundary_events = []
        self.position_outcomes = []

    def _hardstop(self, symbol, t):
        self.all_stops.append({"ts": repr(t), "symbol": symbol})
        self.last_stops.append(t)
        while (self.last_stops and
               t - self.last_stops[0] > old.FIRST_TO_SECOND_WINDOW_S):
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

    def handle_touch(self, symbol, t, seq, px, trade_id, candidates, state):
        bucket = int(t // 300) * 300
        current = self.pos[symbol]
        closed_here = False
        if current is not None:
            tracker = self.trackers[symbol]
            target = bound_for(current.side, state)
            # Keep original HARD_STOP/protected stop precedence. The
            # existing TP engine is bypassed ONLY for new touch cohorts.
            if current.tp is not None:
                raise ValueError("native TP in touch-mode position")
            nxt, ex = old.base.on_trade(
                current, px, seq, bucket, None, old.previous.KIND
            )
            self.pos[symbol] = nxt
            if ex is None and seq > current.entry_seq:
                touch, motion = tracker.observe(
                    t, seq, px, trade_id, bucket, target, current
                )
                if motion:
                    motion["scenario"] = f"PRICE_TOUCH_{self.n}"
                    self.boundary_events.append(motion)
                    if motion["boundary_only_crossed_last_price"]:
                        self.count["boundary_only_crossings"] += 1
                if touch:
                    touch["scenario"] = f"PRICE_TOUCH_{self.n}"
                    self.touch_events.append(touch)
                    self.count["observed_touch"] += 1
                    self.count[f"observed_touch_{tracker.touches}"] += 1
                    if tracker.touches == self.n:
                        maker = touch.pop("maker")
                        close_px = target if maker else px
                        reason = "RESTING_TP" if maker else "MARKETABLE_TP"
                        ex = (reason, close_px, maker)
                        self.pos[symbol] = None
            if ex is not None:
                reason, close_px, maker = ex
                gross, fee_in, fee_out, net = old.base.gross_and_net(
                    current, close_px, maker
                )
                row = old.base.trade_record(
                    symbol, old.previous.COHORT, old.previous.KIND,
                    current, seq, t, close_px, reason, maker, gross,
                    fee_in, fee_out, net
                )
                self.trades.append(row)
                self.position_outcomes.append({
                    "scenario": f"PRICE_TOUCH_{self.n}",
                    "symbol": symbol, "side": current.side,
                    "entry_event_id": current.entry_event_id,
                    "entry_ts": repr(current.entry_ts),
                    "end_ts": repr(t), "reason": reason,
                    "observed_contacts": tracker.touches,
                    "net_usdt": str(net),
                })
                self.trackers.pop(symbol)
                for key, val in (
                    ("gross_usdt", gross), ("entry_fees_usdt", fee_in),
                    ("exit_fees_usdt", fee_out), ("net_usdt", net),
                    ("positive_net_usdt", max(net, D(0))),
                    ("negative_net_usdt", min(net, D(0))),
                ):
                    self.money[key] += val
                self.count["closed"] += 1
                self.count["close_" + reason] += 1
                self.count["win" if net > 0 else "loss" if net < 0
                           else "flat"] += 1
                closed_here = True
                if reason == "HARD_STOP":
                    self._hardstop(symbol, t)

        for ev in candidates:
            if not ev["PREV7D"]:
                continue
            self.count["qualified_candidate"] += 1
            if self.pos[symbol] is not None or closed_here:
                self.count["busy_or_same_tick"] += 1
                continue
            if t < self.pause_until or self.pending:
                self.count["blocked_global_pause"] += 1
                continue
            if state is None:
                raise ValueError("accepted Entry lacks causal L5 state")
            pos = old.base.initial_position(
                ev, seq, bucket, old.previous.COHORT, old.previous.KIND
            )
            target = bound_for(pos.side, state)
            self.pos[symbol] = pos
            # No resting TP is placed until the Nth genuine price crossing.
            self.trackers[symbol] = TouchState(
                symbol=symbol, side=pos.side, entry=pos.entry,
                tick=TICK_SIZE[symbol], entry_event_id=pos.entry_event_id,
                last_px=px, last_bound=target, last_bucket=bucket
            )
            self.count["accepted"] += 1

    def finish_touch(self):
        report = self.finish()
        if report["counts"]["accepted"] != len(self.position_outcomes) + sum(
                pos is not None for pos in self.pos.values()):
            raise ValueError("touch position/outcome accounting failed")
        for sym, pos in self.pos.items():
            if pos is not None:
                tracker = self.trackers[sym]
                self.position_outcomes.append({
                    "scenario": f"PRICE_TOUCH_{self.n}", "symbol": sym,
                    "side": pos.side,
                    "entry_event_id": pos.entry_event_id,
                    "entry_ts": repr(pos.entry_ts), "end_ts": None,
                    "reason": "CENSORED", "observed_contacts": tracker.touches,
                    "net_usdt": None,
                })
        if self.count.get("close_PORTFOLIO_FLATTEN", 0):
            raise ValueError("forbidden forced flatten")
        counts = Counter(x["observed_contacts"] for x in self.position_outcomes)
        report["reached_at_least"] = {
            str(k): sum(n for contacts, n in counts.items() if contacts >= k)
            for k in range(1, 6)
        }
        report["boundary_events"] = len(self.boundary_events)
        report["touch_events"] = len(self.touch_events)
        report["status"] = "HISTORICAL_PROXY_NOT_LIVE"
        return report


def references(period):
    parent = ROOTS[period] / "results"
    folder = parent / "p90l90_h9_second48_embargo_20261026_v1"
    manifest = json.loads((folder / "result.json").read_text())
    ledger = folder / "trade_ledger.jsonl"
    if sha256(ledger) != manifest["ledger_sha256"]:
        raise ValueError("frozen #224 reference SHA drift")
    target = {sym: [] for sym in SYMBOLS}
    for line in ledger.open(encoding="utf-8"):
        row = json.loads(line)
        if row.pop("portfolio_scenario") == "L53_SECOND48":
            target[row["symbol"]].append(row)
    return target, manifest["ledger_sha256"]


def preflight(period, output, smoke_hours):
    if period not in ROOTS or smoke_hours < 0 or smoke_hours > 24:
        raise ValueError("bad period or bounded hours")
    if os.geteuid() != 107:
        raise PermissionError("effective research actor must be cripta 107")
    parent = ROOTS[period] / "results"
    if (not output.is_absolute() or output.name != LEAF
            or output.parent.resolve() != parent.resolve()):
        raise ValueError("invalid exact immutable output path")
    if output.exists() or output.is_symlink():
        raise FileExistsError("research leaf collision")
    if parent.stat().st_uid != 107 or not os.access(
            parent, os.W_OK | os.X_OK):
        raise PermissionError("research parent not writable by cripta")


def summarize(books, reports):
    result = {}
    for name, book in books.items():
        rows = book.trades
        by_symbol = {}
        for sym in SYMBOLS:
            subset = [row for row in rows if row["symbol"] == sym]
            by_symbol[sym] = {
                "closed": len(subset),
                "hard_stop": sum(x["exit_reason"] == "HARD_STOP"
                                 for x in subset),
                "tp_plus": sum(
                    x["exit_reason"] in ("RESTING_TP", "MARKETABLE_TP")
                    and D(x["net_usdt"]) > 0 for x in subset
                ),
                "tp_minus": sum(
                    x["exit_reason"] in ("RESTING_TP", "MARKETABLE_TP")
                    and D(x["net_usdt"]) <= 0 for x in subset
                ),
                "protected_stop": sum(
                    x["exit_reason"] == "PROTECTED_STOP" for x in subset
                ),
                "net_original_fees": str(sum(
                    (D(x["net_usdt"]) for x in subset), D(0)
                )),
                "long_net": str(sum(
                    (D(x["net_usdt"]) for x in subset
                     if x["side"] == "LONG"), D(0)
                )),
                "short_net": str(sum(
                    (D(x["net_usdt"]) for x in subset
                     if x["side"] == "SHORT"), D(0)
                )),
            }
        net = sum((D(x["net_usdt"]) for x in rows), D(0))
        original = D(reports[name]["money"]["net_usdt"])
        if net != original:
            raise ValueError("aggregate money mismatch " + name)
        result[name] = {
            "by_symbol": by_symbol, "net_usdt": str(net),
            "counts": reports[name]["counts"]
        }
    return result


def run(period: str, output: Path, smoke_hours: int = 0):
    preflight(period, output, smoke_hours)
    first = old.entry90.START_TS if period == "90d" else old.entry29.START_TS
    end = old.entry90.END_TS if period == "90d" else old.entry29.END_TS
    if period == "90d":
        data = {s: old.previous.inputs(
            s, old.previous.RAW_DEFAULT, old.previous.FROZEN_DEFAULT
        ) for s in SYMBOLS}
        streams = {s: old.previous.ticks(data[s]) for s in SYMBOLS}
    else:
        data = {s: old.sep.read_inputs(s) for s in SYMBOLS}
        streams = {s: old.sep.native_ticks(data[s]) for s in SYMBOLS}

    books = {BASELINE: old.EntryEmbargo(2, 48)}
    books.update({f"PRICE_TOUCH_{n}": TouchBook(n) for n in range(1, 6)})
    queue = []
    for idx, sym in enumerate(SYMBOLS):
        tick = next(streams[sym], None)
        if tick is None:
            raise ValueError("empty Bybit tick stream " + sym)
        heapq.heappush(queue, (tick[0], idx, tick))
    cutoff = end if not smoke_hours else min(end, first + smoke_hours * 3600)
    ticks = 0
    last_day = None
    while queue:
        t, idx, (timestamp, seq, px, trade_id) = heapq.heappop(queue)
        if t >= cutoff:
            break
        sym = SYMBOLS[idx]
        candidates = data[sym]["events"].get(seq, ())
        state = data[sym]["states"].get(int(t // 300) * 300)
        baseline = books[BASELINE]
        if candidates or baseline.pos[sym] is not None:
            baseline.handle(sym, t, seq, px, candidates, state)
        for n in range(1, 6):
            book = books[f"PRICE_TOUCH_{n}"]
            if candidates or book.pos[sym] is not None:
                book.handle_touch(sym, t, seq, px, trade_id,
                                  candidates, state)
        ticks += 1
        day = int(t // 86400)
        if day != last_day:
            last_day = day
            print(json.dumps({
                "phase": "REPLAY", "period": period,
                "day_epoch_utc": day, "evaluated_ticks": ticks
            }), flush=True)
        nxt = next(streams[sym], None)
        if nxt is not None:
            heapq.heappush(queue, (nxt[0], idx, nxt))

    reports = {}
    for name, book in books.items():
        reports[name] = (book.finish() if name == BASELINE
                         else book.finish_touch())
    if smoke_hours:
        return {"status": "SMOKE_PASS", "period": period,
                "native_ticks": ticks,
                "scenario_counts": {s: x["counts"]
                                    for s, x in reports.items()}}

    for sym in SYMBOLS:
        d = data[sym]
        expected = sum(map(len, d["events"].values()))
        actual = (d["seen_candidate"] if period == "90d"
                  else d["seen_events"])
        if actual != expected:
            raise ValueError("immutable candidate event count drift " + sym)
    reference, ref_sha = references(period)
    for sym in SYMBOLS:
        generated = [r for r in books[BASELINE].trades
                     if r["symbol"] == sym]
        if generated != reference[sym]:
            raise ValueError("EXACT old SECOND48 trade parity failed " + sym)

    breakdown = summarize(books, reports)
    summary = {
        "research": RESEARCH, "period": period,
        "status": "COMPLETE_HISTORICAL_PROXY_NOT_LIVE",
        "symbols": list(SYMBOLS),
        "starting_theoretical_capital_usdt": "50",
        "nominal_usdt_per_coin": "10",
        "original_legacy_second48_parity": True,
        "reference_224_ledger_sha256": ref_sha,
        "historical_ticksize_stability_assumed": True,
        "ticksize_source": (
            "Bybit V5 public linear /v5/market/instruments-info "
            "priceFilter.tickSize, retrieved 2026-10-26 19:49 UTC; "
            "NOT verified historical tickSize"
        ),
        "tick_size": {s: str(v) for s, v in TICK_SIZE.items()},
        "bybit_instrument_server_ms": BYBIT_INSTRUMENT_SERVER_MS,
        "model": (
            "Six independent P90L90 PREV7D one-slot-per-coin portfolios; "
            "legacy exact L53 maker/marketable TP control; new scenarios "
            "1..5 genuine raw public-trade PRICE crossings of dynamic "
            "opposite 36 closed 5m L53 RAW bound, reset only after one "
            "current assumed instrument tick price retreat; no geometry-"
            "only contact; unchanged original TRAIL035>=0.5 favorable, "
            "distance 0.35 percentage points, hard stop -10%; second "
            "actual hard stop within 48h pauses NEW Entry 48h, no flat."
        ),
        "unmodeled": ["spread", "slippage", "funding", "latency",
                      "historical_ticksize_changes", "true_exchange_fills"],
        "scenarios": reports, "financial_breakdown": breakdown,
        "evaluated_native_ticks": ticks,
        "input_manifest_sha256": {
            s: data[s]["manifest"]["events_sha256"] for s in SYMBOLS
        },
    }
    output.mkdir(mode=0o2770)
    snap = output / "source_snapshot"
    snap.mkdir(mode=0o2770)
    paths = (Path(__file__), Path(old.__file__),
             Path(old.previous.__file__), Path(old.base.__file__),
             Path(old.entry90.__file__), Path(old.entry29.__file__),
             Path(old.sep.__file__), Path(old.sources29.__file__))
    summary["source_snapshot_sha256"] = {}
    for p in paths:
        dest = snap / p.name
        shutil.copyfile(p, dest)
        summary["source_snapshot_sha256"][p.name] = sha256(dest)
    outputs = {
        "trade_ledger.jsonl": (
            {**row, "portfolio_scenario": name}
            for name, book in books.items() for row in book.trades
        ),
        "touch_events.jsonl": (
            event for name, book in books.items() if name != BASELINE
            for event in book.touch_events
        ),
        "boundary_timeline.jsonl": (
            event for name, book in books.items() if name != BASELINE
            for event in book.boundary_events
        ),
        "position_outcomes.jsonl": (
            event for name, book in books.items() if name != BASELINE
            for event in book.position_outcomes
        ),
    }
    for filename, records in outputs.items():
        path = output / filename
        count = 0
        with path.open("x", encoding="utf-8") as stream:
            for row in records:
                stream.write(json.dumps(
                    row, sort_keys=True, separators=(",", ":")) + "\n"
                )
                count += 1
        summary[filename] = {
            "rows": count, "sha256": sha256(path),
        }
    with (output / "result.json").open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return {
        "status": "COMPLETE", "period": period,
        "output": str(output), "baseline_parity": True,
        "net_original_fee": {k: v["net_usdt"]
                            for k, v in breakdown.items()}
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=("29d", "90d"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--smoke-hours", type=int, default=0)
    args = parser.parse_args()
    print("RESULT", json.dumps(
        run(args.period, args.output, args.smoke_hours),
        sort_keys=True
    ), flush=True)


if __name__ == "__main__":
    main()
