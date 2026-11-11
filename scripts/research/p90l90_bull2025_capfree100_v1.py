#!/usr/bin/env python3
"""2025 P90L90 research-only capital-unconstrained NO_PAUSE vs SECOND48.

Uses the ORIGINAL, SHA-pinned 2025 native-tick merge, causal candidate admission,
opposite L5 TP, TRAIL035, and scenario hardstop pause. Exactly two fixed-size
books per direction; capital never vetoes Entry (synthetic 1e12 USDT bookkeeping
base is NOT an investment account or real margin). Each position's notional is
100 USDT: 20 USDT synthetic margin at fixed 5x.

Server actor/path contract (DEV §5.1):
  effective writer: cripta UID107/GID109, never root
  reads: /srv/cripta/source_checkout/scripts/research (GitHub-main mirror)
         /data/cripta/research/runs/p90l90_bull_2025_holdout_v1/{inputs,results}
  writes/creates/renames: ONLY under
         /data/cripta/research/runs/p90l90_bull_2025_holdout_v1/results/
             portfolio_capfree100_2025_v1/{checkpoint,full,controller.log,status.json,
                                          worker.lock,qc_*.json}
  delete: none, except replace ephemeral status.tmp by atomic rename
  DB/exchange/runtime/systemd activation: none
  systemd sandbox: none (standalone approved cripta research job)
  original inputs and published checkpoints never mutated
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import traceback
from decimal import Decimal as D

SOURCE = pathlib.Path("/srv/cripta/source_checkout")
sys.path.insert(0, str(SOURCE))
from scripts.research import p90l90_bull2025_portfolio_chunked_v2 as engine  # noqa: E402

old = engine.old
SELF_REL = "scripts/research/p90l90_bull2025_capfree100_v1.py"
OUT = old.RESULTS / "portfolio_capfree100_2025_v1"
VIRTUAL_BASE = D("1000000000000")
MARGIN = D("20")
POSITION_NOTIONAL = MARGIN * old.LEVERAGE
SIDES = ("LONG_ONLY", "SHORT_ONLY")
SCENARIOS = ("NO_PAUSE", "SECOND48")
MAX_DAYS = 5
RESERVE_BYTES = 16 * 2**30


def sha(path: pathlib.Path) -> str:
    return old.sha(path)


def configure() -> None:
    """Only local research process module constants; original sources untouched."""
    if POSITION_NOTIONAL != D("100") or MARGIN * old.LEVERAGE != D("100"):
        raise ValueError("unapproved cap-free research notional")
    old.START_WALLET = VIRTUAL_BASE
    old.FIXED_MARGIN = MARGIN
    old.SCENARIOS = SCENARIOS
    old.CASH_MODES = ("fixed",)
    engine.PARTS = OUT / "checkpoint"
    engine.FINAL = OUT / "full"
    engine.STATUS = "QC_PASS_CAPFREE100_NATIVE_2025_V1"
    engine.PINNED = tuple(dict.fromkeys((*engine.PINNED, SELF_REL)))


def preflight(source_head: str, *, initial: bool = False) -> dict:
    if os.geteuid() != 107 or os.getegid() != 109:
        raise PermissionError("research writer must be exact cripta UID107/GID109")
    if not source_head or len(source_head) != 40 or any(ch not in "0123456789abcdef" for ch in source_head):
        raise ValueError("exact source commit required")
    if old.entry.git_head() != source_head:
        raise RuntimeError("source mirror HEAD differs from approved GitHub main")
    if not (SOURCE / SELF_REL).is_file() or (SOURCE / SELF_REL).is_symlink():
        raise PermissionError("driver source identity unavailable")
    if sha(SOURCE / SELF_REL) == "":
        raise RuntimeError("driver source SHA missing")
    if OUT.is_symlink() or engine.PARTS.is_symlink() or engine.FINAL.is_symlink():
        raise PermissionError("symlink research path denied")
    parent = old.RESULTS
    s = parent.stat()
    if s.st_uid != 107 or s.st_gid != 109 or not os.access(parent, os.W_OK | os.X_OK):
        raise PermissionError("exact result parent owner/group/access mismatch")
    if OUT.exists():
        s = OUT.stat()
        if s.st_uid != 107 or s.st_gid != 109 or not os.access(OUT, os.W_OK | os.X_OK):
            raise PermissionError("existing output parent lacks cripta permissions")
        for side in SIDES:
            folder = engine.PARTS / side.lower()
            if folder.exists():
                _, manifest, _ = engine.load_parts(folder)
                if manifest and (manifest.get("source_pin_sha256", {}).get(SELF_REL) != sha(SOURCE / SELF_REL)):
                    raise RuntimeError("capfree driver source changed since checkpoint")
    if shutil.disk_usage(parent).free < RESERVE_BYTES:
        raise RuntimeError("research disk reserve below 16 GiB")
    if initial and OUT.exists():
        raise FileExistsError("new capfree run must not overwrite/duplicate existing state")
    return {"status": "PREFLIGHT_PASS_NO_MUTATION", "actor_uid": os.geteuid(),
            "source_head": source_head, "script_sha256": sha(SOURCE / SELF_REL),
            "output": str(OUT), "existing": OUT.exists()}


def prepare(source_head: str) -> None:
    preflight(source_head)
    for folder in (OUT, engine.PARTS, engine.FINAL):
        if not folder.exists():
            folder.mkdir(mode=0o2770)
        s = folder.stat()
        if folder.is_symlink() or s.st_uid != 107 or s.st_gid != 109:
            raise PermissionError("prepared directory not owned by cripta")
    meta = OUT / "study.json"
    if not meta.exists():
        document = {
            "research": "P90L90_BULL2025_CAPFREE100_V1",
            "status": "RESEARCH_ONLY_NOT_TRADING_POLICY",
            "source_head_first": source_head,
            "driver_sha256": sha(SOURCE / SELF_REL),
            "period_utc": ["2025-04-01T00:00:00Z", "2025-10-01T00:00:00Z"],
            "exact_original_native_tick_engine": engine.SELF_REL,
            "versioned_method": "same original 2025 Entry/Exit, fixed notional $100, virtual 1e12 wallet prevents ALL capital rejections",
            "cases": list(SCENARIOS), "sides": list(SIDES),
            "notional_usdt": str(POSITION_NOTIONAL),
            "synthetic_margin_usdt": str(MARGIN),
            "virtual_base_usdt_not_investable": str(VIRTUAL_BASE),
            "excluded": ["funding", "slippage", "spread", "latency", "exchange fill", "liquidation", "real margin"],
            "result_reporting": "report modeled NET PnL (=virtual ending wallet minus virtual base); never report virtual base as invested capital",
        }
        with meta.open("x", encoding="utf-8") as f:
            json.dump(document, f, sort_keys=True, indent=2)
            f.write("\n")
    else:
        doc = json.loads(meta.read_text())
        if doc["driver_sha256"] != sha(SOURCE / SELF_REL) or doc["notional_usdt"] != "100":
            raise RuntimeError("immutable capfree study identity mismatch")


def state_update(stage: str, **fields) -> None:
    payload = {"stage": stage, "source_head_at_launch": fields.pop("source_head_at_launch", None), **fields}
    tmp = OUT / "status.tmp"
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(OUT / "status.json")


def independent_qc(side: str) -> dict:
    path = engine.FINAL / side.lower() / "result.json"
    report = json.loads(path.read_text())
    assert report["status"] == "INTERNAL_QC_PASS_PENDING_INDEPENDENT"
    assert report["evaluation_days"] == 183
    assert set(report["results"]) == {"NO_PAUSE_fixed", "SECOND48_fixed"}
    assert D(report["start_wallet_each_case_usdt"]) == VIRTUAL_BASE
    assert D(report["fixed_margin_usdt"]) == MARGIN
    assert D(report["leverage"]) == D("5")
    assert report["source_pin_files_sha256"][SELF_REL] == sha(SOURCE / SELF_REL)
    results = {}
    for case in SCENARIOS:
        key = case + "_fixed"
        leaf = engine.FINAL / side.lower() / ("ledger_" + key + ".jsonl")
        if sha(leaf) != report["ledger_sha256"][key]:
            raise ValueError("immutable capfree ledger SHA mismatch")
        trades = [json.loads(s) for s in leaf.read_text().splitlines() if s]
        counts = report["results"][key]["counts"]
        if counts.get("capital_blocked_candidate", 0) != 0:
            raise ValueError("unconstrained simulation capital-blocked a candidate")
        if counts["accepted"] != counts["closed"] + counts["censored_open"]:
            raise ValueError("accepted/closed/censored reconciliation failed")
        if counts["closed"] != len(trades) or counts["exit_HARD_STOP"] != sum(t["exit_reason"]=="HARD_STOP" for t in trades):
            raise ValueError("exit count mismatch")
        money = D(0)
        per_coin = {s: {"closed": 0, "hard": 0, "net": D(0)} for s in old.SYMBOLS}
        for t in trades:
            ratio = D(t["exit_price"]) / D(t["entry_price"])
            gross = old.money(POSITION_NOTIONAL * (ratio-D(1)) * (D(1) if side=="LONG_ONLY" else D(-1)))
            entryfee = old.money(POSITION_NOTIONAL * old.TAKER)
            exitfee = old.money(POSITION_NOTIONAL * ratio * (old.MAKER if t["maker_exit"] else old.TAKER))
            if (D(t["gross_usdt"]), D(t["entry_fee_usdt"]), D(t["exit_fee_usdt"]), D(t["net_usdt"])) != (gross,entryfee,exitfee,gross-entryfee-exitfee):
                raise ValueError("independent price/fee/side PnL parity failed")
            per_coin[t["symbol"]]["closed"] += 1
            per_coin[t["symbol"]]["hard"] += (t["exit_reason"]=="HARD_STOP")
            per_coin[t["symbol"]]["net"] += D(t["net_usdt"])
            money += D(t["net_usdt"])
        open_positions = report["results"][key].get("open_positions", [])
        if not isinstance(open_positions,list):
            raise ValueError("censored open positions type mismatch")
        for pos in open_positions:
            money -= old.money(POSITION_NOTIONAL * old.TAKER)
            per_coin[pos["symbol"]]["net"] -= old.money(POSITION_NOTIONAL * old.TAKER)
        if money != D(report["results"][key]["wallet_usdt"]) - VIRTUAL_BASE:
            raise ValueError("independent cash/fees/wallet mismatch")
        if case == "NO_PAUSE":
            for sym in old.SYMBOLS:
                unit_path = old.UNIT_PARENT / sym / f"trade_ledger_{side.split('_')[0].lower()}.jsonl"
                unit = [json.loads(s) for s in unit_path.read_text().splitlines() if s]
                actual = [t for t in trades if t["symbol"]==sym]
                projection = lambda t: tuple(t[c] for c in ("candidate_event_id","entry_ts","exit_ts","entry_price","exit_price","exit_reason","maker_exit"))
                if list(map(projection,unit)) != list(map(projection,actual)):
                    raise ValueError("no-cap NO_PAUSE did not exactly reproduce independent original unit Exit "+sym)
        results[case] = {"closed": len(trades), "hardstops": counts["exit_HARD_STOP"],
                         "accepted": counts["accepted"], "censored": counts["censored_open"],
                         "paused_candidates": counts.get("paused_candidate",0),
                         "net_after_modeled_fees_usdt": str(money),
                         "per_coin": {c: {"closed":v["closed"],"hardstops":v["hard"],
                                          "net_after_fees_usdt":str(v["net"])}
                                      for c,v in per_coin.items()}}
    qc = {"status": "INDEPENDENT_QC_PASS_CAPFREE100_2025_V1",
          "side": side, "result_sha256": sha(path), "driver_sha256": sha(SOURCE / SELF_REL),
          "last_day":"2025-09-30", "cases":results}
    qcpath = OUT / f"qc_{side.lower()}.json"
    if qcpath.exists() or qcpath.is_symlink():
        raise FileExistsError("immutable capfree independent QC already published")
    with qcpath.open("x", encoding="utf-8") as f:
        json.dump(qc,f,sort_keys=True,indent=2)
        f.write("\n")
    return qc


def worker(source_head: str) -> None:
    prepare(source_head)
    with (OUT / "worker.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            for side in SIDES:
                while True:
                    preflight(source_head)
                    saved,_,_ = engine.load_parts(engine.PARTS / side.lower())
                    days = 0 if saved is None else saved["next_day"]
                    if days == engine.TOTAL_DAYS:
                        break
                    state_update("RUNNING", side=side, days_done=days, days_total=183, source_head_at_launch=source_head)
                    result = engine.run(side, source_head, MAX_DAYS)
                    saved,_,_ = engine.load_parts(engine.PARTS / side.lower())
                    days=saved["next_day"]
                    state_update("RUNNING", side=side, days_done=days, days_total=183, source_head_at_launch=source_head)
                    print(json.dumps({"stage":"CAPFREE100_DAY_BATCH_VERIFIED","side":side,"days":days,"worker_result":result.get("stage",result.get("status"))}),flush=True)
                if not (OUT / f"qc_{side.lower()}.json").exists():
                    check=independent_qc(side)
                    print(json.dumps({"stage":"CAPFREE100_SIDE_QC_PASS","side":side,"cases":{k:{"hard":v["hardstops"],"net":v["net_after_modeled_fees_usdt"]} for k,v in check["cases"].items()}}),flush=True)
            state_update("COMPLETED_INDEPENDENT_QC_PASS", days_done=183, days_total=183, source_head_at_launch=source_head)
        except BaseException as exc:
            state_update("FAILED", error_type=type(exc).__name__, error=str(exc), source_head_at_launch=source_head)
            traceback.print_exc()
            raise


def launch(source_head: str) -> dict:
    preflight(source_head)
    prepare(source_head)
    lock = OUT / "worker.lock"
    # Do not duplicate a live worker, including previous ambiguous launches.
    status = OUT / "launch.json"
    if status.exists():
        info=json.loads(status.read_text())
        pid=int(info.get("pid",0))
        if pid > 0 and pathlib.Path(f"/proc/{pid}").exists():
            raise RuntimeError("existing capfree worker still alive, never duplicate")
        raise RuntimeError("prior capfree launch exists; resume requires independent job reconciliation")
    log = (OUT / "controller.log").open("a", encoding="utf-8")
    cmd=[sys.executable,"-B",str(SOURCE/SELF_REL),"--worker","--source-head",source_head]
    process = subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                               close_fds=True,start_new_session=True,cwd=str(SOURCE))
    log.close()
    info={"status":"LAUNCHED_NOT_YET_VERIFIED","pid":process.pid,
          "source_head":source_head,"actor_uid":os.geteuid(),"log":str(OUT/"controller.log")}
    with status.open("x",encoding="utf-8") as f:
        json.dump(info,f,indent=2)
        f.write("\n")
    return info


def main() -> None:
    p=argparse.ArgumentParser()
    p.add_argument("--source-head",required=True)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight",action="store_true")
    mode.add_argument("--launch",action="store_true")
    mode.add_argument("--worker",action="store_true")
    args=p.parse_args()
    configure()
    if args.preflight:print(json.dumps(preflight(args.source_head)))
    elif args.launch:print(json.dumps(launch(args.source_head)))
    else:worker(args.source_head)


if __name__=="__main__":
    main()
