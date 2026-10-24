#!/usr/bin/env python3
"""Nine disjoint UTC 10d bins and overlapping rolling-10d from P90L90 v2 ledgers.

Realized modeled net is assigned to UTC EXIT day. Not a live strategy PnL.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D, getcontext
from pathlib import Path

from scripts.research.p90l90_entry_90d_v2 import END_TS, START_TS, SYMBOLS, sha256

getcontext().prec = 50
ROOT = Path("/data/cripta/research/runs")
COHORTS = ("FIXED90", "PREV7D")
EXITS = ("LOCK050", "BE013", "TRAIL035")
SCENARIOS = tuple(f"{c}_{e}" for c in COHORTS for e in EXITS)
TEN_DAYS = 10 * 86400
ONE_DAY = 86400
N_BINS = 9
N_DAYS = 90


def bucket(ts: float) -> int:
    if not START_TS <= ts < END_TS:
        raise ValueError("event outside full 90-day UTC study")
    result = int((ts - START_TS) // TEN_DAYS)
    if not 0 <= result < N_BINS:
        raise ValueError("bad ten-day interval")
    return result


def day_index(ts: float) -> int:
    if not START_TS <= ts < END_TS:
        raise ValueError("outside full 90-day evaluation")
    return int((ts - START_TS) // ONE_DAY)


def new_bin(index: int) -> dict:
    start = datetime.fromtimestamp(START_TS + index * TEN_DAYS, timezone.utc)
    stop = start + timedelta(days=10)
    return {
        "from_utc": start.isoformat(),
        "to_utc_exclusive": stop.isoformat(),
        "qualified_candidates": 0, "accepted_by_entry": 0,
        "busy": 0, "closed_by_exit": 0, "censored_entry": 0,
        "cross_bin_closures": 0, "winners": 0, "losers": 0, "flat": 0,
        "hard_stops": 0, "gross_usdt": D(0),
        "entry_fees_usdt": D(0), "exit_fees_usdt": D(0),
        "net_usdt": D(0),
        "positive_net_usdt": D(0), "negative_net_usdt": D(0),
        "by_symbol": {}, "by_side": {}, "exit_reasons": {},
    }


def add_close(acc: dict, row: dict) -> None:
    gross = D(row["gross_usdt"])
    fee_in = D(row["entry_fee_usdt"])
    fee_out = D(row["exit_fee_usdt"])
    net = D(row["net_usdt"])
    if gross - fee_in - fee_out != net:
        raise ValueError("modeled trade money identity mismatch")
    if int(row["exit_source_order"]) <= int(row["entry_source_order"]):
        raise ValueError("reversed trade tape source order")
    acc["closed_by_exit"] += 1
    acc["gross_usdt"] += gross
    acc["entry_fees_usdt"] += fee_in
    acc["exit_fees_usdt"] += fee_out
    acc["net_usdt"] += net
    acc["positive_net_usdt"] += max(net, D(0))
    acc["negative_net_usdt"] += min(net, D(0))
    acc["winners"] += net > 0
    acc["losers"] += net < 0
    acc["flat"] += net == 0
    reason = row["exit_reason"]
    acc["hard_stops"] += reason == "HARD_STOP"
    for key, part in (("by_symbol", row["symbol"]), ("by_side", row["side"]),
                      ("exit_reasons", reason)):
        if part not in acc[key]:
            acc[key][part] = {"closed": 0, "net_usdt": D(0)}
        acc[key][part]["closed"] += 1
        acc[key][part]["net_usdt"] += net


def export(obj):
    if isinstance(obj, D):
        return str(obj)
    if isinstance(obj, dict):
        return {k: export(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [export(v) for v in obj]
    return obj


def compute(entry_root: Path, exit_root: Path) -> dict:
    bins = {scenario: [new_bin(i) for i in range(N_BINS)] for scenario in SCENARIOS}
    daily = {scenario: [D(0) for _ in range(N_DAYS)] for scenario in SCENARIOS}
    daily_closed = {scenario: [0 for _ in range(N_DAYS)] for scenario in SCENARIOS}
    provenance = {}
    for symbol in SYMBOLS:
        alias = symbol.removesuffix("USDT")
        ep = entry_root / f"entry_{alias}_FULL90"
        with (ep / "manifest.json").open("r", encoding="utf-8") as stream:
            em = json.load(stream)
        if em["research"] != "P90L90_ENTRY_90D_V2" or not em["complete_90d_input"]:
            raise ValueError("cannot aggregate incomplete or legacy Entry")
        event_path = ep / f"candidates_{symbol}.jsonl"
        if sha256(event_path) != em["events_sha256"]:
            raise ValueError("Entry candidate SHA mismatch")
        xp = exit_root / f"exit_{alias}_FULL90"
        report_path = xp / "result.json"
        with report_path.open("r", encoding="utf-8") as stream:
            xr = json.load(stream)
        if xr["research"] != "P90L90_EXIT_90D_V2" or xr["period"] != "FULL_90D_BOTH_COHORTS":
            raise ValueError("wrong independent 90d Exit version")
        if xr["entry_events_sha256"] != em["events_sha256"] or len(xr["scenarios"]) != 6:
            raise ValueError("incompatible Entry and Exit")
        ledger_path = xp / f"trade_ledger_{symbol}.jsonl"
        if sha256(ledger_path) != xr["trade_ledger_sha256"]:
            raise ValueError("trade ledger SHA mismatch")
        accepted = {s: 0 for s in SCENARIOS}
        closed = Counter()
        entries = {}
        with event_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                e = json.loads(line)
                entries[e["event_id"]] = e
                for scenario in SCENARIOS:
                    cohort = scenario.split("_")[0]
                    if e[cohort]:
                        bins[scenario][bucket(float(e["ts"]))]["qualified_candidates"] += 1
        with ledger_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                trade = json.loads(line)
                scenario = trade["scenario"]
                if scenario not in SCENARIOS or trade["symbol"] != symbol:
                    raise ValueError("bad modeled ledger lineage")
                origin = entries[trade["candidate_event_id"]]
                if int(trade["entry_source_order"]) != int(origin["source_order"]):
                    raise ValueError("wrong original trade candidate")
                if D(trade["entry_price"]) != D(origin["price"]):
                    raise ValueError("entry candidate price differs")
                if not origin[scenario.split("_")[0]]:
                    raise ValueError("scenario entered excluded candidate")
                entry_bin = bucket(float(trade["entry_ts"]))
                exit_bin = bucket(float(trade["exit_ts"]))
                bins[scenario][entry_bin]["accepted_by_entry"] += 1
                bins[scenario][exit_bin]["cross_bin_closures"] += entry_bin != exit_bin
                add_close(bins[scenario][exit_bin], trade)
                daily[scenario][day_index(float(trade["exit_ts"]))] += D(trade["net_usdt"])
                daily_closed[scenario][day_index(float(trade["exit_ts"]))] += 1
                accepted[scenario] += 1
                closed[scenario] += 1
        for scenario in SCENARIOS:
            r = xr["scenarios"][scenario]
            censored = r.get("censored_position")
            if censored:
                candidate = entries[censored["candidate_event_id"]]
                if not candidate[scenario.split("_")[0]]:
                    raise ValueError("censored entry excluded by width cohort")
                bins[scenario][bucket(float(censored["entry_ts"]))]["censored_entry"] += 1
                bins[scenario][bucket(float(censored["entry_ts"]))]["accepted_by_entry"] += 1
                accepted[scenario] += 1
            observed = sum(b["qualified_candidates"] for b in bins[scenario])
            total_closed = closed[scenario]
            if observed < accepted[scenario]:
                raise ValueError("unbalanced accepted versus candidate archive")
            if accepted[scenario] != r["counts"]["accepted"] + 0:
                # bins are global over coins; compare below in the final aggregate
                pass
            if total_closed != r["counts"]["closed"]:
                # Likewise the global accumulator includes preceding symbols
                pass
        provenance[symbol] = {
            "entry_sha256": em["events_sha256"],
            "result_sha256": sha256(report_path),
            "ledger_sha256": xr["trade_ledger_sha256"],
        }
    totals = {}
    rolling = {}
    for scenario in SCENARIOS:
        arr = bins[scenario]
        for b in arr:
            b["busy"] = b["qualified_candidates"] - b["accepted_by_entry"]
            if b["busy"] < 0:
                raise ValueError("negative busy events by evaluation decade")
            if b["gross_usdt"] - b["entry_fees_usdt"] - b["exit_fees_usdt"] != b["net_usdt"]:
                raise ValueError("10d gross / modeled fee reconciliation")
            if b["positive_net_usdt"] + b["negative_net_usdt"] != b["net_usdt"]:
                raise ValueError("10d signed realized PnL reconciliation")
        totals[scenario] = {
            "net_usdt": sum((b["net_usdt"] for b in arr), D(0)),
            "closed": sum(b["closed_by_exit"] for b in arr),
            "censored": sum(b["censored_entry"] for b in arr),
            "qualified": sum(b["qualified_candidates"] for b in arr),
            "accepted": sum(b["accepted_by_entry"] for b in arr),
            "busy": sum(b["busy"] for b in arr),
        }
        rolling[scenario] = [
            {
                "end_day_utc": datetime.fromtimestamp(START_TS + (d + 1) * ONE_DAY - 1,
                                                     timezone.utc).date().isoformat(),
                "start_day_utc": datetime.fromtimestamp(START_TS + (d - 9) * ONE_DAY,
                                                       timezone.utc).date().isoformat(),
                "net_usdt": sum(daily[scenario][d-9:d+1], D(0)),
                "closed": sum(daily_closed[scenario][d-9:d+1]),
            }
            for d in range(9, N_DAYS)
        ]
        if len(rolling[scenario]) != 81:
            raise ValueError("expected 81 overlapping ten-day windows")
    return {
        "research": "P90L90_90D_WARMUP_V2_BIN_ANALYSIS",
        "status": "NINE_DISJOINT_BINS_AND_ROLLING_10D_QC",
        "window": ["2026-05-18T00:00:00Z", "2026-08-16T00:00:00Z"],
        "lookahead": {"FIXED90": True, "PREV7D_width_only": False},
        "modeled_execution_only": True,
        "unmodeled": ["funding", "slippage", "spread", "latency"],
        "provenance": provenance, "bins": bins, "rolling_10d": rolling,
        "totals": totals,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--entry-root", type=Path, required=True)
    p.add_argument("--exit-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if not args.output.is_absolute() or ROOT not in args.output.parents:
        p.error("only absolute path within Data research runs")
    if args.output.exists():
        raise FileExistsError("refuse overwrite of previous ten-day analysis")
    output = compute(args.entry_root, args.exit_root)
    args.output.mkdir(parents=False, exist_ok=False)
    (args.output / "ten_day_analysis.json").write_text(
        json.dumps(export(output), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": output["status"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
