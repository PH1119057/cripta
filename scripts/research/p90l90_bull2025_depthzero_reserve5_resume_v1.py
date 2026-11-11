#!/usr/bin/env python3
"""Resume Issue #598 from immutable day 103 with owner-approved 5 GiB reserve.

Only the two 16 GiB disk guards in the original verified 2025 portfolio
*functions* are replaced with 5 GiB in explicitly versioned replay functions.
Original source files, Entry/Exit, checkpoints and baseline remain immutable.
This is post-holdout research ONLY; not Strategy/DB/Exchange/LIVE.

Filesystem contract DEV 5.1:
 writer cripta UID107:GID109; no systemd/DB role
 READ: clean detached Git research worktree at exact source HEAD,
       /data/cripta/research/runs/p90l90_bull_2025_holdout_v1/{inputs,results}
 CREATE/WRITE/RENAME: existing output
       .../results/portfolio_depthzero_2025_v1/{checkpoint,full,
       status.json(.new),resume5_*.json,resume5_controller.log,*.lock}
 DELETE: none, except atomic replacement of status/launch metadata
 Validated input/source SHA manifests and checkpoint chain are not overwritten.
"""
from __future__ import annotations

import argparse
from decimal import Decimal as D
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

from scripts.research import p90l90_bull2025_depthzero_v1 as original

SELF_REL = "scripts/research/p90l90_bull2025_depthzero_reserve5_resume_v1.py"
SOURCE = Path(__file__).resolve().parents[2]
OUT = original.OUT
ORIGINAL_HEAD = "87d44fb5ab6ae8e82ab63e6d765c6742d5289cf2"
ORIGINAL_FAILURE = "research data disk fell below 16GiB reserve"
RESERVE_BYTES = 5 * 2**30
INITIAL_LONG_DAYS = 103
SIDES = original.SIDES
CASES = original.CASES


def free_bytes() -> int:
    return shutil.disk_usage(OUT).free


def reserve5() -> None:
    if free_bytes() < RESERVE_BYTES:
        raise RuntimeError("research data disk fell below OWNER-approved 5GiB reserve")


