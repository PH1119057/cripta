#!/usr/bin/env python3
"""Isolated strict-RAW-depth==0 P90L90 2025 comparison; no production mutation.

OWNER study: #598. Original CAPFREE100 Entry, Exit, portfolio implementation,
and 2025 candidate/native data stay byte-identical to independent-QC baseline.
Only the book's Entry admission rejects non-zero RAW H9 union depth.

Server contract (DEV 5.1 / 19):
  actor: cripta UID107:GID109 (not root / SentinelX actor)
  read: frozen clean research checkout beneath /data/cripta/research/worktrees,
        /data/cripta/research/runs/p90l90_bull_2025_holdout_v1/{inputs,results}
  create/write/rename: exclusively
        /data/cripta/research/runs/p90l90_bull_2025_holdout_v1/results/
          portfolio_depthzero_2025_v1/{checkpoint,full,*.json,*.log,*.lock}
  delete: none; atomic status temp replace only
  systemd/DB/Exchange/trading services: none
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal as D
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

SELF_REL = "scripts/research/p90l90_bull2025_depthzero_v1.py"
SOURCE = Path(__file__).resolve().parents[2]
RESULTS = Path("/data/cripta/research/runs/p90l90_bull_2025_holdout_v1/results")
BASE = RESULTS / "portfolio_capfree100_2025_v1"
OUT = RESULTS / "portfolio_depthzero_2025_v1"
SIDES = ("LONG_ONLY", "SHORT_ONLY")
CASES = ("NO_PAUSE", "SECOND48")
MIN_FREE = 16 * 2**30
EXPECTED_CANDIDATES = {"LONG_ONLY": 2583, "SHORT_ONLY": 2863}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def zero_entry(event: dict, book_side: str) -> bool:
    """A PREV7D entry at *exactly* RAW union-depth zero; no epsilon/rounding."""
    return (
        event["side"] == book_side
        and event["PREV7D"] is True
        and D(str(event["depth_pct"])) == D(0)
    )


def filtered_events(events, side: str) -> tuple:
    return tuple(ev for ev in events if zero_entry(ev, side))


def atom(path: Path, value: dict) -> None:
    temp = path.with_name(path.name + ".new")
    if temp.exists() or temp.is_symlink():
        raise FileExistsError("ambiguous status temp: " + str(temp))
    with temp.open("x", encoding="utf-8") as out:
        json.dump(value, out, sort_keys=True, indent=2)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    temp.replace(path)


def set_status(stage: str, **data) -> None:
    atom(OUT / "status.json", {"stage": stage, **data})


def baseline_proof() -> dict:
    if os.geteuid() != 107 or os.getegid() != 109:
        raise PermissionError("research requires effective cripta UID107:GID109")
    if OUT.is_symlink() or RESULTS.is_symlink():
        raise PermissionError("research output symlinks rejected")
    if RESULTS.stat().st_uid != 107 or not os.access(RESULTS, os.W_OK | os.X_OK):
        raise PermissionError("research results parent not writable by actual actor")
    if shutil.disk_usage(RESULTS).free < MIN_FREE:
        raise RuntimeError("data-disk below 16GiB research reserve")
    summary = {}
    source_pins = None
    for side in SIDES:
        short = side.lower()
        qc = json.loads((BASE / ("qc_" + short + ".json")).read_text())
        if qc.get("status") != "INDEPENDENT_QC_PASS_CAPFREE100_2025_V1":
            raise ValueError("frozen baseline independent QC missing for " + side)
        original = BASE / "full" / short / "result.json"
        if sha(original) != qc["result_sha256"]:
            raise ValueError("baseline financial result bytes changed")
        report = json.loads(original.read_text())
        if report["checkpoint_days"] != 183 or report["side"] != side:
            raise ValueError("baseline days/side mismatch")
        if report["unlimited_no_pause_per_coin_shadow_parity"] is not True:
            raise ValueError("baseline per-coin shadow parity missing")
        if source_pins is None:
            source_pins = report["source_pin_files_sha256"]
        elif report["source_pin_files_sha256"] != source_pins:
            raise ValueError("baseline directional source pins differ")
        for rel, digest in source_pins.items():
            if sha(SOURCE / rel) != digest:
                raise ValueError("unreviewed original replay Python change: " + rel)
        summary[side] = {
            case: {
                "net_usdt": qc["cases"][case]["net_after_modeled_fees_usdt"],
                "accepted": qc["cases"][case]["accepted"],
                "closed": qc["cases"][case]["closed"],
                "hardstops": qc["cases"][case]["hardstops"],
            }
            for case in CASES
        }
    return {"baseline": summary, "source_pin_sha256": source_pins}


def candidate_proof() -> dict:
    from datetime import datetime, timezone
    counts = defaultdict(int)
    for symbol in ("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT"):
        folder = RESULTS / "entry_full_2025_v1" / symbol
        manifest = json.loads((folder / "manifest.json").read_text())
        leaf = folder / ("candidates_" + symbol + ".jsonl")
        if sha(leaf) != manifest["events_sha256"]:
            raise ValueError("frozen entry candidate hash changed: " + symbol)
        for line in leaf.open(encoding="utf-8"):
            ev = json.loads(line)
            if zero_entry(ev, ev["side"]):
                counts[ev["side"] + "_ONLY"] += 1
    if dict(counts) != EXPECTED_CANDIDATES:
        raise ValueError("strict-zero source candidate count mismatch: " + repr(counts))
    return dict(counts)


def prepare(expected: str):
    # Import from the exact code already used to resume/review CAPFREE100, not
    # from rolling main. It checks actual actor, detached HEAD and clean tree.
    sys.path.insert(0, str(SOURCE))
    from scripts.research import p90l90_bull2025_capfree100_frozen_resume_v2 as freeze
    driver, engine = freeze.load_frozen_modules(SOURCE, expected)
    if engine.old.START_WALLET != D("1000000000000"):
        raise ValueError("virtual unlimited wallet drift")
    if engine.old.FIXED_MARGIN != D("20") or engine.old.LEVERAGE != D("5"):
        raise ValueError("position sizing drift")
    proof = baseline_proof()
    proof["depthzero_candidates"] = candidate_proof()

    # Only Entry admission in model books changes. Unit shadow positions,
    # original native tick ordering, temporal stops, TP and fees are untouched.
    previous_handle = engine.old.Book.handle

    def handle_depthzero(self, symbol, t, seq, px, events, state, priority):
        return previous_handle(
            self, symbol, t, seq, px, filtered_events(events, self.side),
            state, priority,
        )

    engine.old.Book.handle = handle_depthzero
    engine.PARTS = OUT / "checkpoint"
    engine.FINAL = OUT / "full"
    engine.STATUS = "QC_PASS_P90L90_DEPTHZERO_NATIVE_2025_V1"
    engine.PINNED = tuple(dict.fromkeys((*engine.PINNED, SELF_REL)))
    if sha(SOURCE / SELF_REL) == "":
        raise RuntimeError("missing depthzero source pin")
    return engine, proof


def read_day(engine, side: str) -> int:
    folder = OUT / "checkpoint" / side.lower()
    if not folder.exists():
        return 0
    state, last, _ = engine.load_parts(folder)
    if state is None:
        return 0
    if last is None:
        raise RuntimeError("orphan checkpoint")
    return state["next_day"]


def pilot(engine, proof: dict, source_head: str) -> dict:
    if OUT.exists() or OUT.is_symlink():
        raise FileExistsError("depthzero output already exists; no duplicate pilot")
    OUT.mkdir(mode=0o2770)
    (OUT / "study.json").write_text(json.dumps({
        "study": "P90L90_BULL2025_DEPTHZERO_CAPFREE100_V1",
        "issue": 598,
        "exact_depth_percent": "0",
        "width_filter": "PREV7D +/-0.50 percentage points UNCHANGED",
        "exit": "TRAIL035 / opposite RAW L5-3 TP / -10% hardstop UNCHANGED",
        "cases": list(CASES),
        "sides": list(SIDES),
        "source_head": source_head,
        "source_sha256": sha(SOURCE / SELF_REL),
        "baseline_proof": proof,
    }, sort_keys=True, indent=2) + "\n")
    set_status("PILOT_RUNNING", side="LONG_ONLY", days_done=0)
    try:
        result = engine.run("LONG_ONLY", source_head, max_days=1)
        if read_day(engine, "LONG_ONLY") != 1 or result["status"] != "PARTIAL_DURABLE_RESUME_READY_NOT_FINAL":
            raise RuntimeError("bounded pilot did not commit one verified native UTC day")
        set_status("PILOT_PASS", side="LONG_ONLY", days_done=1, days_total=183)
        return {"status": "PILOT_PASS", "checkpoint_days": 1, "source_head": source_head}
    except BaseException as exc:
        set_status("FAILED", phase="PILOT", error=str(exc))
        raise


def eligible_ids(side: str) -> set[str]:
    ids = set()
    for symbol in ("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT"):
        path = RESULTS / "entry_full_2025_v1" / symbol / ("candidates_" + symbol + ".jsonl")
        for line in path.open(encoding="utf-8"):
            e = json.loads(line)
            if zero_entry(e, side.split("_")[0]):
                ids.add(e["event_id"])
    return ids


def independent_qc(side: str, source_head: str) -> dict:
    # Independent by-price/commission/entry-identity audit, not engine counters.
    leaf = OUT / "full" / side.lower()
    report_file = leaf / "result.json"
    report = json.loads(report_file.read_text())
    baseline_qc = json.loads((BASE / ("qc_" + side.lower() + ".json")).read_text())
    if report["side"] != side or report["evaluation_days"] != 183:
        raise ValueError("wrong result period/side")
    if report["source_head_at_publication"] != source_head:
        raise ValueError("frozen source HEAD drift")
    if report["cash_modes"] != ["fixed"] or report["cases"] != list(CASES):
        raise ValueError("unreviewed scenario/cash mode")
    if D(report["fixed_margin_usdt"]) != D("20") or D(report["leverage"]) != D("5"):
        raise ValueError("unexpected modeled position sizing")
    if report["unlimited_no_pause_per_coin_shadow_parity"] is not True:
        raise ValueError("original untouched shadow parity missing")
    eligible = eligible_ids(side)
    audited = {}
    for case in CASES:
        key = case + "_fixed"
        ledger = leaf / ("ledger_" + key + ".jsonl")
        if sha(ledger) != report["ledger_sha256"][key]:
            raise ValueError("immutable ledger hash failure")
        rows = [json.loads(line) for line in ledger.open(encoding="utf-8")]
        book = report["results"][key]
        counts = book["counts"]
        if counts.get("capital_blocked_candidate", 0) != 0:
            raise ValueError("capital gate incorrectly changed entry")
        if counts["accepted"] != counts["closed"] + counts["censored_open"]:
            raise ValueError("accepted/closed/censored mismatch")
        if counts["closed"] != len(rows):
            raise ValueError("trade ledger length mismatch")
        if counts["qualified_candidate"] != EXPECTED_CANDIDATES[side]:
            raise ValueError("candidate count mismatch inside replay")
        money = D(0)
        hard = 0
        for trade in rows:
            if trade["side"] != side.split("_")[0] or trade["candidate_event_id"] not in eligible:
                raise ValueError("nonzero-depth trade entered forbidden book")
            ratio = D(trade["exit_price"]) / D(trade["entry_price"])
            signed = D(1) if side == "LONG_ONLY" else D(-1)
            gross = (D(100) * (ratio - 1) * signed).quantize(D("0.000000000001"))
            entryfee = (D(100) * D("0.00055")).quantize(D("0.000000000001"))
            exitfee = (D(100) * ratio * (D("0.0002") if trade["maker_exit"] else D("0.00055"))).quantize(D("0.000000000001"))
            net = gross - entryfee - exitfee
            if (D(trade["gross_usdt"]), D(trade["entry_fee_usdt"]),
                D(trade["exit_fee_usdt"]), D(trade["net_usdt"])) != (gross, entryfee, exitfee, net):
                raise ValueError("independent financial calculation mismatch")
            if trade["maker_exit"] != (trade["exit_reason"] == "RESTING_TP"):
                raise ValueError("maker used for non-resting exit")
            hard += (trade["exit_reason"] == "HARD_STOP")
            money += net
        if hard != counts["exit_HARD_STOP"]:
            raise ValueError("hardstop accounting mismatch")
        money -= D("0.055") * counts["censored_open"]
        if money != D(book["wallet_usdt"]) - D(report["start_wallet_each_case_usdt"]):
            raise ValueError("wallet/fees financial reconciliation failed")
        audited[case] = {
            "net_after_modeled_fees_usdt": str(money),
            "closed": counts["closed"],
            "accepted": counts["accepted"],
            "censored": counts["censored_open"],
            "hardstops": hard,
            "baseline_net_usdt": baseline_qc["cases"][case]["net_after_modeled_fees_usdt"],
            "baseline_hardstops": baseline_qc["cases"][case]["hardstops"],
        }
    result = {
        "status": "INDEPENDENT_QC_PASS_P90L90_DEPTHZERO_2025_V1",
        "source_head": source_head,
        "source_sha256": sha(SOURCE / SELF_REL),
        "side": side,
        "candidate_count": EXPECTED_CANDIDATES[side],
        "results_sha256": sha(report_file),
        "cases": audited,
        "baseline_reference_only": "The untouched full-depth 2025 original shadow is NOT the strict-zero portfolio",
        "censored_not_forced_closed": True,
        "real_exchange_fills": False,
    }
    dest = OUT / ("qc_" + side.lower() + ".json")
    if dest.exists() or dest.is_symlink():
        raise FileExistsError("depthzero QC already published")
    with dest.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return result


def worker(engine, source_head: str) -> None:
    launch = json.loads((OUT / "launch.json").read_text())
    if launch["source_head"] != source_head or not (OUT / "study.json").is_file():
        raise RuntimeError("unrecognized research launch identity")
    with (OUT / ".worker.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            set_status("RUNNING", side="LONG_ONLY", days_done=read_day(engine,"LONG_ONLY"),
                       days_total=183, source_head=source_head)
            for side in SIDES:
                while read_day(engine, side) < 183:
                    current = read_day(engine, side)
                    run_result = engine.run(side, source_head, max_days=min(5, 183 - current))
                    done = read_day(engine, side)
                    print(json.dumps({"event":"DEPTHZERO_DAY_BATCH","side":side,
                                      "days_done":done,"run_status":run_result["status"]}),
                          flush=True)
                    set_status("RUNNING", side=side, days_done=done,
                               days_total=183, source_head=source_head)
                qc = independent_qc(side, source_head)
                print(json.dumps({"event":"DEPTHZERO_INDEPENDENT_QC_PASS",
                                  "side":side,"cases":qc["cases"]}, sort_keys=True),flush=True)

            totals = {case: {"baseline": D(0), "depth0": D(0)} for case in CASES}
            allqc = {}
            for side in SIDES:
                qc = json.loads((OUT / ("qc_" + side.lower() + ".json")).read_text())
                allqc[side] = qc
                for case in CASES:
                    line = qc["cases"][case]
                    totals[case]["baseline"] += D(line["baseline_net_usdt"])
                    totals[case]["depth0"] += D(line["net_after_modeled_fees_usdt"])
            summary = {
                "status": "COMPLETED_INDEPENDENT_QC_PASS_DEPTHZERO_2025_V1",
                "source_head": source_head,
                "directional_qc": allqc,
                "combined": {case: {
                    "baseline_net_usdt": str(v["baseline"]),
                    "depth0_net_usdt": str(v["depth0"]),
                    "difference_net_usdt": str(v["depth0"]-v["baseline"]),
                } for case,v in totals.items()},
            }
            with (OUT / "summary.json").open("x",encoding="utf-8") as f:
                json.dump(summary,f,sort_keys=True,indent=2)
                f.write("\n")
            set_status("COMPLETED_INDEPENDENT_QC_PASS", days_done=183,
                       days_total=183, source_head=source_head)
        except BaseException as exc:
            traceback.print_exc()
            set_status("FAILED", error=str(exc), source_head=source_head,
                       long_days=read_day(engine,"LONG_ONLY"),
                       short_days=read_day(engine,"SHORT_ONLY"))
            raise


def launch(engine, expected: str) -> dict:
    if not OUT.is_dir() or OUT.is_symlink():
        raise RuntimeError("pilot output missing")
    st = json.loads((OUT / "status.json").read_text())
    if st.get("stage") != "PILOT_PASS" or read_day(engine,"LONG_ONLY") != 1:
        raise RuntimeError("bounded pilot incomplete or active worker exists")
    if read_day(engine, "SHORT_ONLY") != 0:
        raise RuntimeError("unexpected second side checkpoints")
    intent = OUT / "launch.json"
    if intent.exists() or intent.is_symlink():
        raise FileExistsError("depthzero already launched; no duplicate")
    with intent.open("x",encoding="utf-8") as f:
        json.dump({"status":"LAUNCH_INTENT","source_head":expected},f)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    cmd = [sys.executable, "-B", str(SOURCE / SELF_REL),
           "--worker", "--source-head", expected]
    with (OUT / "controller.log").open("x",encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, cwd=str(SOURCE), stdin=subprocess.DEVNULL,
                                stdout=log,stderr=subprocess.STDOUT,
                                close_fds=True,start_new_session=True)
    record = {"status":"LAUNCHED_NOT_YET_VERIFIED","source_head":expected,
              "pid":proc.pid,"log":str(OUT/"controller.log")}
    atom(intent, record)
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--preflight",action="store_true")
    modes.add_argument("--pilot",action="store_true")
    modes.add_argument("--launch",action="store_true")
    modes.add_argument("--worker",action="store_true")
    parser.add_argument("--source-head",required=True)
    args = parser.parse_args()
    engine, proof = prepare(args.source_head)
    if args.preflight:
        print(json.dumps({"status":"PREPARED_READ_ONLY","source_head":args.source_head,
             "candidate_count":proof["depthzero_candidates"],"disk_free_gib":
             round(shutil.disk_usage(RESULTS).free/2**30,3),
             "existing_output":OUT.exists()},sort_keys=True),flush=True)
    elif args.pilot:
        print(json.dumps(pilot(engine,proof,args.source_head),sort_keys=True),flush=True)
    elif args.launch:
        print(json.dumps(launch(engine,args.source_head),sort_keys=True),flush=True)
    else:
        worker(engine,args.source_head)


if __name__ == "__main__":
    main()
