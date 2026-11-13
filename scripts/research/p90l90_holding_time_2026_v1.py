#!/usr/bin/env python3
"""Research-only P90L90 2026 holding-time native replay, Issue #646.

Purpose: change ONLY maximum open-position age, not Entry, structural TP,
dynamic TP repositioning, TRAIL035, HARD_STOP or SECOND48. The original
opposite RAW L5-3 extremum is the TP. +0.50% is the MINIMUM favorable gross
eligibility for arming/replacing that structural TP, never a fixed TP price.

FS contract: writer cripta uid107 gid109, interpreter /usr/bin/python3 -I.
Read-only source imports from the GitHub-main synchronized operational mirror;
read-only archived native 29d/90d ticks, frozen geometry and research ledgers.
The only writes are under the isolated
/data/cripta/jobs/running/C2-R646-H{29,90}-*/bundle job spool, which the
independent cripta-job-runner relocates to completed/failed. No database,
system disk research output, Exchange, runtime, deploy, LIVE, branch reset,
source mirror write or service mutation. No deletes or external rename.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
import pathlib
import sys
import time
from collections import Counter
from decimal import Decimal as D

SOURCE = pathlib.Path("/srv/cripta/source_checkout")
sys.path.insert(0, str(SOURCE))

from scripts.research import p90l90_exit_90d_v2 as base
from scripts.research import p90l90_portfolio_pause_v1 as ninety
from scripts.research import p90l90_entry_only_hardstop_embargo_v1 as embargo
from scripts.research import p90l90_29d_exit_replay_v1 as sep

RESEARCH = "P90L90_HOLDING_TIME_INDEPENDENT_2026_V1"
SIDES = ("LONG_ONLY", "SHORT_ONLY")
COINS = ("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT")
CAPS_H = (None, 3, 6, 12, 18, 24, 36, 48, 72)
PHASES = ("ALL", "NEG")
JOB_ID = os.environ.get("CRIPTA_JOB_ID", "")
ROOT = pathlib.Path.cwd().resolve()
ORIGINAL_ON_TRADE = base.on_trade
ORIGINAL_TP_ELIGIBILITY = base.take_profit_eligible

def digest(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()

def cap_label(cap: int | None) -> str:
    return "NO_CAP" if cap is None else f"H{cap:02d}"

def age_gate(pos, seq: int, timestamp: float, cap: int | None) -> bool:
    return (cap is not None and seq > pos.entry_seq
            and timestamp >= pos.entry_ts + cap * 3600)

def force_time_exit(pos, seq: int, timestamp: float, price: D,
                    cap: int | None, phase: str) -> bool:
    if not age_gate(pos, seq, timestamp, cap):
        return False
    if phase == "ALL":
        return True
    if phase == "NEG":
        # A sell/buy at native print is assumed a MARKET / TAKER close.
        # The fee-inclusive closeable net is evaluated AS OF THIS tick.
        return base.gross_and_net(pos, price, False)[-1] <= 0
    raise ValueError("unsupported research-only time phase")

def original_resting_exit_due(pos, price: D, seq: int) -> bool:
    """Preserve pre-existing stop/TP on deadline collisions; no new TP arm."""
    if seq <= pos.entry_seq:
        return False
    if base.hits_stop(pos.side, price, pos.stop):
        return True
    return (pos.tp is not None and seq > pos.tp_live_after_seq
            and base.hits_tp(pos.side, price, pos.tp))


class TimeBook(embargo.EntryEmbargo):
    """One isolated LONG or SHORT book, including its own SECOND48."""

    def __init__(self, cap: int | None, phase: str):
        super().__init__(2, 48)
        self.cap = cap
        self.phase = phase

    def handle(self, symbol, timestamp, seq, price, candidates, state):
        # The runner is single-threaded; restoration occurs even on failure.
        # This intercepts only the BEFORE-entry native Exit decision.
        if base.on_trade is not ORIGINAL_ON_TRADE:
            raise RuntimeError("unexpected concurrent Exit callback modification")

        def with_time_gate(pos, px, source_seq, bucket, tp, kind):
            if original_resting_exit_due(pos, px, source_seq):
                return ORIGINAL_ON_TRADE(pos, px, source_seq, bucket, tp, kind)
            if force_time_exit(pos, source_seq, timestamp, px,
                               self.cap, self.phase):
                return None, ("TIME_LIMIT", px, False)
            return ORIGINAL_ON_TRADE(pos, px, source_seq, bucket, tp, kind)

        base.on_trade = with_time_gate
        try:
            return super().handle(symbol, timestamp, seq, price,
                                  candidates, state)
        finally:
            base.on_trade = ORIGINAL_ON_TRADE

def preflight(period: str, phase: str) -> pathlib.Path:
    if period not in ("29d", "90d") or phase not in PHASES:
        raise ValueError("unsupported research period or phase")
    if os.geteuid() != 107 or os.getegid() != 109:
        raise PermissionError("actual actor must be cripta uid107 gid109")
    prefix = "C2-R646-H29-" if period == "29d" else "C2-R646-H90-"
    if not JOB_ID.startswith(prefix):
        raise RuntimeError("wrong durable job identity")
    expected = pathlib.Path("/data/cripta/jobs/running") / JOB_ID / "bundle"
    if ROOT != expected or ROOT.is_symlink() or not ROOT.is_dir():
        raise PermissionError("research worker is outside exact job spool")
    if ROOT.stat().st_uid != 107 or not os.access(ROOT, os.W_OK | os.X_OK):
        raise PermissionError("job output path inaccessible to cripta")
    if os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize < 5 * 2**30:
        raise RuntimeError("research data disk below 5 GiB reserve")
    source = ROOT / "source_snapshot" / "p90l90_holding_time_2026_v1.py"
    if (not source.is_file() or source.is_symlink()
            or source.resolve() != pathlib.Path(__file__).resolve()):
        raise RuntimeError("unverified frozen research driver")
    if (ROOT / period).exists():
        raise FileExistsError("immutable result directory collision")
    if base.take_profit_eligible is not ORIGINAL_TP_ELIGIBILITY:
        raise RuntimeError("structural TP eligibility was modified")
    if base.on_trade is not ORIGINAL_ON_TRADE:
        raise RuntimeError("baseline Exit callback was modified")
    return source

def inputs(period: str):
    if period == "29d":
        data = {s: sep.read_inputs(s) for s in COINS}
        streams = {s: sep.native_ticks(data[s]) for s in COINS}
        start, end = sep.entry.START_TS, sep.entry.END_TS
    else:
        data = {s: ninety.inputs(s, ninety.RAW_DEFAULT,
                                ninety.FROZEN_DEFAULT) for s in COINS}
        streams = {s: ninety.ticks(data[s]) for s in COINS}
        start, end = ninety.START_TS, ninety.END_TS
    refjob = "C2-R635-TP29-FULL2" if period == "29d" else "C2-R635-TP90-FULL2"
    refroot = pathlib.Path("/data/cripta/jobs/completed") / refjob / "bundle" / period
    refmanifest = json.loads((refroot / "result.json").read_text())
    if refmanifest["baseline_050_exact_ledger_parity"] is not True:
        raise RuntimeError("baseline independent source QC missing")
    original = refmanifest.get("scenarios", refmanifest.get("books"))
    references = {}
    for side in SIDES:
        path = refroot / f"{side.lower()}_floor0p50.jsonl"
        if digest(path) != original[side + "|0.50"]["ledger_sha256"]:
            raise ValueError("original 0.50 structural TP ledger SHA mismatch")
        references[side] = [json.loads(z) for z in path.open() if z.strip()]
    return data, streams, start, end, references, refjob

def run(period: str, phase: str, smoke_hours: int) -> dict:
    src = preflight(period, phase)
    data, streams, start, end, references, refjob = inputs(period)
    until = min(end, start + smoke_hours * 3600) if smoke_hours else end
    books = {(side, cap): TimeBook(cap, phase)
             for side in SIDES for cap in CAPS_H}
    assert len(books) == 18 and len(set(map(id, books.values()))) == 18
    heap = []
    for priority, coin in enumerate(COINS):
        tick = next(streams[coin], None)
        if tick is None:
            raise ValueError("missing native Bybit stream " + coin)
        heapq.heappush(heap, (tick[0], priority, tick))

    total_ticks = 0
    last_day = None
    began = time.monotonic()
    heartbeat = began
    while heap:
        t, priority, (_, seq, price, trade_id) = heapq.heappop(heap)
        if t >= until:
            break
        coin = COINS[priority]
        candidates = data[coin]["events"].get(seq, ())
        state = data[coin]["states"].get(int(t // 300) * 300)
        by_side = {
            side: tuple(e for e in candidates if e["side"] == side.split("_")[0])
            for side in SIDES
        }
        for (side, _), book in books.items():
            selected = by_side[side]
            if selected or book.pos[coin] is not None:
                book.handle(coin, t, seq, price, selected, state)
        total_ticks += 1
        utc_day = int(t // 86400)
        if utc_day != last_day:
            last_day = utc_day
            print(json.dumps({"phase": "DAY", "period": period, "mode": phase,
                "utc_day_epoch": utc_day, "native_ticks": total_ticks,
                "seconds_elapsed": round(time.monotonic() - began, 1)}),
                flush=True)
        if time.monotonic() - heartbeat >= 25:
            print(json.dumps({"phase": "HEARTBEAT", "period": period,
                "mode": phase, "native_ticks": total_ticks,
                "seconds_elapsed": round(time.monotonic() - began, 1)}),
                flush=True)
            heartbeat = time.monotonic()
        nxt = next(streams[coin], None)
        if nxt is not None:
            heapq.heappush(heap, (nxt[0], priority, nxt))

    if base.on_trade is not ORIGINAL_ON_TRADE:
        raise RuntimeError("Exit function was not restored")

    if smoke_hours:
        return {"status": "SMOKE_PASS_NO_WRITES", "period": period,
                "phase": phase, "native_ticks": total_ticks,
                "scenarios": len(books)}

    # The exact NO_CAP legacy structural-TP books must reproduce 2 original
    # 2026 fee-inclusive ledgers byte-for-byte at the record level.
    for side in SIDES:
        produced = books[(side, None)].trades
        if produced != references[side]:
            raise RuntimeError("NO_CAP exact ledger parity failed " + period
                               + " " + side + " expected=" + str(len(references[side]))
                               + " got=" + str(len(produced)))
    print(json.dumps({"phase": "NO_CAP_PARITY_PASS", "period": period,
        "long_trades": len(books[("LONG_ONLY", None)].trades),
        "short_trades": len(books[("SHORT_ONLY", None)].trades)}),
        flush=True)

    result = {
        "research": RESEARCH, "status": "RESEARCH_INTERNAL_QC_PASS",
        "period": period, "phase": phase, "job_id": JOB_ID,
        "original_reference_job": refjob,
        "original_raw_l53_dynamic_tp": True,
        "gross_tp_eligibility_pct": "0.50",
        "fixed_percentage_take_profit": False,
        "notional_usdt_per_entry": "10",
        "exit_execution": ("first original native tick at/after deadline; "
                           "pre-existing Stop/resting TP first, then TIME_LIMIT taker; "
                           "same-tick reopening blocked; NEG mode checks immediate "
                           "model net at every subsequent native tick"),
        "source_sha256": digest(src), "caps_h": list(CAPS_H),
        "native_ticks": total_ticks, "baseline_exact_ledger_parity": True,
        "scenarios": {}, "unmodeled": [
            "bid_ask_spread", "slippage", "funding", "latency", "real_exchange_fills"],
    }
    output = ROOT / period
    output.mkdir(mode=0o770)
    for (side, cap), book in books.items():
        summary = book.finish()
        trades = book.trades
        if summary["counts"]["closed"] != len(trades):
            raise ValueError("closed trade count mismatch")
        amount = sum((D(row["net_usdt"]) for row in trades), D(0))
        if amount != D(summary["money"]["net_usdt"]):
            raise ValueError("scenario PnL parity failed")
        if summary["counts"]["accepted"] != (
                len(trades) + summary["counts"]["censored_open"]):
            raise ValueError("scenario position accounting failed")
        label = side + "|" + cap_label(cap)
        file = output / (side.lower() + "_" + cap_label(cap).lower() + ".jsonl")
        with file.open("x", encoding="utf-8") as fd:
            for row in trades:
                fd.write(json.dumps(row, sort_keys=True,
                                    separators=(",", ":")) + "\n")
            fd.flush()
            os.fsync(fd.fileno())
        orig = books[(side, None)]
        orig_ids = {r["candidate_event_id"] for r in orig.trades}
        orig_ids.update(x["entry_event_id"] for x in orig.finish()["censored"])
        own_ids = {r["candidate_event_id"] for r in trades}
        own_ids.update(x["entry_event_id"] for x in summary["censored"])
        result["scenarios"][label] = {
            "counts": summary["counts"], "money": summary["money"],
            "net_after_fees_usdt": str(amount),
            "ledger_sha256": digest(file),
            "extra_entries_not_in_uncapped": len(own_ids - orig_ids),
            "missing_original_entries": len(orig_ids - own_ids),
            "extra_hardstops_from_new_entries": sum(
                r["exit_reason"] == "HARD_STOP"
                and r["candidate_event_id"] not in orig_ids for r in trades),
            "time_exit_net_positive": sum(
                r["exit_reason"] == "TIME_LIMIT"
                and D(r["net_usdt"]) > 0 for r in trades),
            "time_exit_net_negative": sum(
                r["exit_reason"] == "TIME_LIMIT"
                and D(r["net_usdt"]) < 0 for r in trades),
            "censored": summary["censored"],
        }
    with (output / "result.json").open("x", encoding="utf-8") as fd:
        json.dump(result, fd, indent=2, sort_keys=True, ensure_ascii=False)
        fd.write("\n")
        fd.flush()
        os.fsync(fd.fileno())
    return {"status": result["status"], "period": period, "phase": phase,
            "native_ticks": total_ticks,
            "scenarios": len(result["scenarios"]),
            "control_parity": True,
            "result_path": str(output / "result.json")}

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", choices=("29d", "90d"), required=True)
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--smoke-hours", type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.smoke_hours <= 2:
        raise ValueError("smoke must be 0..2 source hours")
    print(json.dumps(run(args.period, args.phase, args.smoke_hours),
                     sort_keys=True), flush=True)

if __name__ == "__main__":
    main()