def verify_and_load5(old, side: str, source_head: str):
    """Exact original portfolio_v1.verify_and_load except 16->5 GiB guard."""
    if os.geteuid() != 107 or old.entry.git_head() != source_head:
        raise PermissionError("2025 full research requires cripta UID107/exact SOURCE_HEAD")
    if side not in old.SIDES or len(source_head) != 40:
        raise ValueError("invalid independent side or source commit")
    if old.RESULTS.stat().st_uid != 107 or not os.access(old.RESULTS, os.W_OK | os.X_OK):
        raise PermissionError("actual research writer lacks parent access")
    if old.OUT_PARENT.is_symlink() or old.RESULTS.is_symlink():
        raise PermissionError("output root symlink denied")
    if old.OUT_PARENT.exists() and (
        old.OUT_PARENT.stat().st_uid != 107
        or not os.access(old.OUT_PARENT, os.W_OK | os.X_OK)
    ):
        raise PermissionError("portfolio output parent not writable as cripta")
    out = old.OUT_PARENT / side.lower()
    if out.exists() or out.is_symlink():
        raise FileExistsError("2025 full portfolio immutable output collision")
    reserve5()
    acq = json.loads((old.ROOT / "run_manifest.json").read_text())
    if acq.get("status") != "INPUT_COMPLETE_QC_PASS_NO_STRATEGY_REPLAY":
        raise ValueError("2025 original Bybit source QC failed")
    if old.sha(old.SOURCE / old.ORIGINAL_REL) != acq["source_snapshot_sha256"][old.ORIGINAL_REL]:
        raise ValueError("original P90L90 Exit code differs from frozen 2025 snapshot")
    geom = json.loads((old.RESULTS / "l53_geometry_full_2025_v1/qc.json").read_text())
    if geom.get("status") != "QC_PASS_FULL_2025_GEOMETRY_NOT_TRADING":
        raise ValueError("2025 geometry failed")
    all_data = {}
    all_unit = {}
    for symbol in old.SYMBOLS:
        ep = old.ENTRY_PARENT / symbol
        up = old.UNIT_PARENT / symbol
        eq = json.loads((ep / "qc.json").read_text())
        em = json.loads((ep / "manifest.json").read_text())
        uq = json.loads((up / "qc.json").read_text())
        ur = json.loads((up / "result.json").read_text())
        if (
            eq["status"] != "QC_PASS_2025_ENTRY_CANDIDATE_ONLY_NO_EXITS"
            or em["research"] != "P90L90_ENTRY_BULL2025_V1"
            or em["symbol"] != symbol
            or em["window"] != [old.EVAL_START.isoformat(), old.EVAL_END.isoformat()]
            or len(em["inputs"]) != 193
            or em["frozen_l53"]["states"] != 183 * 288
            or em["PREV7D_WIDTH_LOOKAHEAD"] is not False
            or em["counts"]["raw_rows"] != old.COUNTS_2025[symbol][0]
            or em["events"] != old.COUNTS_2025[symbol][1]
            or uq["status"] != "QC_PASS_UNIT_EXIT_ONLY_NOT_PORTFOLIO_2025"
            or ur["symbol"] != symbol
            or ur["native_ticks_all_days"] != old.COUNTS_2025[symbol][0]
            or ur["native_candidate_events"] != old.COUNTS_2025[symbol][1]
            or ur["evaluation_days"] != 183
        ):
            raise ValueError("2025 Entry/unit source identity mismatch " + symbol)
        cp = ep / f"candidates_{symbol}.jsonl"
        fp = old.FROZEN_PARENT / f"states_{symbol}.csv.gz"
        if old.sha(cp) != em["events_sha256"] or old.sha(fp) != em["frozen_l53_sha256"]:
            raise ValueError("2025 Entry/frozen source SHA mismatch " + symbol)
        if old.sha(up / "result.json") != uq["result_sha256"]:
            raise ValueError("2025 unit result SHA mismatch " + symbol)
        for rel in (old.unit.SELF_REL, old.unit.ENTRY_REL, old.ORIGINAL_REL):
            if old.sha(old.SOURCE / rel) != ur["source_sha256"][Path(rel).name]:
                raise ValueError("2025 unit source snapshot no longer matches published code " + rel)
        ul = up / f"trade_ledger_{side.split('_')[0].lower()}.jsonl"
        if old.sha(ul) != uq["ledgers"][side.split("_")[0]]:
            raise ValueError("2025 unit ledger SHA mismatch " + symbol)
        with ul.open() as stream:
            unit_rows = [json.loads(line) for line in stream]
        if len(unit_rows) != ur["scenarios"][side.split("_")[0]]["ledger_rows"]:
            raise ValueError("unit 2025 shadow row mismatch")
        states, _, med7, state_meta = old.entry.load_frozen_period(
            fp, old.EVAL_START, old.EVAL_END
        )
        if len(states) != 183 * 288 or len(med7) != 183 or state_meta["prior_complete_days"] != 7:
            raise ValueError("2025 PREV7D closed-only state/day QC")
        events, counters = old.original.read_events(cp, symbol)
        if counters["raw_candidates"] != old.COUNTS_2025[symbol][1]:
            raise ValueError("2025 independent Entry ledger count mismatch")
        all_data[symbol] = {
            "manifest": em, "events": events, "states": states,
            "seen_events": 0, "rows": 0, "symbol": symbol,
        }
        all_unit[symbol] = {
            "rows": unit_rows, "sha": old.sha(ul),
            "unit_result_sha": uq["result_sha256"],
        }
    return all_data, all_unit, out


