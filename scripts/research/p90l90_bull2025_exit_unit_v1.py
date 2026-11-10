#!/usr/bin/env python3
"""P90L90 2025: independent-side native-tick Exit UNIT ledger, research-only.

Exact unchanged original P90L90 Exit transition/stop/TP and unit fee functions
are imported from p90l90_exit_90d_v2. No 2026 market inputs, reference ledger
or 90d financial gate. This is a 10-USDT *unit* Exit evidence stage, NOT
the 1000-USDT five-symbol portfolio/SECOND48/W7H24 wallet result.

Writer: cripta UID107. Data-disk child only. No DB, service, Exchange, LIVE.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import shutil
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

SOURCE = Path("/srv/cripta/source_checkout")
sys.path.insert(0, str(SOURCE))
from scripts.research import p90l90_bull2025_entry_v1 as entry  # noqa: E402
from scripts.research import p90l90_exit_90d_v2 as original  # noqa: E402

D = Decimal
ROOT = entry.ROOT
RESULTS = ROOT / "results"
ENTRY_ROOT = RESULTS / "entry_full_2025_v1"
FROZEN = RESULTS / "l53_geometry_full_2025_v1/parts"
RAW = ROOT / "inputs"
PARENT = RESULTS / "exit_unit_2025_v1"
EVAL_START = datetime(2025, 4, 1, tzinfo=timezone.utc)
EVAL_END = datetime(2025, 10, 1, tzinfo=timezone.utc)
START_TS = int(EVAL_START.timestamp())
END_TS = int(EVAL_END.timestamp())
SYMBOLS = tuple(entry.SYMBOLS)
SIDES = ("LONG", "SHORT")
ORIGINAL_REL = "scripts/research/p90l90_exit_90d_v2.py"
SELF_REL = "scripts/research/p90l90_bull2025_exit_unit_v1.py"
ENTRY_REL = "scripts/research/p90l90_bull2025_entry_v1.py"
QUANTUM = original.MONEY_QUANTUM
CASH_KEYS = (
    "gross_usdt", "entry_fees_usdt", "exit_fees_usdt",
    "net_usdt", "positive_net_usdt", "negative_net_usdt",
)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()


def preflight(symbol: str, source_head: str) -> tuple[dict, Path, Path, Path]:
    if os.geteuid() != 107 or source_head != entry.git_head():
        raise PermissionError("actual cripta UID107 and exact GitHub main SOURCE_HEAD required")
    if len(source_head) != 40 or any(c not in "0123456789abcdef" for c in source_head):
        raise ValueError("invalid source SHA")
    if symbol not in SYMBOLS:
        raise ValueError("symbol outside owner-frozen cohort")
    if ROOT.stat().st_uid != 107 or RESULTS.stat().st_uid != 107:
        raise PermissionError("research disk root owner mismatch")
    if not os.access(RESULTS, os.W_OK | os.X_OK):
        raise PermissionError("actual actor cannot create results")
    if RESULTS.is_symlink() or PARENT.is_symlink():
        raise PermissionError("research result parent symlink disallowed")
    if PARENT.exists() and (
        PARENT.stat().st_uid != 107 or not os.access(PARENT, os.W_OK | os.X_OK)
    ):
        raise PermissionError("actual actor cannot create Exit leaf")
    out = PARENT / symbol
    if out.exists() or out.is_symlink():
        raise FileExistsError("immutable 2025 Exit unit output collision")
    if shutil.disk_usage(ROOT).free < 16 * 2**30:
        raise RuntimeError("below 16GiB data disk reserve")
    acquisition = json.loads((ROOT / "run_manifest.json").read_text())
    if acquisition.get("status") != "INPUT_COMPLETE_QC_PASS_NO_STRATEGY_REPLAY":
        raise ValueError("2025 input data QC not complete")
    if sha(SOURCE / ORIGINAL_REL) != acquisition["source_snapshot_sha256"][ORIGINAL_REL]:
        raise RuntimeError("original Exit semantics no longer identical to frozen source")
    if sha(SOURCE / ENTRY_REL) != sha(
        ENTRY_ROOT / symbol / "source_snapshot" / Path(ENTRY_REL).name
    ):
        raise RuntimeError("2025 Entry source changed since candidate publication")
    geom_qc = json.loads((RESULTS / "l53_geometry_full_2025_v1/qc.json").read_text())
    if geom_qc.get("status") != "QC_PASS_FULL_2025_GEOMETRY_NOT_TRADING":
        raise ValueError("2025 L5 frozen geometry is not QC PASS")
    ep = ENTRY_ROOT / symbol
    qc = json.loads((ep / "qc.json").read_text())
    manifest = json.loads((ep / "manifest.json").read_text())
    if not (
        qc.get("status") == "QC_PASS_2025_ENTRY_CANDIDATE_ONLY_NO_EXITS"
        and qc["source_head"] == manifest.get("source_head", qc["source_head"])
        and manifest.get("research") == "P90L90_ENTRY_BULL2025_V1"
        and manifest["symbol"] == symbol
        and manifest.get("complete_window_input") is True
        and manifest["frozen_l53"]["states"] == 183 * 288
        and manifest["window"] == [EVAL_START.isoformat(), EVAL_END.isoformat()]
        and len(manifest["inputs"]) == 193
        and manifest["source_first_day"] == "2025-03-22"
        and manifest["source_last_day"] == "2025-09-30"
        and manifest["PREV7D_WIDTH_LOOKAHEAD"] is False
    ):
        raise ValueError("2025 exact independent Entry identity/coverage mismatch")
    candidates = ep / f"candidates_{symbol}.jsonl"
    states = FROZEN / f"states_{symbol}.csv.gz"
    if qc["events_sha256"] != manifest["events_sha256"] or sha(candidates) != qc["events_sha256"]:
        raise ValueError("Entry events provenance mismatch")
    if sha(states) != manifest["frozen_l53_sha256"] or sha(states) != qc["frozen_l53_sha256"]:
        raise ValueError("causal frozen L5 states fingerprint mismatch")
    return manifest, candidates, states, out


def calculate(symbol: str, source_head: str, smoke_hours: int = 0) -> dict:
    manifest, candidates_path, states_path, out = preflight(symbol, source_head)
    states, _, med7, meta = entry.load_frozen_period(states_path, EVAL_START, EVAL_END)
    if len(states) != 52704 or len(med7) != 183 or meta["prior_complete_days"] != 7:
        raise ValueError("2025 closed-only PREV7D geometry not complete")
    candidates, ccount = original.read_events(candidates_path, symbol)
    counts = {side: Counter() for side in SIDES}
    money = {side: defaultdict(lambda: D(0)) for side in SIDES}
    held = dict.fromkeys(SIDES)
    ledgers = {side: [] for side in SIDES}
    last_t, last_px = None, None
    seen_events, raw_rows, last_seq = 0, 0, 0
    started = time.monotonic()
    last_heartbeat = started
    cutoff = END_TS if not smoke_hours else min(END_TS, START_TS + smoke_hours * 3600)
    finished_early = False

    for item in manifest["inputs"]:
        file_path = RAW / symbol / "public_trades" / item["file"]
        if sha(file_path) != item["sha256"]:
            raise ValueError("immutable Bybit daily source SHA mismatch " + item["file"])
        n_file = 0
        with gzip.open(file_path, "rt", newline="") as f:
            for row in csv.DictReader(f):
                last_seq += 1
                n_file += 1
                t = float(row["timestamp"])
                px = D(row["price"])
                if last_t is not None and t < last_t:
                    raise ValueError("unsorted original native ticks")
                last_t, last_px = t, px
                if t < START_TS:
                    continue
                if t >= cutoff:
                    finished_early = bool(smoke_hours)
                    break
                bucket = int(t // 300) * 300
                state = states.get(bucket)
                if state is None:
                    raise ValueError("missing causal frozen L5 state at active tick")
                events = candidates.get(last_seq, ())
                for ev in events:
                    if (
                        ev["trade_id"] != row["trdMatchID"]
                        or float(ev["ts"]) != t
                        or D(ev["price"]) != px
                    ):
                        raise ValueError("Entry event not at original Bybit source tick")
                seen_events += len(events)
                for side in SIDES:
                    pos = held[side]
                    closed_this_tick = False
                    if pos is not None:
                        tp = state[1] if side == "LONG" else state[0]
                        new_pos, exit_event = original.on_trade(
                            pos, px, last_seq, bucket, tp, "TRAIL035"
                        )
                        held[side] = new_pos
                        if exit_event is not None:
                            reason, close_px, maker = exit_event
                            gross, entry_fee, exit_fee, net = original.gross_and_net(
                                pos, close_px, maker
                            )
                            record = original.trade_record(
                                symbol, "PREV7D", "TRAIL035", pos, last_seq,
                                t, close_px, reason, maker,
                                gross, entry_fee, exit_fee, net
                            )
                            record["research_direction"] = side
                            ledgers[side].append(record)
                            counts[side]["closed"] += 1
                            counts[side]["close_" + reason] += 1
                            counts[side]["win" if net > 0 else "loss" if net < 0 else "flat"] += 1
                            for key, val in (
                                ("gross_usdt", gross),
                                ("entry_fees_usdt", entry_fee),
                                ("exit_fees_usdt", exit_fee),
                                ("net_usdt", net),
                                ("positive_net_usdt", max(net, D(0))),
                                ("negative_net_usdt", min(net, D(0))),
                            ):
                                money[side][key] += val
                            closed_this_tick = True
                    for ev in events:
                        if ev["side"] != side or not ev["PREV7D"]:
                            continue
                        counts[side]["qualified_candidate"] += 1
                        if held[side] is not None or closed_this_tick:
                            counts[side]["busy_or_same_tick"] += 1
                            continue
                        pos = original.initial_position(ev, last_seq, bucket, "PREV7D", "TRAIL035")
                        tp = state[1] if side == "LONG" else state[0]
                        if original.take_profit_eligible(side, px, tp):
                            pos.tp = tp
                            pos.tp_live_after_seq = last_seq
                        held[side] = pos
                        counts[side]["accepted"] += 1
                if time.monotonic() - last_heartbeat >= 20:
                    print(json.dumps({"stage": "EXIT_2025_HEARTBEAT", "symbol": symbol,
                                      "elapsed_s": round(time.monotonic() - started),
                                      "source_days": item["file"]}), flush=True)
                    last_heartbeat = time.monotonic()
        if finished_early:
            break
        raw_rows += n_file
        if n_file != int(item["rows"]):
            raise ValueError("original Bybit file row count mismatch")
    if smoke_hours:
        return {"status": "SMOKE_2025_EXIT_ONLY_NO_OUTPUT", "symbol": symbol,
                "hours": smoke_hours, "consumed_events": seen_events,
                "counts": {s: dict(counts[s]) for s in SIDES}}
    if last_seq != sum(int(x["rows"]) for x in manifest["inputs"]):
        raise ValueError("193-day original raw row count mismatch")
    if last_seq != int(manifest["counts"]["raw_rows"]) or raw_rows != last_seq:
        raise ValueError("2025 native tick count mismatch")
    if seen_events != int(manifest["events"]) or seen_events != ccount["raw_candidates"]:
        raise ValueError("2025 candidate source event count mismatch")
    if any(k > last_seq for k in candidates):
        raise ValueError("Entry event beyond source end")
    for side in SIDES:
        expected_candidates = sum(
            1 for events in candidates.values()
            for ev in events if ev["side"] == side and ev["PREV7D"]
        )
        c = counts[side]
        c["censored_open"] = int(held[side] is not None)
        if (
            c["qualified_candidate"] != expected_candidates
            or c["qualified_candidate"] != c["accepted"] + c["busy_or_same_tick"]
            or c["accepted"] != c["closed"] + c["censored_open"]
            or len(ledgers[side]) != c["closed"]
        ):
            raise ValueError("causal Entry/Exit candidate-slot reconciliation failed")
        cash = money[side]
        if (
            cash["gross_usdt"] - cash["entry_fees_usdt"] - cash["exit_fees_usdt"] != cash["net_usdt"]
            or cash["positive_net_usdt"] + cash["negative_net_usdt"] != cash["net_usdt"]
            or sum((D(r["net_usdt"]) for r in ledgers[side]), D(0)) != cash["net_usdt"]
        ):
            raise ValueError("2025 10-USDT unit fee/ledger reconciliation failed")
    if entry.git_head() != source_head:
        raise RuntimeError("source HEAD moved during 2025 Exit computation")
    # The output is created exactly once after all original-source and ledger checks.
    if out.exists() or out.is_symlink():
        raise FileExistsError("2025 unit Exit immutable output collided during calculation")
    PARENT.mkdir(mode=0o2770, parents=False, exist_ok=True)
    if PARENT.stat().st_uid != 107:
        raise PermissionError("wrong exit unit parent owner")
    out.mkdir(mode=0o2770, exist_ok=False)
    results = {}
    for side in SIDES:
        leaf = out / f"trade_ledger_{side.lower()}.jsonl"
        with leaf.open("x", encoding="utf-8") as f:
            for record in ledgers[side]:
                f.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        pos = held[side]
        results[side] = {
            "counts": dict(counts[side]),
            "money_per_10_USDT_unit": {k: str(money[side][k]) for k in CASH_KEYS},
            "ledger_sha256": sha(leaf),
            "ledger_rows": len(ledgers[side]),
            "censored_open": None if pos is None else {
                "symbol": symbol, "side": pos.side,
                "entry_ts": repr(pos.entry_ts), "entry_price": str(pos.entry),
                "entry_event_id": pos.entry_event_id,
                "entry_source_order": pos.entry_seq,
                "last_source_price": str(last_px),
            },
        }
    snapshot = out / "source_snapshot"
    snapshot.mkdir(mode=0o2770)
    source_hashes = {}
    for rel in (ORIGINAL_REL, SELF_REL, ENTRY_REL):
        original_file = SOURCE / rel
        captured = snapshot / Path(rel).name
        shutil.copyfile(original_file, captured)
        source_hashes[Path(rel).name] = sha(captured)
        if sha(original_file) != source_hashes[Path(rel).name]:
            raise RuntimeError("source snapshot SHA mismatch")
    result = {
        "research": "P90L90_BULL2025_EXIT_UNIT_V1",
        "status": "QC_PASS_UNIT_EXIT_ONLY_NOT_PORTFOLIO_2025",
        "symbol": symbol, "evaluation_utc": [EVAL_START.isoformat(), EVAL_END.isoformat()],
        "evaluation_days": 183, "source_days": 193,
        "native_ticks_all_days": last_seq, "native_candidate_events": seen_events,
        "source_head": source_head, "source_sha256": source_hashes,
        "exit_original_unchanged": True,
        "trade_model": "same original TRAIL035/opposite RAW L5-3 TP/gross -10% stop",
        "directions_independent": True, "entry_cohort": "causal PREV7D",
        "unit_notional_usdt": "10",
        "taker_entry_fee": str(original.TAKER),
        "taker_exit_fee": str(original.TAKER),
        "maker_resting_exit_fee": str(original.MAKER),
        "unmodeled": ["funding", "latency", "bid_ask_spread", "slippage",
                      "orderbook_queue", "real_exchange_execution"],
        "warning": "Per-10-USDT UNIT Exit evidence only, not five-symbol wallets, NO_PAUSE/SECOND48/W7H24 or final after-fee 2025 portfolio PnL. Censored positions are NOT marked realized.",
        "entry_events_sha256": manifest["events_sha256"],
        "frozen_l53_sha256": manifest["frozen_l53_sha256"],
        "scenarios": results,
    }
    (out / "result.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    with (out / "qc.json").open("x") as f:
        json.dump({"status": result["status"], "source_head": source_head,
                   "result_sha256": sha(out / "result.json"),
                   "ledgers": {s: results[s]["ledger_sha256"] for s in SIDES},
                   "source_sha256": source_hashes,
                   "source_rows": last_seq,
                   "candidate_events": seen_events}, f, sort_keys=True, indent=2)
        f.write("\n")
    (out / "source_commit.txt").write_text(source_head + "\n")
    print(json.dumps({"stage": "2025_EXIT_UNIT_QC_PASS", "symbol": symbol,
                      "counts": {s: dict(counts[s]) for s in SIDES},
                      "result_path": str(out / "result.json")}), flush=True)
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", choices=SYMBOLS, required=True)
    ap.add_argument("--source-head", required=True)
    ap.add_argument("--smoke-hours", type=int, default=0)
    a = ap.parse_args()
    if a.smoke_hours < 0 or a.smoke_hours > 24:
        raise ValueError("smoke-hours must be 0..24")
    calculate(a.symbol, a.source_head, a.smoke_hours)


if __name__ == "__main__":
    main()
