#!/usr/bin/env python3
"""P90L90 tick-proxy independent six-scenario Exit replay.

Consumes a frozen candidate archive emitted by p90l90_entry_v1, NEVER live orders.
No funding, spread, latency or modeled slippage; no exchange fill claim.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal as D, getcontext
from pathlib import Path

from scripts.research.p90l90_entry_v1 import (
    COMMON_TS,
    END_TS,
    FROZEN_DEFAULT,
    OUTPUT_ROOT,
    RAW_DEFAULT,
    SYMBOLS,
    load_frozen,
    sha256,
)

getcontext().prec = 50
NOMINAL = D("10")
TAKER = D("0.00055")
MAKER = D("0.0002")
EXITS = ("LOCK050", "BE013", "TRAIL035")
COHORTS = ("FIXED90", "PREV7D")
SCENARIOS = tuple((a, b) for a in COHORTS for b in EXITS)


def favorable(side: str, entry: D, current: D) -> D:
    return (current / entry - 1) * D(100) if side == "LONG" else (1 - current / entry) * D(100)


def threshold_price(side: str, entry: D, favorable_pct: D) -> D:
    return entry * (1 + favorable_pct / D(100)) if side == "LONG" else entry * (1 - favorable_pct / D(100))


def take_profit_eligible(side: str, entry: D, level: D) -> bool:
    return favorable(side, entry, level) >= D("0.5")


def hits_stop(side: str, px: D, level: D) -> bool:
    return px <= level if side == "LONG" else px >= level


def hits_tp(side: str, px: D, level: D) -> bool:
    return px >= level if side == "LONG" else px <= level


@dataclass
class Position:
    side: str
    entry: D
    entry_seq: int
    entry_ts: float
    state_bucket: int
    stop: D
    best_favorable: D = D(0)
    protected: bool = False
    protection_seq: int = -1
    tp: D | None = None
    tp_live_after_seq: int = -1


def initial_position(event: dict, seq: int, bucket: int, cohort: str, exit_kind: str):
    del cohort, exit_kind  # scenario identity lives in outer ledger
    side = event["side"]
    entry = D(event["price"])
    stop = threshold_price(side, entry, D("-10"))
    pos = Position(side=side, entry=entry, entry_seq=seq,
                   entry_ts=float(event["ts"]), state_bucket=bucket, stop=stop)
    return pos


def gross_and_net(pos: Position, exit_px: D, maker_exit: bool):
    """10 USDT entry notional; exit fee scales with exit notional."""
    exit_nominal = NOMINAL * exit_px / pos.entry
    gross = NOMINAL * (exit_px / pos.entry - 1)
    if pos.side == "SHORT":
        gross = -gross
    entry_fee = NOMINAL * TAKER
    exit_fee = exit_nominal * (MAKER if maker_exit else TAKER)
    return gross, entry_fee, exit_fee, gross - entry_fee - exit_fee


def on_trade(pos: Position, px: D, seq: int, bucket: int, candidate_tp: D | None,
             kind: str):
    """Return (possibly new position, exit), strictly event-order causal.

    Pre-existing stop and resting TP are tested *before* replacing a target.
    Protection activated by this record can fill only on a later record.
    """
    if seq <= pos.entry_seq:
        return pos, None
    if hits_stop(pos.side, px, pos.stop):
        return None, ("PROTECTED_STOP" if pos.protected else "HARD_STOP", px, False)
    if pos.tp is not None and seq > pos.tp_live_after_seq and hits_tp(pos.side, px, pos.tp):
        return None, ("RESTING_TP", pos.tp, True)

    if bucket != pos.state_bucket:
        pos.state_bucket = bucket
        if candidate_tp is not None and take_profit_eligible(pos.side, pos.entry, candidate_tp):
            if hits_tp(pos.side, px, candidate_tp):
                return None, ("MARKETABLE_TP", px, False)
            pos.tp = candidate_tp
            pos.tp_live_after_seq = seq

    f = favorable(pos.side, pos.entry, px)
    if f > pos.best_favorable:
        pos.best_favorable = f
    if pos.best_favorable >= D("0.5"):
        if kind == "LOCK050":
            proposed = threshold_price(pos.side, pos.entry, D("0.5"))
        elif kind == "BE013":
            proposed = threshold_price(pos.side, pos.entry, D("0.13"))
        elif kind == "TRAIL035":
            proposed = threshold_price(pos.side, pos.entry, pos.best_favorable - D("0.35"))
        else:
            raise ValueError(kind)
        # Stops tighten only; they never retreat.
        tightens = proposed > pos.stop if pos.side == "LONG" else proposed < pos.stop
        if tightens:
            pos.stop = proposed
            pos.protected = True
            pos.protection_seq = seq
    return pos, None


def read_events(path: Path, symbol: str):
    by_seq = defaultdict(list)
    count = Counter()
    prev = 0
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            e = json.loads(line)
            seq = int(e["source_order"])
            if seq < prev or e["symbol"] != symbol or not e["candidate_only"]:
                raise ValueError("candidate archive order/provenance mismatch")
            prev = seq
            by_seq[seq].append(e)
            count["raw_candidates"] += 1
            for cohort in COHORTS:
                count[cohort + "_candidates"] += bool(e[cohort])
    return by_seq, count


def replay(symbol: str, raw_root: Path, frozen_root: Path, candidates_root: Path,
           output: Path, window: str = "common"):
    if symbol not in SYMBOLS or window not in ("common", "extended"):
        raise ValueError("invalid research scope")
    if output.exists():
        raise FileExistsError("immutable output exists")
    if not output.is_absolute() or OUTPUT_ROOT not in output.resolve().parents:
        raise ValueError("only versioned absolute data-disk research output")
    with (candidates_root / "manifest.json").open("r", encoding="utf-8") as stream:
        entry = json.load(stream)
    if entry["symbol"] != symbol or entry["research"] != "P90L90_ENTRY_V1":
        raise ValueError("wrong Entry archive")
    events_path = candidates_root / f"candidates_{symbol}.jsonl"
    if entry["events_sha256"] != sha256(events_path):
        raise ValueError("Entry archive fingerprint mismatch")
    states_path = frozen_root / f"states_{symbol}.csv.gz"
    if entry["frozen_l53_sha256"] != sha256(states_path):
        raise ValueError("frozen state changed since Entry")
    states, _, _, _ = load_frozen(states_path)
    events, counts = read_events(events_path, symbol)
    held = {key: None for key in SCENARIOS}
    metrics = {key: Counter() for key in SCENARIOS}
    money = {key: defaultdict(lambda: D(0)) for key in SCENARIOS}
    seq = 0
    last_price = None
    last_timestamp = None
    start_at = COMMON_TS if window == "common" else int(1779062400)
    for item in entry["inputs"]:
        path = raw_root / symbol / "public_trades" / item["file"]
        if sha256(path) != item["sha256"]:
            raise ValueError("raw source changed since Entry: " + path.name)
        with gzip.open(path, "rt", newline="") as stream:
            for row in csv.DictReader(stream):
                seq += 1
                t = float(row["timestamp"])
                px = D(row["price"])
                if last_timestamp is not None and t < last_timestamp:
                    raise ValueError("raw order moved")
                last_timestamp, last_price = t, px
                bucket = int(t // 300) * 300
                if not start_at <= t < END_TS:
                    continue
                candidates = events.get(seq, [])
                if candidates:
                    for e in candidates:
                        if float(e["ts"]) != t or D(e["price"]) != px or e["trade_id"] != row["trdMatchID"]:
                            raise ValueError("candidate source_row mismatch")
                for key in SCENARIOS:
                    cohort, kind = key
                    pos = held[key]
                    closed_this_record = False
                    if pos is not None:
                        active_before = pos
                        state = states.get(bucket)
                        tp = None if state is None else (state[1] if pos.side == "LONG" else state[0])
                        pos, out = on_trade(pos, px, seq, bucket, tp, kind)
                        held[key] = pos
                        if out is not None:
                            reason, exit_px, maker_exit = out
                            gross, entry_fee, exit_fee, net = gross_and_net(
                                active_before, exit_px, maker_exit
                            )
                            m = metrics[key]
                            m["closed"] += 1
                            m["close_" + reason] += 1
                            m["win"] += net > 0
                            m["loss"] += net < 0
                            money[key]["gross_usdt"] += gross
                            money[key]["entry_fees_usdt"] += entry_fee
                            money[key]["exit_fees_usdt"] += exit_fee
                            money[key]["net_usdt"] += net
                            money[key]["positive_net_usdt"] += max(net, D(0))
                            money[key]["negative_net_usdt"] += min(net, D(0))
                            closed_this_record = True
                    for event in candidates:
                        if not event[cohort] or (cohort == "PREV7D" and bucket < COMMON_TS):
                            continue
                        metrics[key]["qualified_candidate"] += 1
                        if held[key] is not None or closed_this_record:
                            metrics[key]["busy_or_same_tick"] += 1
                            continue
                        held[key] = initial_position(event, seq, bucket, cohort, kind)
                        state = states.get(bucket)
                        if state is None:
                            raise ValueError("missing frozen state for accepted Entry")
                        tp = state[1] if event["side"] == "LONG" else state[0]
                        if take_profit_eligible(event["side"], px, tp):
                            held[key].tp = tp
                            held[key].tp_live_after_seq = seq
                        metrics[key]["accepted"] += 1
    if seq != sum(int(z["rows"]) for z in entry["inputs"]):
        raise ValueError("source file row count mismatch")
    if any(x > seq for x in events):
        raise ValueError("unprocessed candidate source rows")
    report = {
        "research": "P90L90_EXIT_V1", "symbol": symbol,
        "status": "HISTORICAL_TICK_PROXY_NO_EXCHANGE_FILL",
        "entry_events_sha256": entry["events_sha256"],
        "frozen_l53_sha256": entry["frozen_l53_sha256"],
        "period": "COMMON_82D" if window == "common" else "FIXED90_EXTENDED",
        "nominal_usdt": "10", "taker_fee": str(TAKER), "maker_resting_tp_fee": str(MAKER),
        "unmodeled": ["funding", "slippage", "latency", "bid_ask_spread"],
        "entry_candidate_counts": dict(counts),
        "scenarios": {},
    }
    for key in SCENARIOS:
        cohort, kind = key
        m = metrics[key]
        cur = held[key]
        m["censored_open"] = 1 if cur else 0
        if m["qualified_candidate"] != m["accepted"] + m["busy_or_same_tick"]:
            raise ValueError("candidate accounting mismatch")
        mtm_gross = None if cur is None else gross_and_net(cur, last_price, False)[0]
        report["scenarios"][f"{cohort}_{kind}"] = {
            "counts": dict(m),
            "money": {k: str(v) for k, v in money[key].items()},
            "censored_mtm_gross_usdt_excluded": None if mtm_gross is None else str(mtm_gross),
        }
    output.mkdir(parents=False, exist_ok=False)
    p = output / "result.json"
    p.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", choices=SYMBOLS, required=True)
    p.add_argument("--raw-root", type=Path, default=RAW_DEFAULT)
    p.add_argument("--frozen-root", type=Path, default=FROZEN_DEFAULT)
    p.add_argument("--entry-output", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--window", choices=["common", "extended"], default="common")
    args = p.parse_args()
    report = replay(args.symbol, args.raw_root, args.frozen_root,
                    args.entry_output, args.output, args.window)
    print(json.dumps({"status": report["status"], "symbol": args.symbol,
                      "scenarios": report["scenarios"]}))


if __name__ == "__main__":
    main()