def run5(engine, side: str, source_head: str, max_days: int = 5) -> dict:
    """Original chunked_v2.run mechanics, explicit owner override 16->5 GiB."""
    old = engine.old
    if side not in old.SIDES or not 1 <= max_days <= 10:
        raise ValueError("invalid 2025 side or bounded day count")
    if os.geteuid() != 107 or old.entry.git_head() != source_head:
        raise PermissionError("exact active GitHub main/actor cripta UID107 required")
    if old.sha(engine.SOURCE / engine.SELF_REL) == "":
        raise RuntimeError("reader source pin missing")
    current_pins = {rel: old.sha(engine.SOURCE / rel) for rel in engine.PINNED}
    if len(set(engine.PINNED)) != len(engine.PINNED):
        raise ValueError("duplicate research source pin paths")
    data, unit_rows, _ = verify_and_load5(old, side, source_head)
    schedule = old.shadow_schedule(unit_rows, side)
    engine.writer_dir(engine.PARTS)
    folder = engine.PARTS / side.lower()
    engine.writer_dir(folder)
    lock_path = folder / ".writer.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        flock = importlib.import_module("fcntl")
        try:
            flock.flock(lock.fileno(), flock.LOCK_EX | flock.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another active 2025 portfolio writer owns this side") from exc
        state, last_manifest, last_sha = engine.load_parts(folder, data)
        source_pins = current_pins if last_manifest is None else last_manifest["source_pin_sha256"]
        first_head = source_head if last_manifest is None else last_manifest["source_head_first"]
        if current_pins != source_pins:
            raise RuntimeError("2025 resume rejected: effective research source bytes differ")
        engine.pin_current(source_pins, first_head)
        unit_pins = {s: unit_rows[s]["sha"] for s in old.SYMBOLS}
        if last_manifest is not None and last_manifest["unit_ledger_sha256"] != unit_pins:
            raise RuntimeError("2025 immutable per-symbol UNIT ledger changed")
        books = engine.new_books(side, schedule)
        if state is None:
            state = engine.blank_state(data)
        else:
            engine.restore_books(books, state["books"])
        start_day = state["next_day"]
        if not 0 <= start_day <= engine.TOTAL_DAYS:
            raise ValueError("checkpoint day counter outside 2025 evaluation")
        final_leaf = engine.FINAL / side.lower()
        if final_leaf.exists() or final_leaf.is_symlink():
            raise FileExistsError("2025 full result already exists")
        started = time.monotonic()
        target = min(engine.TOTAL_DAYS, start_day + max_days)
        for i in range(start_day, target):
            rows = engine.replay_day(i, state, data, books, side)
            end_head = engine.pin_current(source_pins, first_head)
            _, last_sha = engine.make_part(
                folder, i, state, rows, data, last_sha,
                first_head, source_pins, unit_pins, end_head,
            )
            reserve5()
            elapsed = time.monotonic() - started
            total_done = state["next_day"]
            print(json.dumps({
                "stage": "2025_PORTFOLIO_DAY_CHECKPOINT_QC_PASS",
                "side": side, "day": engine.day_date(i),
                "day_index": total_done, "days_total": engine.TOTAL_DAYS,
                "percent": round(total_done / engine.TOTAL_DAYS * 100, 4),
                "elapsed_sec": round(elapsed, 2),
                "committed_manifest_sha256": last_sha,
                "native_ticks_evaluation": state["ticks"],
                "resume_next_day": (
                    engine.day_date(total_done) if total_done < engine.TOTAL_DAYS else None
                ),
                "effective_disk_reserve_gib": 5,
            }), flush=True)
        if state["next_day"] == engine.TOTAL_DAYS:
            result = engine.publish_final(
                side, state, data, unit_rows, books,
                schedule, source_pins, first_head, last_sha,
            )
            return {
                "status": result["status"], "side": side,
                "days_completed": engine.TOTAL_DAYS, "ticks": state["ticks"],
                "checkpoint_manifest_sha256": last_sha,
            }
        return {
            "status": "PARTIAL_DURABLE_RESUME_READY_NOT_FINAL",
            "side": side, "days_completed": state["next_day"],
            "days_total": engine.TOTAL_DAYS, "ticks": state["ticks"],
            "checkpoint_manifest_sha256": last_sha,
            "next_day": engine.day_date(state["next_day"]),
            "independent_portfolio_qc": "NOT_AVAILABLE",
        }


def prepare(expected: str):
    original.MIN_FREE = RESERVE_BYTES  # only effective in this process; original file unchanged
    engine, proof = original.prepare(expected)
    if engine.STATUS != "QC_PASS_P90L90_DEPTHZERO_NATIVE_2025_V1":
        raise ValueError("unapproved existing checkpoint schema")
    if original.sha(SOURCE / SELF_REL) == "":
        raise ValueError("resume adapter source missing")
    return engine, proof


def check_resume(engine, expected: str) -> dict:
    """No filesystem mutation. Only exact earlier FAILED task is resumable."""
    reserve5()
    if OUT.is_symlink() or not OUT.is_dir():
        raise PermissionError("original 2025 depthzero run output unavailable")
    if not os.access(OUT, os.W_OK | os.X_OK):
        raise PermissionError("actual cripta actor cannot write existing research output")
    if (OUT / "resume5_launch.json").exists() or (OUT / "resume5_launch.json").is_symlink():
        raise FileExistsError("reserve5 resume already launched; reconcile before any restart")
    if (OUT / "resume5_pilot.json").exists() or (OUT / "resume5_pilot.json").is_symlink():
        raise FileExistsError("reserve5 pilot already attempted; reconcile state")
    status = json.loads((OUT / "status.json").read_text())
    if (
        status.get("stage") != "FAILED"
        or status.get("error") != ORIGINAL_FAILURE
        or status.get("source_head") != ORIGINAL_HEAD
    ):
        raise RuntimeError("unexpected original job status; no blind replay")
    launch = json.loads((OUT / "launch.json").read_text())
    pid = launch.get("pid")
    if launch.get("source_head") != ORIGINAL_HEAD or not isinstance(pid, int):
        raise ValueError("original launch identity unknown")
    if Path(f"/proc/{pid}").exists():
        raise RuntimeError("original depthzero worker may still be active")
    prior = json.loads((OUT / "study.json").read_text())
    if (
        prior.get("source_head") != ORIGINAL_HEAD
        or prior.get("source_sha256") != original.sha(SOURCE / original.SELF_REL)
        or prior.get("exact_depth_percent") != "0"
    ):
        raise RuntimeError("original strict-zero study source identity mismatch")
    folder = OUT / "checkpoint" / "long_only"
    state, last_manifest, last_sha = engine.load_parts(folder)
    if (
        state is None or last_manifest is None
        or state["next_day"] != INITIAL_LONG_DAYS
        or last_manifest["source_head_first"] != ORIGINAL_HEAD
        or last_manifest["index"] != INITIAL_LONG_DAYS - 1
    ):
        raise RuntimeError("103-day original SHA checkpoint chain mismatch")
    if engine.load_parts(OUT / "checkpoint" / "short_only")[0] is not None:
        raise RuntimeError("short checkpoint already started")
    if any((OUT / "full").iterdir()) or any(OUT.glob("qc_*.json")):
        raise RuntimeError("original final result/QC already exists")
    return {
        "status": "RESUME5_PREFLIGHT_PASS_NO_MUTATION",
        "previous_status": status,
        "previous_pid": pid,
        "existing_long_days": state["next_day"],
        "short_days": 0,
        "last_original_manifest_sha256": last_sha,
        "source_head_at_resume": expected,
        "old_source_head": ORIGINAL_HEAD,
        "resume5_source_sha256": original.sha(SOURCE / SELF_REL),
        "free_gib": round(free_bytes() / 2**30, 3),
        "owner_approved_reserve_gib": 5,
    }


def write_once(path: Path, obj: dict) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError("immutable resume record collision: " + str(path))
    with path.open("x", encoding="utf-8") as stream:
        json.dump(obj, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def pilot(engine, expected: str) -> dict:
    proof = check_resume(engine, expected)
    write_once(OUT / "resume5_pilot.json", {**proof, "status": "RESUME5_PILOT_INTENT"})
    original.set_status("RESUME5_PILOT_RUNNING", side="LONG_ONLY",
                        days_done=INITIAL_LONG_DAYS, reserve_gib=5,
                        source_head=expected)
    try:
        result = run5(engine, "LONG_ONLY", expected, max_days=1)
        days = original.read_day(engine, "LONG_ONLY")
        if days != INITIAL_LONG_DAYS + 1 or result["status"] != "PARTIAL_DURABLE_RESUME_READY_NOT_FINAL":
            raise RuntimeError("reserve5 pilot did not commit exactly one next UTC day")
        _, last, digest = engine.load_parts(OUT / "checkpoint" / "long_only")
        if last["index"] != INITIAL_LONG_DAYS:
            raise RuntimeError("resume5 pilot SHA checkpoint index mismatch")
        record = {**proof, "status": "RESUME5_PILOT_PASS",
                  "pilot_days_done": days, "pilot_manifest_sha256": digest}
        original.atom(OUT / "resume5_pilot.json", record)
        original.set_status("RESUME5_PILOT_PASS", side="LONG_ONLY",
                            days_done=days, reserve_gib=5,
                            source_head=expected)
        return record
    except BaseException as exc:
        original.set_status("RESUME5_PILOT_FAILED", error=str(exc),
                            long_days=original.read_day(engine,"LONG_ONLY"),
                            source_head=expected)
        raise


def launch(engine, expected: str) -> dict:
    status = json.loads((OUT / "status.json").read_text())
    if status.get("stage") != "RESUME5_PILOT_PASS" or status.get("source_head") != expected:
        raise RuntimeError("owner-approved one-day resume pilot not complete")
    p = json.loads((OUT / "resume5_pilot.json").read_text())
    if p.get("status") != "RESUME5_PILOT_PASS" or p["source_head_at_resume"] != expected:
        raise RuntimeError("durable resume5 pilot evidence mismatch")
    if original.read_day(engine, "LONG_ONLY") != INITIAL_LONG_DAYS + 1:
        raise RuntimeError("pilot checkpoint count mismatch")
    if original.read_day(engine, "SHORT_ONLY") != 0:
        raise RuntimeError("short side already running")
    reserve5()
    if Path(f"/proc/{p['previous_pid']}").exists():
        raise RuntimeError("original writer process unexpectedly reappeared")
    intent = OUT / "resume5_launch.json"
    write_once(intent, {"status": "RESUME5_LAUNCH_INTENT",
                        "source_head": expected, "resume5_source_sha256":
                        original.sha(SOURCE / SELF_REL)})
    with (OUT / "resume5_controller.log").open("x", encoding="utf-8") as log:
        cmd = [sys.executable, "-B", str(SOURCE / SELF_REL),
               "--worker", "--source-head", expected]
        proc = subprocess.Popen(
            cmd, cwd=str(SOURCE), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT,
            close_fds=True, start_new_session=True,
        )
    record = {"status": "RESUME5_LAUNCHED_NOT_YET_VERIFIED",
              "pid": proc.pid, "source_head": expected,
              "resume5_source_sha256": original.sha(SOURCE / SELF_REL),
              "log": str(OUT / "resume5_controller.log")}
    original.atom(intent, record)
    return record


def worker(engine, expected: str) -> None:
    fcntl = importlib.import_module("fcntl")
    record = json.loads((OUT / "resume5_launch.json").read_text())
    pilot_record = json.loads((OUT / "resume5_pilot.json").read_text())
    if (
        record["source_head"] != expected
        or record["resume5_source_sha256"] != original.sha(SOURCE / SELF_REL)
        or pilot_record["status"] != "RESUME5_PILOT_PASS"
    ):
        raise RuntimeError("resume5 worker source/pilot/launch identity mismatch")
    with (OUT / ".worker.lock").open("a+",encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another worker owns depthzero research lock") from exc
        try:
            original.set_status("RUNNING_RESERVE5", side="LONG_ONLY",
                                days_done=original.read_day(engine, "LONG_ONLY"),
                                days_total=183, source_head=expected,
                                reserve_gib=5)
            for side in SIDES:
                while original.read_day(engine, side) < 183:
                    current = original.read_day(engine, side)
                    report = run5(engine, side, expected, max_days=min(5,183-current))
                    done = original.read_day(engine, side)
                    if done <= current:
                        raise RuntimeError("no progress despite successful day batch")
                    print(json.dumps({"event":"RESERVE5_DAY_BATCH","side":side,
                        "days_done":done,"day_status":report["status"],
                        "free_gib":round(free_bytes()/2**30,3)}),flush=True)
                    original.set_status("RUNNING_RESERVE5", side=side,
                                        days_done=done, days_total=183,
                                        reserve_gib=5, source_head=expected)
                qc = original.independent_qc(side, expected)
                print(json.dumps({"event":"DEPTHZERO_INDEPENDENT_QC_PASS",
                                  "side":side,"cases":qc["cases"]},sort_keys=True),flush=True)
            combined = {c:{"baseline":D(0),"depth0":D(0)} for c in CASES}
            allqc = {}
            for side in SIDES:
                qc = json.loads((OUT / ("qc_"+side.lower()+".json")).read_text())
                allqc[side] = qc
                for c in CASES:
                    combined[c]["baseline"] += D(qc["cases"][c]["baseline_net_usdt"])
                    combined[c]["depth0"] += D(qc["cases"][c]["net_after_modeled_fees_usdt"])
            result = {
                "status": "COMPLETED_INDEPENDENT_QC_PASS_DEPTHZERO_2025_V1",
                "source_head": expected,
                "resume5_source_sha256": original.sha(SOURCE/SELF_REL),
                "prior_original_head": ORIGINAL_HEAD,
                "effective_disk_reserve_gib": 5,
                "directional_qc": allqc,
                "combined": {c:{
                    "baseline_net_usdt":str(v["baseline"]),
                    "depth0_net_usdt":str(v["depth0"]),
                    "difference_net_usdt":str(v["depth0"]-v["baseline"]),
                } for c,v in combined.items()},
            }
            write_once(OUT/"summary.json",result)
            original.set_status("COMPLETED_INDEPENDENT_QC_PASS",days_done=183,
                                days_total=183,source_head=expected,reserve_gib=5)
        except BaseException as exc:
            traceback.print_exc()
            original.set_status("FAILED_RESERVE5",error=str(exc),
                                source_head=expected,reserve_gib=5,
                                long_days=original.read_day(engine,"LONG_ONLY"),
                                short_days=original.read_day(engine,"SHORT_ONLY"))
            raise


def main() -> None:
    parser = argparse.ArgumentParser()
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--preflight",action="store_true")
    actions.add_argument("--pilot",action="store_true")
    actions.add_argument("--launch",action="store_true")
    actions.add_argument("--worker",action="store_true")
    parser.add_argument("--source-head",required=True)
    args = parser.parse_args()
    engine, _proof = prepare(args.source_head)
    if args.preflight:
        print(json.dumps(check_resume(engine,args.source_head),sort_keys=True),flush=True)
    elif args.pilot:
        print(json.dumps(pilot(engine,args.source_head),sort_keys=True),flush=True)
    elif args.launch:
        print(json.dumps(launch(engine,args.source_head),sort_keys=True),flush=True)
    else:
        worker(engine,args.source_head)


if __name__ == "__main__":
    main()
