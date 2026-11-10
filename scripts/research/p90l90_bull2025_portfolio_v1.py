#!/usr/bin/env python3
"""P90L90 six-month 2025 native five-coin research portfolio, one side per run.

The exact original Exit transition functions are imported unchanged; 2025
source, window and cash/policy scenarios are isolated research parameters.
W7 signals originate only from a separate QC'd 2025 NO_PAUSE shadow ledger,
with preceding seven *complete* UTC days; not from 2026 performance.

No production DB, StrategyCard, Exchange, release, LIVE, ARM or service mutation.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import heapq
import json
import os
import shutil
import sys
import time
from collections import Counter, defaultdict, deque
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D, ROUND_HALF_EVEN
from pathlib import Path

SOURCE = Path("/srv/cripta/source_checkout")
sys.path.insert(0, str(SOURCE))
from scripts.research import p90l90_bull2025_entry_v1 as entry  # noqa: E402
from scripts.research import p90l90_bull2025_exit_unit_v1 as unit  # noqa: E402
from scripts.research import p90l90_exit_90d_v2 as original  # noqa: E402

ROOT = entry.ROOT
RESULTS = ROOT / "results"
SYMBOLS = tuple(entry.SYMBOLS)
START_TS = unit.START_TS
END_TS = unit.END_TS
EVAL_START = unit.EVAL_START
EVAL_END = unit.EVAL_END
START_WALLET = D("1000")
FIXED_MARGIN = D("200")
LEVERAGE = D("5")
TAKER = original.TAKER
MAKER = original.MAKER
Q = original.MONEY_QUANTUM
THRESHOLDS = (150, 200, 250, 300, 350, 400)
SCENARIOS = ("NO_PAUSE", "SECOND48") + tuple(f"W7H24_B{b}" for b in THRESHOLDS)
CASH_MODES = ("fixed", "compound_capped")
SIDES = ("LONG_ONLY", "SHORT_ONLY")
OUT_PARENT = RESULTS / "portfolio_full_2025_v1"
UNIT_PARENT = RESULTS / "exit_unit_2025_v1"
ENTRY_PARENT = RESULTS / "entry_full_2025_v1"
FROZEN_PARENT = RESULTS / "l53_geometry_full_2025_v1/parts"
SELF_REL = "scripts/research/p90l90_bull2025_portfolio_v1.py"
ORIGINAL_REL = "scripts/research/p90l90_exit_90d_v2.py"
COUNTS_2025 = {
    "LTCUSDT": (41014483, 3724),
    "DOTUSDT": (48902898, 3653),
    "ARBUSDT": (44963953, 3753),
    "APTUSDT": (26287882, 3984),
    "INJUSDT": (43277155, 3865),
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def money(value: D) -> D:
    return value.quantize(Q, rounding=ROUND_HALF_EVEN)


def verify_and_load(side: str, source_head: str):
    if os.geteuid() != 107 or entry.git_head() != source_head:
        raise PermissionError("2025 full research requires cripta UID107/exact SOURCE_HEAD")
    if side not in SIDES or len(source_head) != 40:
        raise ValueError("invalid independent side or source commit")
    if RESULTS.stat().st_uid != 107 or not os.access(RESULTS, os.W_OK | os.X_OK):
        raise PermissionError("actual research writer lacks parent access")
    if OUT_PARENT.is_symlink() or RESULTS.is_symlink():
        raise PermissionError("output root symlink denied")
    if OUT_PARENT.exists() and (
        OUT_PARENT.stat().st_uid != 107 or not os.access(OUT_PARENT, os.W_OK | os.X_OK)
    ):
        raise PermissionError("portfolio output parent not writable as cripta")
    out = OUT_PARENT / side.lower()
    if out.exists() or out.is_symlink():
        raise FileExistsError("2025 full portfolio immutable output collision")
    if shutil.disk_usage(ROOT).free < 16 * 2**30:
        raise RuntimeError("below 16GiB research data-disk reserve")
    acq = json.loads((ROOT / "run_manifest.json").read_text())
    if acq.get("status") != "INPUT_COMPLETE_QC_PASS_NO_STRATEGY_REPLAY":
        raise ValueError("2025 original Bybit source QC failed")
    if sha(SOURCE / ORIGINAL_REL) != acq["source_snapshot_sha256"][ORIGINAL_REL]:
        raise ValueError("original P90L90 Exit code differs from frozen 2025 snapshot")
    if sha(SOURCE / unit.SELF_REL) != sha(SOURCE / unit.SELF_REL):
        raise ValueError("impossible Exit source identity")
    geom = json.loads((RESULTS / "l53_geometry_full_2025_v1/qc.json").read_text())
    if geom.get("status") != "QC_PASS_FULL_2025_GEOMETRY_NOT_TRADING":
        raise ValueError("2025 geometry failed")
    all_data = {}
    all_unit = {}
    for symbol in SYMBOLS:
        ep = ENTRY_PARENT / symbol
        up = UNIT_PARENT / symbol
        eq = json.loads((ep / "qc.json").read_text())
        em = json.loads((ep / "manifest.json").read_text())
        uq = json.loads((up / "qc.json").read_text())
        ur = json.loads((up / "result.json").read_text())
        if (
            eq["status"] != "QC_PASS_2025_ENTRY_CANDIDATE_ONLY_NO_EXITS"
            or em["research"] != "P90L90_ENTRY_BULL2025_V1"
            or em["symbol"] != symbol
            or em["window"] != [EVAL_START.isoformat(), EVAL_END.isoformat()]
            or len(em["inputs"]) != 193
            or em["frozen_l53"]["states"] != 183 * 288
            or em["PREV7D_WIDTH_LOOKAHEAD"] is not False
            or em["counts"]["raw_rows"] != COUNTS_2025[symbol][0]
            or em["events"] != COUNTS_2025[symbol][1]
            or uq["status"] != "QC_PASS_UNIT_EXIT_ONLY_NOT_PORTFOLIO_2025"
            or ur["symbol"] != symbol
            or ur["native_ticks_all_days"] != COUNTS_2025[symbol][0]
            or ur["native_candidate_events"] != COUNTS_2025[symbol][1]
            or ur["evaluation_days"] != 183
        ):
            raise ValueError("2025 Entry/unit source identity mismatch " + symbol)
        cp = ep / f"candidates_{symbol}.jsonl"
        fp = FROZEN_PARENT / f"states_{symbol}.csv.gz"
        if sha(cp) != em["events_sha256"] or sha(fp) != em["frozen_l53_sha256"]:
            raise ValueError("2025 Entry/frozen source SHA mismatch " + symbol)
        if sha(up / "result.json") != uq["result_sha256"]:
            raise ValueError("2025 unit result SHA mismatch " + symbol)
        ul = up / f"trade_ledger_{side.split('_')[0].lower()}.jsonl"
        if sha(ul) != uq["ledgers"][side.split("_")[0]]:
            raise ValueError("2025 unit ledger SHA mismatch " + symbol)
        with ul.open() as f:
            unit_rows = [json.loads(x) for x in f]
        if len(unit_rows) != ur["scenarios"][side.split("_")[0]]["ledger_rows"]:
            raise ValueError("unit 2025 shadow row mismatch")
        states, _, med7, state_meta = entry.load_frozen_period(
            fp, EVAL_START, EVAL_END
        )
        if len(states) != 183 * 288 or len(med7) != 183 or state_meta["prior_complete_days"] != 7:
            raise ValueError("2025 PREV7D closed-only state/day QC")
        events, counters = original.read_events(cp, symbol)
        if counters["raw_candidates"] != COUNTS_2025[symbol][1]:
            raise ValueError("2025 independent Entry ledger count mismatch")
        all_data[symbol] = {
            "manifest": em, "events": events, "states": states,
            "seen_events": 0, "rows": 0, "symbol": symbol,
        }
        all_unit[symbol] = {
            "rows": unit_rows, "sha": sha(ul),
            "unit_result_sha": uq["result_sha256"],
        }
    return all_data, all_unit, out


def shadow_schedule(all_unit: dict, side: str) -> dict[int, list[tuple]]:
    """W7/H24 triggers use only the previous seven COMPLETE UTC days.

    Emitted (timestamp, symbol_priority, native_seq) entries also resolve
    simultaneous native ticks without seeing later symbols at equal timestamps.
    """
    rows = [(symbol, x) for symbol, entry_data in all_unit.items()
            for x in entry_data["rows"]]
    daily = defaultdict(Counter)
    stops = []
    for symbol, row in rows:
        t = float(row["exit_ts"])
        if row["side"] != side.split("_")[0] or not START_TS <= t < END_TS:
            raise ValueError("wrong original 2025 shadow side/time")
        day = datetime.fromtimestamp(t, UTC).date()
        daily[day]["closed"] += 1
        if row["exit_reason"] == "HARD_STOP":
            daily[day]["hard_stop"] += 1
            stops.append((t, SYMBOLS.index(symbol), int(row["exit_source_order"])))
    stops.sort()
    schedule = {b: [] for b in THRESHOLDS}
    for instant in stops:
        day = datetime.fromtimestamp(instant[0], UTC).date()
        if day < EVAL_START.date() + timedelta(days=7):
            continue
        previous = [day - timedelta(days=d) for d in range(1, 8)]
        closed = sum(daily[d]["closed"] for d in previous)
        hard = sum(daily[d]["hard_stop"] for d in previous)
        if closed < 20:
            continue
        for bps in THRESHOLDS:
            if hard * 10000 >= bps * closed:
                schedule[bps].append(instant)
    return schedule


class Book:
    def __init__(self, side: str, scenario: str, mode: str, signals: list[tuple]):
        self.side = side.split("_")[0]
        self.scenario = scenario
        self.mode = mode
        self.signals = signals
        self.signal_index = 0
        self.pause_until = float("-inf")
        self.last_stops = deque()
        self.wallet = START_WALLET
        self.reserved = D(0)
        self.pos = dict.fromkeys(SYMBOLS)
        self.margin = {}
        self.open_fee = {}
        self.trades = []
        self.counts = Counter()
        self.m = defaultdict(lambda: D(0))
        self.peak_equity = START_WALLET
        self.max_daily_dd_pct = D(0)
        self.daily_marks = []
        self.max_reserved = D(0)

    def advance_signals(self, native_key: tuple) -> None:
        while (self.signal_index < len(self.signals)
               and self.signals[self.signal_index] <= native_key):
            ts, _, _ = self.signals[self.signal_index]
            self.signal_index += 1
            self.pause_until = max(self.pause_until, ts + 86400)
            self.counts["shadow_rate_pause_triggers"] += 1

    def mark(self, timestamp: float, last_prices: dict) -> D:
        equity = self.wallet
        for symbol, pos in self.pos.items():
            if pos is None or symbol not in last_prices:
                continue
            notional = self.margin[symbol] * LEVERAGE
            ratio = last_prices[symbol] / pos.entry
            equity += notional * (ratio - 1) * (D(1) if self.side == "LONG" else D(-1))
        if equity > self.peak_equity:
            self.peak_equity = equity
        dd = (self.peak_equity - equity) / self.peak_equity * D(100) if self.peak_equity > 0 else D(0)
        self.max_daily_dd_pct = max(self.max_daily_dd_pct, dd)
        self.daily_marks.append((str(timestamp), str(equity)))
        return equity

    def handle(self, symbol: str, t: float, seq: int, px: D,
               events: tuple, state: tuple, priority: int) -> bool:
        """True if this book now owns an open position in symbol."""
        bucket = int(t // 300) * 300
        pos = self.pos[symbol]
        closed_here = False
        if pos is not None:
            tp = state[1] if self.side == "LONG" else state[0]
            updated, outcome = original.on_trade(
                pos, px, seq, bucket, tp, "TRAIL035"
            )
            self.pos[symbol] = updated
            if outcome is not None:
                reason, exit_px, maker = outcome
                margin = self.margin.pop(symbol)
                entry_fee = self.open_fee.pop(symbol)
                nominal = margin * LEVERAGE
                gross = money(nominal * (exit_px / pos.entry - 1) *
                              (D(1) if self.side == "LONG" else D(-1)))
                exit_fee = money(nominal * exit_px / pos.entry * (MAKER if maker else TAKER))
                net = gross - entry_fee - exit_fee
                self.reserved -= margin
                self.wallet += gross - exit_fee
                self.m["gross_usdt"] += gross
                self.m["closed_entry_fee_usdt"] += entry_fee
                self.m["exit_fee_usdt"] += exit_fee
                self.m["closed_net_usdt"] += net
                self.m["positive_closed_net_usdt"] += max(net, D(0))
                self.m["negative_closed_net_usdt"] += min(net, D(0))
                self.counts["closed"] += 1
                self.counts["exit_" + reason] += 1
                self.counts["win" if net > 0 else "loss" if net < 0 else "flat"] += 1
                self.trades.append({
                    **original.trade_record(
                        symbol, "PREV7D", "TRAIL035", pos, seq, t,
                        exit_px, reason, maker, gross, entry_fee, exit_fee, net
                    ),
                    "scenario": self.scenario,
                    "capital_mode": self.mode,
                    "margin_usdt": str(margin),
                    "notional_usdt": str(nominal),
                })
                closed_here = True
                if self.scenario == "SECOND48" and reason == "HARD_STOP":
                    self.last_stops.append(t)
                    while self.last_stops and t - self.last_stops[0] > 48*3600:
                        self.last_stops.popleft()
                    if len(self.last_stops) >= 2:
                        self.pause_until = max(self.pause_until, t + 48*3600)
                        self.counts["second48_pause_triggers"] += 1
        if self.scenario.startswith("W7H24") and events:
            self.advance_signals((t, priority, seq))
        for event in events:
            if event["side"] != self.side or not event["PREV7D"]:
                continue
            self.counts["qualified_candidate"] += 1
            if self.pos[symbol] is not None or closed_here:
                self.counts["busy_or_same_tick"] += 1
                continue
            if t < self.pause_until:
                self.counts["paused_candidate"] += 1
                continue
            ideal = FIXED_MARGIN if self.mode == "fixed" else max(D(0), self.wallet * D("0.2"))
            free = self.wallet - self.reserved
            if self.mode == "fixed":
                margin = ideal
            else:
                margin = min(ideal, max(D(0), free / (D(1) + LEVERAGE * TAKER) - D("1e-12")))
            fee = money(margin * LEVERAGE * TAKER) if margin > 0 else D(0)
            if margin <= 0 or margin + fee > free:
                self.counts["capital_blocked_candidate"] += 1
                continue
            self.wallet -= fee
            self.reserved += margin
            self.max_reserved = max(self.max_reserved, self.reserved)
            self.m["all_entry_fee_usdt"] += fee
            p = original.initial_position(event, seq, bucket, "PREV7D", "TRAIL035")
            tp = state[1] if self.side == "LONG" else state[0]
            if original.take_profit_eligible(self.side, px, tp):
                p.tp = tp
                p.tp_live_after_seq = seq
            self.pos[symbol] = p
            self.margin[symbol] = margin
            self.open_fee[symbol] = fee
            self.counts["accepted"] += 1
        return self.pos[symbol] is not None

    def finish(self, last_prices: dict, at: float) -> dict:
        if self.scenario.startswith("W7H24"):
            # Advance any signals after last candidate for complete audit,
            # without allowing a retrospective Entry decision.
            self.advance_signals((at, len(SYMBOLS), 2**63-1))
        open_positions = []
        floating_gross = D(0)
        for symbol, pos in self.pos.items():
            if pos is None:
                continue
            mark_px = last_prices[symbol]
            nominal = self.margin[symbol] * LEVERAGE
            floating = money(nominal * (mark_px / pos.entry - 1) *
                             (D(1) if self.side == "LONG" else D(-1)))
            floating_gross += floating
            open_positions.append({
                "symbol": symbol, "side": self.side,
                "entry_ts": repr(pos.entry_ts), "entry_price": str(pos.entry),
                "last_price": str(mark_px), "margin": str(self.margin[symbol]),
                "notional": str(nominal), "unrealized_gross_usdt": str(floating),
                "entry_fee_already_debited": str(self.open_fee[symbol]),
            })
        self.counts["censored_open"] = len(open_positions)
        if self.counts["qualified_candidate"] != (
            self.counts["accepted"] + self.counts["busy_or_same_tick"]
            + self.counts["paused_candidate"]
            + self.counts["capital_blocked_candidate"]
        ):
            raise ValueError("2025 portfolio candidate count reconciliation failed")
        if self.counts["accepted"] != self.counts["closed"] + len(open_positions):
            raise ValueError("portfolio accepted/closed/censored mismatch")
        if len(self.trades) != self.counts["closed"]:
            raise ValueError("closed ledger row count mismatch")
        if (
            self.m["gross_usdt"] - self.m["closed_entry_fee_usdt"]
            - self.m["exit_fee_usdt"] != self.m["closed_net_usdt"]
            or self.m["positive_closed_net_usdt"] + self.m["negative_closed_net_usdt"]
            != self.m["closed_net_usdt"]
            or sum((D(x["net_usdt"]) for x in self.trades), D(0))
            != self.m["closed_net_usdt"]
        ):
            raise ValueError("2025 portfolio fees/ledger PnL mismatch")
        if self.m["all_entry_fee_usdt"] != (
            self.m["closed_entry_fee_usdt"]
            + sum((self.open_fee[s] for s in self.open_fee), D(0))
        ):
            raise ValueError("entry fee from censored positions not recognized")
        if self.wallet - START_WALLET != (
            self.m["gross_usdt"] - self.m["all_entry_fee_usdt"]
            - self.m["exit_fee_usdt"]
        ):
            raise ValueError("actual modeled wallet reconciliation failed")
        if self.reserved != sum(self.margin.values(), D(0)):
            raise ValueError("capital margin reservation mismatch")
        self.mark(at, last_prices)
        return {
            "counts": dict(self.counts),
            "cash_model": self.mode, "scenario": self.scenario,
            "start_wallet_usdt": str(START_WALLET),
            "wallet_usdt": str(self.wallet),
            "closed_net_after_fees_usdt": str(self.m["closed_net_usdt"]),
            "wallet_change_includes_open_entry_fees_usdt": str(self.wallet - START_WALLET),
            "equity_mark_to_last_native_price_gross_usdt": str(self.wallet + floating_gross),
            "unrealized_open_gross_usdt": str(floating_gross),
            "reserved_margin_usdt": str(self.reserved),
            "max_reserved_margin_usdt": str(self.max_reserved),
            "daily_mark_drawdown_pct": str(self.max_daily_dd_pct),
            "money": {k: str(v) for k,v in self.m.items()},
            "open_censored": open_positions,
            "ledger_rows": len(self.trades),
            "modeled_fills_not_exchange": True,
        }


def native_ticks(d: dict):
    symbol = d["symbol"]
    seq = 0
    last_ts = None
    for item in d["manifest"]["inputs"]:
        p = ROOT / "inputs" / symbol / "public_trades" / item["file"]
        if sha(p) != item["sha256"]:
            raise ValueError("original 2025 Bybit source day hash mismatch")
        n = 0
        with gzip.open(p, "rt", newline="") as f:
            for row in csv.DictReader(f):
                n += 1
                seq += 1
                t = float(row["timestamp"])
                if last_ts is not None and t < last_ts:
                    raise ValueError("native trade source timestamp regression")
                last_ts = t
                if t < START_TS:
                    continue
                if t >= END_TS:
                    raise ValueError("native tick outside 2025 study end")
                px = D(row["price"])
                es = d["events"].get(seq, ())
                for e in es:
                    if e["trade_id"] != row["trdMatchID"] or float(e["ts"]) != t or D(e["price"]) != px:
                        raise ValueError("candidate/native tick identity mismatch")
                d["seen_events"] += len(es)
                yield t, seq, px, row["trdMatchID"]
        if n != item["rows"]:
            raise ValueError("daily native 2025 row count mismatch")
    d["rows"] = seq
    if seq != sum(x["rows"] for x in d["manifest"]["inputs"]):
        raise ValueError("2025 193-day native source count mismatch")
    if d["seen_events"] != d["manifest"]["events"]:
        raise ValueError("2025 raw Entry candidate tick count mismatch")


def run(side: str, source_head: str, smoke_hours: int = 0) -> dict:
    data, unit_reference, out = verify_and_load(side, source_head)
    schedule = shadow_schedule(unit_reference, side)
    books = {
        (case, mode): Book(side, case, mode,
                           schedule[int(case.rsplit("B", 1)[1])] if case.startswith("W7H24") else [])
        for case in SCENARIOS for mode in CASH_MODES
    }
    # Unlimited-capital NO_PAUSE shadow proves the native five-symbol timeline
    # exactly reproduces all five separately calculated 2025 unit Exit ledgers.
    shadow_positions = dict.fromkeys(SYMBOLS)
    shadow_rows = {s: [] for s in SYMBOLS}
    streams = {s: native_ticks(data[s]) for s in SYMBOLS}
    heap = []
    for i, s in enumerate(SYMBOLS):
        first = next(streams[s], None)
        if first is None:
            raise ValueError("empty 2025 native source in evaluation " + s)
        heapq.heappush(heap, (first[0], i, first))
    n = 0
    previous_day = None
    prices = {}
    started = time.monotonic()
    last_heartbeat = started
    limit = END_TS if not smoke_hours else min(END_TS, START_TS + smoke_hours * 3600)
    while heap:
        t, priority, (_, seq, px, _) = heapq.heappop(heap)
        if t >= limit:
            break
        s = SYMBOLS[priority]
        prices[s] = px
        events = data[s]["events"].get(seq, ())
        state = data[s]["states"].get(int(t // 300) * 300)
        if state is None:
            raise ValueError("causal 2025 frozen state missing on native print")
        day = int(t // 86400)
        if day != previous_day:
            if previous_day is not None:
                for b in books.values():
                    b.mark(previous_day * 86400 + 86400, prices)
            previous_day = day
        current = shadow_positions[s]
        just_closed = False
        if current is not None:
            tp = state[1] if current.side == "LONG" else state[0]
            newp, ex = original.on_trade(
                current, px, seq, int(t // 300) * 300, tp, "TRAIL035"
            )
            shadow_positions[s] = newp
            if ex is not None:
                reason, close_px, maker = ex
                gross, ef, xf, net = original.gross_and_net(current, close_px, maker)
                row = original.trade_record(
                    s, "PREV7D", "TRAIL035", current, seq, t,
                    close_px, reason, maker, gross, ef, xf, net
                )
                row["research_direction"] = side.split("_")[0]
                shadow_rows[s].append(row)
                just_closed = True
        for e in events:
            if e["side"] != side.split("_")[0] or not e["PREV7D"]:
                continue
            if shadow_positions[s] is not None or just_closed:
                continue
            pos = original.initial_position(e, seq, int(t // 300) * 300,
                                            "PREV7D", "TRAIL035")
            tp = state[1] if pos.side == "LONG" else state[0]
            if original.take_profit_eligible(pos.side, px, tp):
                pos.tp, pos.tp_live_after_seq = tp, seq
            shadow_positions[s] = pos
        for book in books.values():
            if events or book.pos[s] is not None:
                book.handle(s, t, seq, px, events, state, priority)
        n += 1
        if time.monotonic() - last_heartbeat >= 20:
            print(json.dumps({"stage": "PORTFOLIO_2025_HEARTBEAT",
                              "side": side, "native_ticks": n,
                              "utc_day": day}), flush=True)
            last_heartbeat = time.monotonic()
        nxt = next(streams[s], None)
        if nxt is not None:
            heapq.heappush(heap, (nxt[0], priority, nxt))
    if smoke_hours:
        return {"status": "SMOKE_PASS_2025_NO_OUTPUT",
                "side": side, "hours": smoke_hours, "ticks": n,
                "scenarios": len(books)}
    if n <= 0 or sum(data[s]["rows"] for s in SYMBOLS) != 204446371:
        raise ValueError("full 2025 native tick coverage mismatch")
    for s in SYMBOLS:
        if shadow_rows[s] != unit_reference[s]["rows"]:
            raise ValueError("exact five-coin 2025 unlimited NO_PAUSE shadow parity failure " + s)
    for s in SYMBOLS:
        if data[s]["seen_events"] != data[s]["manifest"]["events"]:
            raise ValueError("2025 candidate event source counter mismatch")
    results = {(case,mode): book.finish(prices, END_TS) for (case,mode),book in books.items()}
    if entry.git_head() != source_head:
        raise RuntimeError("source HEAD changed while 2025 portfolio was running")
    if out.exists() or out.is_symlink():
        raise FileExistsError("immutable 2025 portfolio output collision at publish")
    OUT_PARENT.mkdir(mode=0o2770, parents=False, exist_ok=True)
    if OUT_PARENT.stat().st_uid != 107:
        raise PermissionError("output parent actor identity not cripta")
    out.mkdir(mode=0o2770)
    hashes = {}
    for (case,mode), book in books.items():
        p = out / f"ledger_{case}_{mode}.jsonl"
        with p.open("x") as f:
            for record in book.trades:
                f.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        hashes[case + "_" + mode] = sha(p)
        if len(book.trades) != results[(case,mode)]["ledger_rows"]:
            raise ValueError("published per-case trade row mismatch")
    snap = out / "source_snapshot"
    snap.mkdir(mode=0o2770)
    sources = {}
    for rel in (SELF_REL, unit.SELF_REL, unit.ENTRY_REL, ORIGINAL_REL):
        p = SOURCE / rel
        target = snap / p.name
        shutil.copyfile(p, target)
        sources[p.name] = sha(target)
        if sources[p.name] != sha(p):
            raise RuntimeError("2025 portfolio source snapshot changed")
    report = {
        "research": "P90L90_BULL2025_PORTFOLIO_V1",
        "status": "INTERNAL_QC_PASS_PENDING_INDEPENDENT",
        "evaluation_utc": [EVAL_START.isoformat(), EVAL_END.isoformat()],
        "source_input_days": 193, "evaluation_days": 183,
        "symbols": list(SYMBOLS), "side": side,
        "native_ticks_evaluation": n,
        "source_rows_including_warmup": 204446371,
        "source_head": source_head, "source_sha256": sources,
        "unit_shadow_sha256": {s: unit_reference[s]["sha"] for s in SYMBOLS},
        "unlimited_no_pause_per_coin_shadow_parity": True,
        "start_wallet_each_case_usdt": str(START_WALLET),
        "cash_modes": list(CASH_MODES), "cases": list(SCENARIOS),
        "fee_taker": str(TAKER), "fee_maker_resting_tp": str(MAKER),
        "leverage": str(LEVERAGE), "fixed_margin_usdt": str(FIXED_MARGIN),
        "compound_margin_rule": "min(20% of current wallet, fee-covered available margin)",
        "W7": {
            "thresholds_bps": list(THRESHOLDS), "min_prior_complete_7d_closes": 20,
            "pause_hours": 24,
            "trigger_only_new_no_pause_shadow_hardstop": True,
            "number_signals_by_threshold": {str(k):len(v) for k,v in schedule.items()},
        },
        "SECOND48": {
            "trigger": ">=2 actual scenario hardstops in previous rolling 48 hours",
            "embargo": "new entries only 48 hours, no forced flatten",
        },
        "unmodeled": [
            "funding","bid_ask_spread","slippage","latency",
            "tick_size_and_lot_size_rounding","exchange_liquidation",
            "orderbook_queue","actual_venue_order_fills",
        ],
        "censored_not_forced_closed": True,
        "daily_drawdown_is_eod_sampled_not_intraday": True,
        "results": {case+"_"+mode: r for (case,mode),r in results.items()},
        "ledger_sha256": hashes,
    }
    (out / "result.json").write_text(json.dumps(report, sort_keys=True, indent=2)+"\n")
    with (out / "qc.json").open("x") as f:
        json.dump({"status":"INTERNAL_QC_PASS_PENDING_INDEPENDENT",
                   "result_sha256": sha(out/"result.json"),
                   "source_head": source_head, "ledger_sha256": hashes,
                   "native_ticks": n, "no_pause_shadow_parity": True}, f,sort_keys=True,indent=2)
        f.write("\n")
    (out/"source_commit.txt").write_text(source_head+"\n")
    print(json.dumps({"stage":"2025_PORTFOLIO_INTERNAL_QC_PASS",
                      "side":side,"source_ticks":n,
                      "result":str(out/"result.json")}),flush=True)
    return report


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--side", choices=SIDES, required=True)
    p.add_argument("--source-head", required=True)
    p.add_argument("--smoke-hours", type=int, default=0)
    args = p.parse_args()
    if not 0 <= args.smoke_hours <= 24:
        raise ValueError("smoke-hours outside 0..24")
    result = run(args.side, args.source_head, args.smoke_hours)
    if args.smoke_hours:
        print(json.dumps(result, sort_keys=True),flush=True)


if __name__=="__main__":
    main()
