#!/usr/bin/env python3
"""#835: Direct P90L90 Entry-only first -10% stop; MFE +0.5% before stop.

Research only. Native immutable candidate tapes and Bybit public trades.
Every frozen-qualified Entry is independently opened as a virtual position.
NO structural TP, TRAIL035, clock Exit, position occupancy, SECOND48, or cash gate.
Only the first -10% gross adverse price stop and period-end censor are allowed.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import heapq
import json
import os
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path

SYMS = ("LTC", "DOT", "ARB", "APT", "INJ")
RESEARCH = "P90L90_DIRECT_ALL_ENTRY_NOEXIT_FIRST_HS_MFE05_V1"
SPEC = {
    "183d": {
        "root": "/data/cripta/research/runs/p90l90_bull_2025_holdout_v1",
        "entry_sub": "results/entry_full_2025_v1",
        "eval_start": "2025-04-01", "eval_end": "2025-10-01",
        "job": "C2-R635-TP25-FULL2", "job_sub": "2025",
        "strict0": True, "expected_entry": 5446, "expected_hs": 73,
    },
    "90d": {
        "root": "/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024",
        "entry_sub": "results", "eval_start": "2026-05-18",
        "eval_end": "2026-08-16", "job": "C2-R635-TP90-FULL2",
        "job_sub": "90d", "strict0": False,
        "expected_entry": 3959, "expected_hs": 21,
    },
    "29d": {
        "root": "/data/cripta/research/runs/p90l90_prev7d_29d_20261025",
        "entry_sub": "results", "eval_start": "2026-09-21",
        "eval_end": "2026-10-20", "job": "C2-R635-TP29-FULL2",
        "job_sub": "29d", "strict0": False,
        "expected_entry": 886, "expected_hs": 12,
    },
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as stream:
        while data := stream.read(1024 * 1024):
            h.update(data)
    return h.hexdigest()


def freeze_entries(period: str, sym: str, cfg: dict):
    name = sym + "USDT"
    if period == "183d":
        target = Path(cfg["root"]) / cfg["entry_sub"] / name
    else:
        postfix = "_FULL90" if period == "90d" else "_SEP29"
        target = Path(cfg["root"]) / cfg["entry_sub"] / ("entry_" + sym + postfix)
    manifest = json.loads((target / "manifest.json").read_text())
    p = target / ("candidates_" + name + ".jsonl")
    if manifest["symbol"] != name or sha256(p) != manifest["events_sha256"]:
        raise ValueError("candidate manifest or sha mismatch " + name)
    if not manifest.get("candidate_only", True):
        raise ValueError("candidate archive includes actual fills")
    es = []
    prev_seq = 0
    raw_count = 0
    for line in p.read_text().splitlines():
        e = json.loads(line)
        seq = int(e["source_order"])
        if seq < prev_seq or e["symbol"] != name or not e["candidate_only"]:
            raise ValueError("out-of-order or invalid candidate")
        prev_seq = seq
        raw_count += 1
        if not e["PREV7D"] or (cfg["strict0"] and D(e["depth_pct"]) != 0):
            continue
        es.append((seq, e))
    expected = manifest.get("events")
    if expected is None:
        expected = manifest.get("counts", {}).get("raw_crossing")
    if expected is None or raw_count != int(expected):
        raise ValueError("candidate count mismatch")
    return manifest, es, p


def baseline_hardstops(cfg: dict, symbol: str) -> set[str]:
    root = (Path("/data/cripta/jobs/completed") / cfg["job"] /
            "bundle" / cfg["job_sub"])
    result = json.loads((root / "result.json").read_text())
    if result.get("status") is None:
        raise ValueError("missing baseline result status")
    ids = set()
    for side in ("long_only", "short_only"):
        f = root / (side + "_floor0p50.jsonl")
        for line in f.read_text().splitlines():
            r = json.loads(line)
            if r["exit_reason"] == "HARD_STOP" and r["symbol"] == symbol:
                if r["candidate_event_id"] in ids:
                    raise ValueError("duplicate hardstop original identity")
                ids.add(r["candidate_event_id"])
    return ids


def simulate_symbol(period: str, sym: str, cfg: dict, smoke_days: int, out: Path):
    symbol = sym + "USDT"
    manifest, events, event_file = freeze_entries(period, sym, cfg)
    expected_hs = baseline_hardstops(cfg, symbol)
    all_ev_ids = {e["event_id"] for _, e in events}
    if len(all_ev_ids) != len(events):
        raise ValueError("duplicate qualified event ids")
    if not expected_hs <= all_ev_ids:
        raise ValueError("original hardstop Entry not in direct Entry cohort")

    # Two ordered heaps per side: first stop and first +0.5% favorable.
    # Lazy deletes mean O(log N) work only on threshold crossings.
    long_stops, short_stops, long_gains, short_gains = [], [], [], []
    active: dict[int, dict] = {}
    totals, by_side = Counter(), defaultdict(Counter)
    outputs: list[dict] = []
    next_id, cursor, seq = 0, 0, 0
    processed_days = 0
    last_ts = None

    def close(p: dict, t: float, px: D, order: int):
        r = {
            "candidate_event_id": p["event_id"],
            "symbol": symbol, "side": p["side"],
            "entry_ts": p["ts"], "entry_source_order": p["order"],
            "entry_price": str(p["price"]),
            "stop_barrier_price": str(p["stop"]),
            "terminal": "HARD_STOP", "exit_ts": str(t),
            "exit_source_order": order, "exit_tick_price": str(px),
            "hit_plus0p5_before_stop": p["hit05"],
            "original_trail035_hardstop": p["original_hs"],
        }
        outputs.append(r)
        totals["HARD_STOP"] += 1
        by_side[p["side"]]["HARD_STOP"] += 1
        if p["hit05"]:
            totals["HS_AFTER_05"] += 1
            by_side[p["side"]]["HS_AFTER_05"] += 1
        else:
            totals["HS_NEVER_05"] += 1
            by_side[p["side"]]["HS_NEVER_05"] += 1
        if p["original_hs"]:
            totals["ORIGINAL_HS_CLOSED"] += 1
            by_side[p["side"]]["ORIGINAL_HS_CLOSED"] += 1
            if p["hit05"]:
                raise ValueError("Original trailing HS previously touched +0.5%")

    for item in manifest["inputs"]:
        filename = item["file"]
        date = filename[len(symbol):len(symbol) + 10]
        if not cfg["eval_start"] <= date < cfg["eval_end"]:
            seq += int(item["rows"])
            continue
        if smoke_days and processed_days >= smoke_days:
            break
        p = Path(cfg["root"]) / "inputs" / symbol / "public_trades" / filename
        if not p.is_file() or not p.resolve().is_relative_to("/data/cripta"):
            raise ValueError("unapproved raw source path " + str(p))
        if sha256(p) != item["sha256"]:
            raise ValueError("raw data sha mismatch " + str(p))
        day_count = 0
        with gzip.open(p, "rt", newline="") as stream:
            reader = csv.reader(stream)
            head = next(reader)
            ts_ix, px_ix, id_ix = (head.index(x) for x in
                                    ("timestamp", "price", "trdMatchID"))
            for row in reader:
                seq += 1
                day_count += 1
                t = float(row[ts_ix])
                px = D(row[px_ix])
                if last_ts is not None and t < last_ts:
                    raise ValueError("non-monotonic native trade timestamps")
                last_ts = t
                # First old HARD_STOPs, only then favorable crosses.
                while long_stops and -long_stops[0][0] >= px:
                    _, ident = heapq.heappop(long_stops)
                    pos = active.pop(ident, None)
                    if pos is not None:
                        close(pos, t, px, seq)
                while short_stops and short_stops[0][0] <= px:
                    _, ident = heapq.heappop(short_stops)
                    pos = active.pop(ident, None)
                    if pos is not None:
                        close(pos, t, px, seq)
                while long_gains and long_gains[0][0] <= px:
                    _, ident = heapq.heappop(long_gains)
                    pos = active.get(ident)
                    if pos is not None and not pos["hit05"]:
                        pos["hit05"] = True
                        totals["TOUCHED_05"] += 1
                        by_side[pos["side"]]["TOUCHED_05"] += 1
                while short_gains and -short_gains[0][0] >= px:
                    _, ident = heapq.heappop(short_gains)
                    pos = active.get(ident)
                    if pos is not None and not pos["hit05"]:
                        pos["hit05"] = True
                        totals["TOUCHED_05"] += 1
                        by_side[pos["side"]]["TOUCHED_05"] += 1
                # All frozen-qualified events independently enter on their exact
                # source tick AFTER evaluating already-open virtual positions.
                while cursor < len(events) and events[cursor][0] == seq:
                    _, e = events[cursor]
                    if (float(e["ts"]) != t or D(e["price"]) != px or
                            e["trade_id"] != row[id_ix]):
                        raise ValueError("candidate/raw tick identity mismatch")
                    side = e["side"]
                    entry = D(e["price"])
                    if side not in ("LONG", "SHORT"):
                        raise ValueError("invalid side")
                    next_id += 1
                    original = e["event_id"] in expected_hs
                    pos = {"event_id": e["event_id"], "side": side,
                           "price": entry, "ts": e["ts"], "order": seq,
                           "stop": entry * (D("0.90") if side == "LONG"
                                             else D("1.10")),
                           "hit05": False, "original_hs": original}
                    active[next_id] = pos
                    if side == "LONG":
                        heapq.heappush(long_stops, (-pos["stop"], next_id))
                        heapq.heappush(long_gains,
                                       (entry * D("1.005"), next_id))
                    else:
                        heapq.heappush(short_stops, (pos["stop"], next_id))
                        heapq.heappush(short_gains,
                                       (-entry * D("0.995"), next_id))
                    totals["OPENED"] += 1
                    by_side[side]["OPENED"] += 1
                    if original:
                        totals["ORIGINAL_HS_OPENED"] += 1
                        by_side[side]["ORIGINAL_HS_OPENED"] += 1
                    cursor += 1
                if cursor < len(events) and events[cursor][0] < seq:
                    raise ValueError("unprocessed frozen candidate")
        if day_count != int(item["rows"]):
            raise ValueError("native day row count mismatch")
        processed_days += 1
        totals["TICKS"] += day_count
        print(json.dumps({"progress": symbol, "day": date,
                          "days": processed_days, "ticks": totals["TICKS"],
                          "opened": totals["OPENED"], "hardstops": totals["HARD_STOP"],
                          "active": len(active)}), flush=True)

    for pos in active.values():
        outputs.append({
            "candidate_event_id": pos["event_id"], "symbol": symbol,
            "side": pos["side"], "entry_ts": pos["ts"],
            "entry_source_order": pos["order"],
            "entry_price": str(pos["price"]),
            "stop_barrier_price": str(pos["stop"]), "terminal": "CENSORED",
            "hit_plus0p5_before_stop": pos["hit05"],
            "original_trail035_hardstop": pos["original_hs"],
        })
        totals["CENSORED"] += 1
        by_side[pos["side"]]["CENSORED"] += 1

    if totals["OPENED"] != len(outputs):
        raise ValueError("not all virtual trades reached a terminal/censor state")
    if totals["HARD_STOP"] != totals["HS_AFTER_05"] + totals["HS_NEVER_05"]:
        raise ValueError("hardstop classification mismatch")
    if not smoke_days:
        if cursor != len(events):
            raise ValueError("did not consume all frozen Entries")
        if totals["OPENED"] != len(events):
            raise ValueError("full raw Entry source mismatch")
        if totals["ORIGINAL_HS_CLOSED"] != len(expected_hs):
            raise ValueError("not all original HARD_STOPs were first-stop observed")
    else:
        if totals["OPENED"] == 0 or totals["TICKS"] == 0:
            raise ValueError("smoke must observe actual Entries and raw ticks")

    trades_path = out / (symbol + "_direct_virtual_trades.jsonl")
    if trades_path.exists():
        raise FileExistsError("immutable virtual ledger exists")
    with trades_path.open("x") as f:
        for row in outputs:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    return {
        "symbol": symbol, "status": "PASS", "candidate_source_sha256": sha256(event_file),
        "virtual_ledger_sha256": sha256(trades_path),
        "original_hardstop_count": len(expected_hs),
        "counters": dict(totals),
        "by_side": {k: dict(v) for k, v in by_side.items()},
        "days_processed": processed_days,
        "price_threshold": "gross favorable >= +0.50%, strictly BEFORE first -10%",
        "raw_stop_model": "first native tick crossing adverse -10%, no TP/TRAIL",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", required=True, choices=sorted(SPEC))
    ap.add_argument("--smoke-days", type=int, default=0)
    a = ap.parse_args()
    if a.smoke_days < 0 or a.smoke_days > 2:
        raise ValueError("smoke days must be 0,1,2")
    if os.geteuid() != 107:
        raise PermissionError("approved research actor expected UID=107")
    root = Path.cwd().resolve()
    if not (str(root).startswith("/data/cripta/jobs/running/") and
            root.name == "bundle"):
        raise PermissionError("must run inside approved job spool bundle")
    sub = root / a.period
    if sub.exists():
        raise FileExistsError("immutable output collision")
    sub.mkdir(mode=0o750)
    result = {
        "research": RESEARCH, "status": "IN_PROGRESS",
        "period": a.period, "dataset": SPEC[a.period],
        "universe": [s+"USDT" for s in SYMS], "smoke_days": a.smoke_days,
        "comparison": "all causal Entry events independently, NO EXIT but -10%",
        "assumption": "virtual entries NOT executions; no fees applied to gross thresholds",
        "symbols": {},
    }
    for sym in SYMS:
        result["symbols"][sym] = simulate_symbol(
            a.period, sym, SPEC[a.period], a.smoke_days, sub)
        (sub / "progress.json").write_text(json.dumps(result, sort_keys=True))
    aggregate = Counter()
    side = defaultdict(Counter)
    for r in result["symbols"].values():
        aggregate.update(r["counters"])
        for key, value in r["by_side"].items():
            side[key].update(value)
    result["aggregate"] = dict(aggregate)
    result["by_side"] = {k: dict(v) for k, v in side.items()}
    if not a.smoke_days:
        if aggregate["OPENED"] != SPEC[a.period]["expected_entry"]:
            raise ValueError("full entry cohort count changed")
        if aggregate["ORIGINAL_HS_CLOSED"] != SPEC[a.period]["expected_hs"]:
            raise ValueError("original baseline hardstop count changed")
        result["status"] = "INTERNAL_QC_PASS"
    else:
        result["status"] = "SMOKE_PASS"
    (sub / "result.json").write_text(json.dumps(result, sort_keys=True, indent=2))
    print(json.dumps({"result_status": result["status"],
                      "period": a.period, "aggregate": dict(aggregate),
                      "by_side": result["by_side"]}), flush=True)


if __name__ == "__main__":
    main()
