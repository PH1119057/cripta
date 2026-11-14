#!/usr/bin/env python3
"""Research-only 183-day P90L90 independent opposite-loss3 Exit replay, #690.

Exact original TP = opposite RAW L5-3 structural high/low, moving only on
eligible new closed 5m zone. Favorable +0.50% is the MINIMUM structural TP
eligibility, never a fixed TP target. Only current opposite-book loss >=3.00% at new Entry changes Exit.
Original PREV7D / TRAIL035 / hardstop -10% / independent SECOND48 remain.

Actor/path matrix: cripta UID107:GID109; read-only 2025 193d Bybit source,
frozen geometry, Entry candidates and previous #635 183d QC baseline.
Create/write only bundle/2025 and bundle/2025/checkpoint under
/data/cripta/jobs/running/C2-R690-X25-*/bundle; checkpoint pending->final
rename only inside its own exact parent. No delete, no root/system disk
research output, no DB/Exchange/runtime/Strategy/LIVE changes. Independent
existing cripta-job-runner worker, never an untracked SentinelX exec child.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import time
from collections import Counter
from decimal import Decimal as D

SOURCE = pathlib.Path("/srv/cripta/source_checkout")
sys.path.insert(0, str(SOURCE))
from scripts.research import p90l90_bull2025_portfolio_v1 as old
from scripts.research import p90l90_bull2025_portfolio_chunked_v2 as engine
from scripts.research import p90l90_exit_90d_v2 as base
from scripts.research import p90l90_bull2025_entry_v1 as entry

RESEARCH = "P90L90_OPPOSITE_LOSS3_2025_STRICT0_V1"
JOB_ID = os.environ.get("CRIPTA_JOB_ID", "")
ROOT = pathlib.Path.cwd().resolve()
OUT = ROOT / "2025"
CHECKPOINT = OUT / "checkpoint"
SIDES = ("LONG_ONLY", "SHORT_ONLY")
CAPS_H = (None, "OPP3")
PHASES = ("ALL", "NEG")
ORIGINAL_HANDLE = old.Book.handle
ORIGINAL_ON_TRADE = base.on_trade
ORIGINAL_INIT = base.initial_position
ORIGINAL_ELIGIBLE = base.take_profit_eligible

def digest(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()

def label(cap: int | None) -> str:
    return "NO_CAP" if cap is None else "OPP3"


PAIRED_BOOKS = None
LAST_NATIVE_TICK = None


def alternate_trade(pos, px, seq, bucket, candidate_tp, kind):
    """Only flagged positions get fixed counterposition TP and +1.2/1.0 trail."""
    if not getattr(pos, "_r690_special", False):
        return ORIGINAL_ON_TRADE(pos, px, seq, bucket, candidate_tp, kind)
    if seq <= pos.entry_seq:
        return pos, None
    if base.hits_stop(pos.side, px, pos.stop):
        return None, ("PROTECTED_STOP" if pos.protected else "HARD_STOP", px, False)
    if pos.tp is not None and seq > pos.tp_live_after_seq and base.hits_tp(pos.side, px, pos.tp):
        return None, ("RESTING_TP", pos.tp, True)
    # No future RAW L5-3 replacement of frozen opposite original hardstop target.
    pos.state_bucket = bucket
    fav = base.favorable(pos.side, pos.entry, px)
    if fav > pos.best_favorable:
        pos.best_favorable = fav
    if pos.best_favorable >= D("1.2"):
        proposed = base.threshold_price(pos.side, pos.entry,
                                        pos.best_favorable - D("1.0"))
        tightens = proposed > pos.stop if pos.side == "LONG" else proposed < pos.stop
        if tightens:
            pos.stop = proposed
            pos.protected = True
            pos.protection_seq = seq
    return pos, None


def apply_original_book(book, symbol, timestamp, seq, px, events, state,
                        priority, opponent=None):
    """Temporarily install only the exact one-book research Exit callbacks."""
    if (base.on_trade is not ORIGINAL_ON_TRADE
            or base.initial_position is not ORIGINAL_INIT
            or base.take_profit_eligible is not ORIGINAL_ELIGIBLE):
        raise RuntimeError("shared research engine patched by another worker")
    pending = None
    opponent_pnl = None
    if book.cap_h == "OPP3" and opponent is not None:
        opponent_pnl = base.favorable(opponent.side, opponent.entry, px)
        if opponent_pnl <= D("-3"):
            pending = base.threshold_price(opponent.side, opponent.entry,
                                           D("-10"))
            new_side = "LONG" if opponent.side == "SHORT" else "SHORT"
            if base.favorable(new_side, px, pending) <= D(0):
                raise ValueError("opposite original catastrophic target not favorable")

    created = None
    def initial(ev, entry_seq, bucket, cohort, kind):
        nonlocal created
        p = ORIGINAL_INIT(ev, entry_seq, bucket, cohort, kind)
        created = p
        if pending is not None:
            if p.side == opponent.side:
                raise RuntimeError("opposite book does not match new Entry")
            p._r690_special = True
            p.tp = pending
            p.tp_live_after_seq = entry_seq
            book.special_ids.add(p.entry_event_id)
            book.special_entries.append({
                "symbol": symbol, "side": p.side,
                "entry_ts": repr(timestamp),
                "entry_event_id": p.entry_event_id,
                "entry_price": str(p.entry),
                "opponent_entry_event_id": opponent.entry_event_id,
                "opponent_entry_price": str(opponent.entry),
                "opponent_unrealized_gross_pct": str(opponent_pnl),
                "target_price": str(pending),
            })
        return p

    def eligible(side, entry_px, raw_tp):
        if created is not None and getattr(created, "_r690_special", False):
            return False
        return ORIGINAL_ELIGIBLE(side, entry_px, raw_tp)

    base.on_trade = alternate_trade
    base.initial_position = initial
    base.take_profit_eligible = eligible
    try:
        return ORIGINAL_HANDLE(book, symbol, timestamp, seq, px,
                               events, state, priority)
    finally:
        base.on_trade = ORIGINAL_ON_TRADE
        base.initial_position = ORIGINAL_INIT
        base.take_profit_eligible = ORIGINAL_ELIGIBLE


def paired_handle(self, symbol, timestamp, seq, px, events, state, priority):
    """One native tick: settle BOTH existing sides, then snapshot before Entries."""
    global LAST_NATIVE_TICK
    token = (symbol, seq)
    if token == LAST_NATIVE_TICK:
        return self.pos[symbol] is not None
    LAST_NATIVE_TICK = token
    if PAIRED_BOOKS is None or len(PAIRED_BOOKS) != 4:
        raise RuntimeError("paired research book session not initialized")
    selected = {
        side: tuple(e for e in events if
                    e["side"] == side.split("_")[0] and e["PREV7D"] is True
                    and D(str(e["depth_pct"])) == D(0))
        for side in SIDES
    }
    for cap in CAPS_H:
        closed_now = {}
        for side in SIDES:
            book = PAIRED_BOOKS[side + "|" + label(cap)]
            was_open = book.pos[symbol] is not None
            if was_open:
                apply_original_book(book, symbol, timestamp, seq, px, (),
                                    state, priority)
            closed_now[side] = was_open and book.pos[symbol] is None
        snapshot = {side: PAIRED_BOOKS[side + "|" + label(cap)].pos[symbol]
                    for side in SIDES}
        for side, opposite in (("LONG_ONLY","SHORT_ONLY"),
                               ("SHORT_ONLY","LONG_ONLY")):
            incoming = selected[side]
            if not incoming:
                continue
            book = PAIRED_BOOKS[side + "|" + label(cap)]
            if closed_now[side] or book.pos[symbol] is not None:
                # No second Exit call within the same source tick.
                book.counts["qualified_candidate"] += len(incoming)
                book.counts["busy_or_same_tick"] += len(incoming)
            else:
                apply_original_book(book, symbol, timestamp, seq, px,
                                    incoming, state, priority,
                                    opponent=snapshot[opposite])
    return self.pos[symbol] is not None


def preflight(phase: str, pilot_days: int) -> pathlib.Path:
    if phase not in PHASES or pilot_days not in (0, 1):
        raise ValueError("invalid research phase/pilot")
    if os.geteuid() != 107 or os.getegid() != 109:
        raise PermissionError("research writer must be cripta")
    if not JOB_ID.startswith("C2-R690-X25-"):
        raise RuntimeError("unapproved research job identity")
    expected = pathlib.Path("/data/cripta/jobs/running") / JOB_ID / "bundle"
    if ROOT != expected or ROOT.is_symlink() or not ROOT.is_dir():
        raise PermissionError("outside independent research spool")
    if ROOT.stat().st_uid != 107 or not os.access(ROOT, os.W_OK | os.X_OK):
        raise PermissionError("invalid exact job writer ownership")
    if os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize < 5 * 2**30:
        raise RuntimeError("research data disk reserve <5 GiB")
    src = ROOT / "source_snapshot" / "p90l90_opposite_loss3_exit_2025_v1.py"
    if (not src.is_file() or src.is_symlink()
            or pathlib.Path(__file__).resolve() != src.resolve()):
        raise RuntimeError("frozen executable source mismatch")
    if pilot_days and OUT.exists():
        raise FileExistsError("smoke is no-write and requires empty output")
    if OUT.is_symlink():
        raise RuntimeError("existing output must not be symlink")
    if base.take_profit_eligible is not ORIGINAL_ELIGIBLE:
        raise RuntimeError("structural TP eligibility overwritten")
    if base.on_trade is not ORIGINAL_ON_TRADE:
        raise RuntimeError("unexpected original Exit mutation")
    if old.Book.handle is not ORIGINAL_HANDLE:
        raise RuntimeError("original 2025 book mutated before scope")
    return src

def sources() -> dict:
    data = {}
    for symbol in old.SYMBOLS:
        folder = old.RESULTS / "entry_full_2025_v1" / symbol
        manifest = json.loads((folder / "manifest.json").read_text())
        candidate = folder / f"candidates_{symbol}.jsonl"
        frozen = old.FROZEN_PARENT / f"states_{symbol}.csv.gz"
        if manifest["symbol"] != symbol or len(manifest["inputs"]) != 193:
            raise RuntimeError("2025 original 193d source mismatch")
        if (digest(candidate) != manifest["events_sha256"]
                or digest(frozen) != manifest["frozen_l53_sha256"]):
            raise RuntimeError("frozen source hash failure")
        states, _, median7, meta = entry.load_frozen_period(
            frozen, old.EVAL_START, old.EVAL_END)
        if len(states) != 183 * 288 or len(median7) != 183:
            raise ValueError("2025 complete geometry/median missing")
        events, counts = base.read_events(candidate, symbol)
        if counts["raw_candidates"] != manifest["events"]:
            raise ValueError("candidate event count mismatch")
        data[symbol] = {"manifest": manifest, "events": events,
                        "states": states, "symbol": symbol}
    return data

def new_books(phase: str) -> dict:
    result = {}
    for side in SIDES:
        for cap in CAPS_H:
            b = old.Book(side, "SECOND48", "fixed", [])
            b.cap_h = cap
            b.phase = phase
            b.special_entries = []
            b.special_ids = set()
            result[side + "|" + label(cap)] = b
    assert len(result) == 4 and len(set(map(id, result.values()))) == 4
    return result

def save_day(day: int, state: dict, sha: str) -> str:
    path = CHECKPOINT / f"day_{day:03d}.json"
    pending = CHECKPOINT / f"day_{day:03d}.pending"
    if path.exists() or pending.exists():
        raise FileExistsError("checkpoint collision or interrupted pending")
    item = {"research": RESEARCH, "day_index": day,
            "next_day": state["next_day"], "driver_sha256": sha,
            "state": engine.encode(state)}
    with pending.open("x", encoding="utf-8") as stream:
        json.dump(item, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    pending.rename(path)
    return digest(path)

def resume(books, src_sha: str, data: dict) -> tuple[dict, int]:
    files = sorted(CHECKPOINT.glob("day_*.json"))
    if list(CHECKPOINT.glob("*.pending")):
        raise RuntimeError("incomplete pending checkpoint requires forensic")
    if not files:
        return engine.blank_state(data), 0
    if len(files) > 183:
        raise ValueError("excess checkpoint count")
    for i, p in enumerate(files):
        if p.name != f"day_{i:03d}.json":
            raise RuntimeError("non-contiguous 2025 checkpoint")
    item = json.loads(files[-1].read_text())
    if (item["research"] != RESEARCH or
            item["driver_sha256"] != src_sha or
            item["day_index"] != len(files) - 1 or
            item["next_day"] != len(files)):
        raise RuntimeError("checkpoint source/day identity mismatch")
    state = engine.decode(item["state"])
    if state["next_day"] != len(files):
        raise RuntimeError("invalid checkpoint state")
    engine.restore_books(books, state["books"])
    extra = state.get("r690_extra")
    if not isinstance(extra, dict) or set(extra) != set(books):
        raise RuntimeError("paired checkpoint missing opposite-special metadata")
    for key, book in books.items():
        item = extra[key]
        book.cap_h = None if key.endswith("NO_CAP") else "OPP3"
        book.special_entries = item["special_entries"]
        book.special_ids = set(item["special_ids"])
        for symbol in item["active_special"]:
            p = book.pos[symbol]
            if p is None:
                raise RuntimeError("checkpoint special position missing")
            p._r690_special = True
    return state, len(files)

def run(phase: str, pilot_days: int) -> dict:
    src = preflight(phase, pilot_days)
    old.START_WALLET = D("1000000000000")
    old.FIXED_MARGIN = D("20")
    old.LEVERAGE = D("5")
    if old.FIXED_MARGIN * old.LEVERAGE != D("100"):
        raise RuntimeError("2025 fixed nominal no longer $100")
    data = sources()
    global PAIRED_BOOKS, LAST_NATIVE_TICK
    books = new_books(phase)
    PAIRED_BOOKS, LAST_NATIVE_TICK = books, None
    if pilot_days:
        state, start = engine.blank_state(data), 0
    else:
        OUT.mkdir(mode=0o770, exist_ok=True)
        CHECKPOINT.mkdir(mode=0o770, exist_ok=True)
        state, start = resume(books, digest(src), data)
    began = time.monotonic()
    old.Book.handle = paired_handle
    try:
        stop = min(183, start + pilot_days) if pilot_days else 183
        for day in range(start, stop):
            # Keep the original five-symbol native source chronology.
            engine.replay_day(day, state, data, books, "LONG_ONLY")
            if not pilot_days:
                state["r690_extra"] = {
                    key: {"special_entries": book.special_entries,
                          "special_ids": sorted(book.special_ids),
                          "active_special": [
                              symbol for symbol, p in book.pos.items()
                              if p is not None and getattr(p, "_r690_special", False)]}
                    for key, book in books.items()
                }
            if not pilot_days:
                checkpoint = save_day(day, state, digest(src))
                print(json.dumps({"stage": "DAY_PERSISTED", "day": day + 1,
                    "of": 183, "native_ticks": state["ticks"],
                    "checkpoint_sha256": checkpoint,
                    "seconds_elapsed": round(time.monotonic() - began, 1)}),
                    flush=True)
    finally:
        old.Book.handle = ORIGINAL_HANDLE
        PAIRED_BOOKS = None
        if (base.on_trade is not ORIGINAL_ON_TRADE
                or base.initial_position is not ORIGINAL_INIT
                or base.take_profit_eligible is not ORIGINAL_ELIGIBLE):
            raise RuntimeError("native Exit callbacks not restored")
    if pilot_days:
        return {"status": "SMOKE_PASS_NO_WRITES", "phase": phase,
                "days": pilot_days, "native_ticks": state["ticks"],
                "scenarios": len(books)}
    if state["next_day"] != 183:
        raise RuntimeError("incomplete 183d replay")
    baseline = {}
    for side in SIDES:
        book = books[side + "|NO_CAP"]
        refpath = (old.RESULTS / "portfolio_depthzero_2025_v1" / "full"
                   / side.lower() / "ledger_SECOND48_fixed.jsonl")
        reference = [json.loads(r) for r in refpath.open() if r.strip()]
        if book.trades != reference:
            raise RuntimeError("2025 NO_CAP exact original baseline failed " + side)
        baseline[side] = len(reference)
    print(json.dumps({"stage": "NO_CAP_PARITY_PASS", "long": baseline["LONG_ONLY"],
        "short": baseline["SHORT_ONLY"]}), flush=True)
    scenarios = {}
    for key, book in books.items():
        summary = book.finish(state["prices"], old.END_TS)
        if summary["ledger_rows"] != len(book.trades):
            raise ValueError("2025 closed ledger count mismatch")
        net = sum((D(r["net_usdt"]) for r in book.trades), D(0))
        if net != D(summary["closed_net_after_fees_usdt"]):
            raise ValueError("2025 net fee/ledger discrepancy")
        side, cap = key.split("|")
        file = OUT / f"{side.lower()}_{cap.lower()}.jsonl"
        with file.open("x", encoding="utf-8") as fd:
            for row in book.trades:
                fd.write(json.dumps(row, sort_keys=True,
                                    separators=(",", ":")) + "\n")
            fd.flush()
            os.fsync(fd.fileno())
        scenarios[key] = {
            "counts": summary["counts"],
            "money": summary["money"],
            "net_after_fees_usdt": str(net),
            "wallet_delta_includes_open_entry_fees": summary[
                "wallet_change_includes_open_entry_fees_usdt"],
            "censored": summary["open_censored"],
            "ledger_sha256": digest(file),
            "special_entries": book.special_entries,
            "special_count": len(book.special_entries),
            "special_closed": sum(
                r["candidate_event_id"] in book.special_ids
                for r in book.trades),
            "net_taker_taker_same_fill_proxy_usdt": str(sum(
                (D(r["gross_usdt"]) - D(r["entry_fee_usdt"]) -
                 old.money(D(r["notional_usdt"]) *
                           D(r["exit_price"]) / D(r["entry_price"]) *
                           old.TAKER))
                for r in book.trades
            , D(0))),
            "censored": summary["open_censored"],
        }
    final = {
        "status": "RESEARCH_INTERNAL_QC_PASS", "research": RESEARCH,
        "period": "2025_183D", "phase": phase, "job_id": JOB_ID,
        "entry": "original 2025 strict depth zero, PREV7D, +/-0.50pp",
        "original_raw_l53_dynamic_tp": True,
        "gross_tp_eligibility_pct": "0.50",
        "fixed_percentage_take_profit": False,
        "same_tick_priority": "both-side pre-existing Exit before any Entry",
        "trailing": "TRAIL035 unless opposite open <= -3.00%; then +1.20/1.00pp",
        "special_tp": "opposite initial -10% gross hypothetical hardstop",
        "SECOND48": "independent per side/book",
        "nominal_usdt_per_trade": "100",
        "source_sha256": digest(src),
        "native_ticks": state["ticks"], "days": 183,
        "exit_cases": [label(c) for c in CAPS_H],
        "baseline_exact_ledger_parity": True,
        "scenarios": scenarios,
        "unmodeled": ["spread", "slippage", "latency",
                      "funding", "exchange actual fills"],
    }
    with (OUT / "result.json").open("x", encoding="utf-8") as fd:
        json.dump(final, fd, indent=2, sort_keys=True, ensure_ascii=False)
        fd.write("\n")
        fd.flush()
        os.fsync(fd.fileno())
    return {"status": final["status"], "days": 183,
            "native_ticks": state["ticks"], "scenario_count": len(scenarios),
            "baseline": baseline, "result_path": str(OUT / "result.json")}

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--phase", required=True, choices=PHASES)
    p.add_argument("--pilot-days", type=int, default=0)
    args = p.parse_args()
    print(json.dumps(run(args.phase, args.pilot_days),
                     sort_keys=True), flush=True)

if __name__ == "__main__":
    main()
