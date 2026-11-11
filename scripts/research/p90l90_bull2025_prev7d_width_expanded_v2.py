#!/usr/bin/env python3
"""Owner-approved P90L90 PREV7D width sweep, exact H9 depth zero, 2025.

Issue #617. A versioned, post-holdout exploratory comparison of causal
previous-seven-complete-UTC-day L5-3 width bands +/-0.10, +/-0.20,
+/-0.80, +/-0.90, +/-1.00, +/-1.50 percentage points.
Immutable prior +/-0.25, +/-0.50, +/-0.75 results reused (QC, SHA checked).
Six additional tolerance bands; original frozen native tick/Exit/fees
and both NO_PAUSE/SECOND48 in the same tick pass, one worker.
Exact zero-width tolerance checked: zero strict H9=0 candidates, no replay.

DEV 5.1/19 filesystem actor contract:
 EFFECTIVE_ACTOR: cripta UID107:GID109
 READ_PATHS: clean detached SOURCE worktree under /data/cripta/research/worktrees;
             original immutable 2025 Bybit tapes, frozen candidate manifests,
             source/Exit/portfolio baseline results under /data/cripta/research/runs
 CREATE/WRITE/RENAME: only OUT and its new band010/020/080/090/100/150/{checkpoint,full},
                      unique pilot, launch, status, qc, summary and log files
 DELETE: none; replace only private mutable status.json through atomic rename
 SYSTEMD_SANDBOX: none (not a service); DB_ROLE: none; no Exchange/LIVE changes
 OWNER/GROUP/MODE: preflight cripta:cripta 2770 on parent; new children mode 2770
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import traceback
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))
original = importlib.import_module("scripts.research.p90l90_bull2025_depthzero_v1")
resume = importlib.import_module(
    "scripts.research.p90l90_bull2025_depthzero_reserve5_resume_v1"
)

SELF_REL = "scripts/research/p90l90_bull2025_prev7d_width_expanded_v2.py"
RESULTS = original.RESULTS
BASE = RESULTS / "portfolio_depthzero_2025_v1"
OUT = RESULTS / "portfolio_depthzero_prev7d_expanded_2025_v2"
PREVIOUS = RESULTS / "portfolio_depthzero_prev7d_width_sweep_2025_v1"
WIDTHS = {
    "010": D("0.10"), "020": D("0.20"), "025": D("0.25"),
    "050": D("0.50"), "075": D("0.75"), "080": D("0.80"),
    "090": D("0.90"), "100": D("1.00"), "150": D("1.50"),
}
NEW = ("010", "020", "080", "090", "100", "150")
REUSED = ("025", "050", "075")
SIDES = ("LONG_ONLY", "SHORT_ONLY")
CASES = ("NO_PAUSE", "SECOND48")
SYMBOLS = ("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT")
RESERVE = 5 * 2**30
EXPECTED_050 = {"LONG_ONLY": 2583, "SHORT_ONLY": 2863}
EXPECTED_COUNTS = {
    "010": {"LONG_ONLY": 581, "SHORT_ONLY": 592},
    "020": {"LONG_ONLY": 1148, "SHORT_ONLY": 1211},
    "025": {"LONG_ONLY": 1401, "SHORT_ONLY": 1525},
    "050": EXPECTED_050,
    "075": {"LONG_ONLY": 3510, "SHORT_ONLY": 4057},
    "080": {"LONG_ONLY": 3645, "SHORT_ONLY": 4241},
    "090": {"LONG_ONLY": 3909, "SHORT_ONLY": 4554},
    "100": {"LONG_ONLY": 4131, "SHORT_ONLY": 4847},
    "150": {"LONG_ONLY": 4817, "SHORT_ONLY": 5780},
}
QC_STATUS = "INDEPENDENT_QC_PASS_P90L90_DEPTHZERO_PREV7D_EXPANDED_2025_V2"


def sha(p: Path) -> str:
    return original.sha(p)


def day_of(event: dict) -> str:
    return datetime.fromtimestamp(
        int(event["state_effective_ts"]), UTC
    ).date().isoformat()


def eligible(event: dict, side: str, band: D, medians: dict) -> bool:
    if event["side"] != side or D(str(event["depth_pct"])) != 0:
        return False
    median = medians[event["symbol"]][day_of(event)]
    return abs(D(str(event["width_pct"])) - median) <= band


def verify_source_catalog() -> dict:
    """Independent candidate-archive and exact historical +/-0.50 parity."""
    median = {}
    selected = {b: {s: set() for s in SIDES} for b in WIDTHS}
    counts = {b: {s: 0 for s in SIDES} for b in WIDTHS}
    checks = {}
    for symbol in SYMBOLS:
        folder = RESULTS / "entry_full_2025_v1" / symbol
        manifest = json.loads((folder / "manifest.json").read_text())
        source = folder / ("candidates_" + symbol + ".jsonl")
        if sha(source) != manifest["events_sha256"]:
            raise ValueError("immutable 2025 candidate digest differs: " + symbol)
        if manifest["symbol"] != symbol or manifest["PREV7D_WIDTH_LOOKAHEAD"] is not False:
            raise ValueError("2025 causal width provenance incorrect")
        med = manifest["medians_PREV7D"]
        if len(med) != 183:
            raise ValueError("not exactly 183 previous-seven-day medians")
        median[symbol] = {day: D(value) for day, value in med.items()}
        checks[symbol] = {"candidate_sha256": manifest["events_sha256"],
                          "median_manifest_sha256": sha(folder / "manifest.json")}
        seen = set()
        for line in source.open(encoding="utf-8"):
            event = json.loads(line)
            key = event["event_id"]
            if key in seen:
                raise ValueError("repeated candidate event identity " + symbol)
            seen.add(key)
            if event["symbol"] != symbol or event["side"] not in ("LONG", "SHORT"):
                raise ValueError("candidate source instrument/side mismatch")
            width = D(str(event["width_pct"]))
            prev = median[symbol][day_of(event)]
            if not (width.is_finite() and prev.is_finite()):
                raise ValueError("nonfinite causal width")
            matched_050 = abs(width - prev) <= WIDTHS["050"]
            if matched_050 != event["PREV7D"]:
                raise ValueError("historical PREV7D +/-0.50 parity failed: " + key)
            if D(str(event["depth_pct"])) != 0:
                continue
            side = event["side"] + "_ONLY"
            for code, band in WIDTHS.items():
                if abs(width - prev) <= band:
                    selected[code][side].add(key)
                    counts[code][side] += 1
    if counts != EXPECTED_COUNTS:
        raise ValueError("strict-zero width candidate counts drift from audited source")
    ordered = tuple(WIDTHS)
    for side in SIDES:
        for left, right in zip(ordered, ordered[1:]):
            if not selected[left][side] <= selected[right][side]:
                raise ValueError("causal nested width cohorts lost")
    return {"median": median, "selected": selected,
            "counts": counts, "source_checks": checks}


def verify_base() -> dict:
    if os.geteuid() != 107 or os.getegid() != 109:
        raise PermissionError("actual Unix writer cripta UID107 GID109 required")
    if RESULTS.stat().st_uid != 107 or RESULTS.stat().st_gid != 109:
        raise PermissionError("research result parent owner/group mismatch")
    if not os.access(RESULTS, os.W_OK | os.X_OK) or RESULTS.is_symlink():
        raise PermissionError("research parent unsafe/unwritable")
    if shutil.disk_usage(RESULTS).free < RESERVE:
        raise RuntimeError("owner-approved 5GiB research reserve unavailable")
    baseline = {}
    for side in SIDES:
        q = json.loads((BASE / ("qc_" + side.lower() + ".json")).read_text())
        if q["status"] != "INDEPENDENT_QC_PASS_P90L90_DEPTHZERO_2025_V1":
            raise ValueError("prior exact depthzero baseline is not independently QC")
        if sha(BASE / "full" / side.lower() / "result.json") != q["results_sha256"]:
            raise ValueError("prior depthzero financial baseline changed")
        if q["candidate_count"] != EXPECTED_050[side]:
            raise ValueError("prior +/-0.50 candidate count mismatch")
        baseline[side] = q
    if (BASE / "summary.json").is_file():
        s = json.loads((BASE / "summary.json").read_text())
        if s["status"] != "COMPLETED_INDEPENDENT_QC_PASS_DEPTHZERO_2025_V1":
            raise ValueError("prior full strict-depthzero baseline incomplete")
    else:
        raise ValueError("prior full depthzero summary unavailable")
    # Prior +/-0.25 and +/-0.75 are already independently QC'd and are
    # never re-executed or edited by this expanded source-only study.
    prior_summary = json.loads((PREVIOUS / "summary.json").read_text())
    if prior_summary["status"] != "COMPLETED_INDEPENDENT_QC_PASS_WIDTH_SWEEP_2025_V1":
        raise ValueError("previous tolerance sweep baseline incomplete")
    for code in ("025", "075"):
        for side in SIDES:
            q = json.loads(
                (PREVIOUS / ("band" + code) / ("qc_" + side.lower() + ".json")).read_text()
            )
            report = PREVIOUS / ("band" + code) / "full" / side.lower() / "result.json"
            if (
                q["status"] != "INDEPENDENT_QC_PASS_P90L90_DEPTHZERO_PREV7D_SWEEP_2025_V1"
                or q["candidate_count"] != EXPECTED_COUNTS[code][side]
                or q["result_sha256"] != sha(report)
            ):
                raise ValueError("previous tolerance sweep result drift: " + code + side)
    return baseline


def prepare(expected: str) -> tuple:
    original.MIN_FREE = RESERVE
    engine, _proof = original.prepare(expected)
    if engine.old.START_WALLET != D("1000000000000"):
        raise ValueError("virtual capfree wallet changed")
    if engine.old.FIXED_MARGIN != D(20) or engine.old.LEVERAGE != D(5):
        raise ValueError("fixed modeled capital changed")
    verify_base()
    catalog = verify_source_catalog()
    if sha(SOURCE / SELF_REL) == "":
        raise RuntimeError("new width source missing")
    engine.PINNED = tuple(dict.fromkeys((*engine.PINNED, SELF_REL)))
    current = {"width": None}
    prior = engine.old.Book.handle  # frozen original + strict0 wrapper
    def handle_width(self, symbol, t, seq, px, events, state, priority):
        code = current["width"]
        if code not in NEW:
            raise RuntimeError("width book called without frozen band identity")
        injected = tuple(
            {**e, "PREV7D": True}
            for e in events
            if eligible(e, self.side, WIDTHS[code], catalog["median"])
        )
        return prior(self, symbol, t, seq, px, injected, state, priority)
    engine.old.Book.handle = handle_width
    return engine, catalog, current


def target(code: str) -> Path:
    if code not in NEW:
        raise ValueError("not a requested new width band")
    return OUT / ("band" + code)


def bind(engine, current: dict, code: str) -> None:
    current["width"] = code
    engine.PARTS = target(code) / "checkpoint"
    engine.FINAL = target(code) / "full"
    engine.STATUS = "QC_PASS_P90L90_DEPTHZERO_PREV7D_" + code + "_2025_V1"


def atom(path: Path, obj: dict) -> None:
    original.atom(path, obj)


def write_once(path: Path, value: dict) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError("immutable output already exists: " + str(path))
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def status(stage: str, **facts) -> None:
    atom(OUT / "status.json", {
        "stage": stage, "source_head": facts.pop("source_head", None),
        "research_only": True, "reserve_gib": 5, **facts,
    })


def preflight(engine, catalog: dict, expected: str) -> dict:
    if OUT.exists() or OUT.is_symlink():
        raise FileExistsError("width sweep already created; no duplicate launch")
    return {
        "status": "PREPARED_READ_ONLY",
        "source_head": expected,
        "source_sha256": sha(SOURCE / SELF_REL),
        "baseline": "P90L90_DEPTHZERO_PREV7D_050_2025_QC_PASS",
        "band_candidate_counts": catalog["counts"],
        "catalog_hashes": catalog["source_checks"],
        "research_out": str(OUT),
        "free_gib": round(shutil.disk_usage(OUT.parent).free / 2**30, 3),
    }


def pilot(engine, catalog, current, expected: str) -> dict:
    proof = preflight(engine, catalog, expected)
    OUT.mkdir(mode=0o2770)
    for code in NEW:
        target(code).mkdir(mode=0o2770)
    write_once(OUT / "study.json", {
        **proof,
        "status": "STUDY_RESEARCH_ONLY_NOT_TRADING",
        "period_utc": ["2025-04-01T00:00:00Z", "2025-10-01T00:00:00Z"],
        "depth_pct": "0",
        "width_bands_new_pp": [str(WIDTHS[code]) for code in NEW],
        "width_bands_reused_pp": [str(WIDTHS[code]) for code in REUSED],
        "zero_band_no_candidates_no_replay": True,
        "scenario_modes": list(CASES),
        "sides": list(SIDES),
        "model": "fixed100USDT_notional_unbounded_virtual_wallet",
        "fees": "frozen taker 0.055 pct entry/marketable exit, resting TP maker 0.02 pct",
        "known_exclusions": ["slippage", "funding", "spread", "latency", "real fills"],
        "post_holdout_exploratory_not_blinded_oos": True,
    })
    status("PILOT_RUNNING", side="LONG_ONLY", depth0=True,
           bands=list(NEW), days_done=0, source_head=expected)
    try:
        check = {}
        for code in NEW:
            bind(engine, current, code)
            result = resume.run5(engine, "LONG_ONLY", expected, max_days=1)
            state, last, digest = engine.load_parts(engine.PARTS / "long_only")
            if (
                result["status"] != "PARTIAL_DURABLE_RESUME_READY_NOT_FINAL"
                or state is None or state["next_day"] != 1
                or last["index"] != 0 or not digest
            ):
                raise RuntimeError("one-day width pilot not SHA durable: " + code)
            check[code] = {"days": 1, "manifest_sha256": digest,
                           "candidates": catalog["counts"][code]}
        write_once(OUT / "pilot.json", {
            "status": "PILOT_PASS", "source_head": expected, "bands": check,
        })
        status("PILOT_PASS", pilot=check, source_head=expected)
        return check
    except BaseException as ex:
        traceback.print_exc()
        status("FAILED", phase="PILOT", error=str(ex), source_head=expected)
        raise


def independent_qc(code: str, side: str, source_head: str, catalog: dict) -> dict:
    leaf = target(code) / "full" / side.lower()
    report_file = leaf / "result.json"
    report = json.loads(report_file.read_text())
    previous = json.loads((BASE / ("qc_" + side.lower() + ".json")).read_text())
    if report["side"] != side or report["evaluation_days"] != 183:
        raise ValueError("wrong side/period in width result")
    if report["source_head_at_publication"] != source_head:
        raise ValueError("width frozen source HEAD drift")
    if report["cash_modes"] != ["fixed"] or report["cases"] != list(CASES):
        raise ValueError("width portfolio mode drift")
    if D(report["fixed_margin_usdt"]) != 20 or D(report["leverage"]) != 5:
        raise ValueError("width model notional drift")
    if report["unlimited_no_pause_per_coin_shadow_parity"] is not True:
        raise ValueError("frozen independent per-coin shadow parity missing")
    eligible_ids = catalog["selected"][code][side]
    audited = {}
    for case in CASES:
        key = case + "_fixed"
        ledger = leaf / ("ledger_" + key + ".jsonl")
        if sha(ledger) != report["ledger_sha256"][key]:
            raise ValueError("width ledger digest changed")
        book = report["results"][key]
        counts = book["counts"]
        rows = [json.loads(line) for line in ledger.open(encoding="utf-8")]
        if counts["qualified_candidate"] != catalog["counts"][code][side]:
            raise ValueError("width qualified candidate count mismatch")
        if counts.get("capital_blocked_candidate", 0) != 0:
            raise ValueError("virtual wallet capital exclusion present")
        if counts["accepted"] != counts["closed"] + counts["censored_open"]:
            raise ValueError("accepted != closed+censored")
        if len(rows) != counts["closed"]:
            raise ValueError("closed ledger row mismatch")
        money, hard = D(0), 0
        for trade in rows:
            if (
                trade["candidate_event_id"] not in eligible_ids
                or trade["side"] != side.split("_")[0]
            ):
                raise ValueError("unqualified candidate admitted in width portfolio")
            ratio = D(trade["exit_price"]) / D(trade["entry_price"])
            signed = D(1) if side == "LONG_ONLY" else D(-1)
            gross = (D(100) * (ratio - 1) * signed).quantize(D("0.000000000001"))
            ef = (D(100) * D("0.00055")).quantize(D("0.000000000001"))
            xf = (D(100) * ratio * (
                D("0.0002") if trade["maker_exit"] else D("0.00055")
            )).quantize(D("0.000000000001"))
            net = gross - ef - xf
            if tuple(D(trade[k]) for k in (
                "gross_usdt", "entry_fee_usdt", "exit_fee_usdt", "net_usdt"
            )) != (gross, ef, xf, net):
                raise ValueError("independent modeled commission/PnL mismatch")
            if bool(trade["maker_exit"]) != (trade["exit_reason"] == "RESTING_TP"):
                raise ValueError("maker for a non-resting TP")
            hard += trade["exit_reason"] == "HARD_STOP"
            money += net
        if hard != counts["exit_HARD_STOP"]:
            raise ValueError("hardstop accounting drift")
        money -= D("0.055") * counts["censored_open"]
        if money != D(book["wallet_usdt"]) - D(report["start_wallet_each_case_usdt"]):
            raise ValueError("wallet net and independent trade ledger mismatch")
        audited[case] = {
            "accepted": counts["accepted"], "closed": counts["closed"],
            "censored": counts["censored_open"], "hardstops": hard,
            "net_after_modeled_fees_usdt": str(money),
            "baseline_050_accepted": previous["cases"][case]["accepted"],
            "baseline_050_hardstops": previous["cases"][case]["hardstops"],
            "baseline_050_net_usdt": previous["cases"][case]["net_after_modeled_fees_usdt"],
            "hardstop_per_accepted_pct": str(
                D(100) * hard / counts["accepted"] if counts["accepted"] else D(0)
            ),
        }
    result = {
        "status": QC_STATUS,
        "source_head": source_head,
        "source_sha256": sha(SOURCE / SELF_REL),
        "side": side,
        "band_pp": str(WIDTHS[code]),
        "depth_pct": "0",
        "candidate_count": catalog["counts"][code][side],
        "result_sha256": sha(report_file),
        "cases": audited,
        "censored_not_forced_closed": True,
        "real_exchange_fills": False,
    }
    write_once(target(code) / ("qc_" + side.lower() + ".json"), result)
    return result


def launch(engine, catalog, current, expected: str) -> dict:
    if not OUT.is_dir() or OUT.is_symlink():
        raise RuntimeError("pilot root not available")
    if (OUT / "launch.json").exists() or (OUT / "launch.json").is_symlink():
        raise FileExistsError("width sweep already launched; no duplicates")
    pilot_record = json.loads((OUT / "pilot.json").read_text())
    stat = json.loads((OUT / "status.json").read_text())
    if (
        stat["stage"] != "PILOT_PASS" or
        pilot_record["status"] != "PILOT_PASS" or
        pilot_record["source_head"] != expected
    ):
        raise RuntimeError("width sweep pilot not successfully completed")
    for code in NEW:
        bind(engine, current, code)
        state, _last, _sha = engine.load_parts(engine.PARTS / "long_only")
        if state is None or state["next_day"] != 1:
            raise RuntimeError("unexpected width pilot checkpoint count")
        if (engine.PARTS / "short_only").exists():
            raise RuntimeError("width SHORT already started; do not duplicate")
    if shutil.disk_usage(OUT).free < RESERVE:
        raise RuntimeError("data-disk fell below 5GiB research reserve before launch")
    write_once(OUT / "launch.json", {
        "stage": "LAUNCH_INTENT", "source_head": expected,
        "source_sha256": sha(SOURCE / SELF_REL),
    })
    cmd = [
        sys.executable, "-B", str(SOURCE / SELF_REL),
        "--worker", "--source-head", expected,
    ]
    with (OUT / "controller.log").open("x", encoding="utf-8") as stream:
        proc = subprocess.Popen(
            cmd, cwd=str(SOURCE), stdin=subprocess.DEVNULL,
            stdout=stream, stderr=subprocess.STDOUT,
            close_fds=True, start_new_session=True,
        )
    record = {
        "stage": "LAUNCHED_NOT_YET_VERIFIED",
        "pid": proc.pid, "source_head": expected,
        "source_sha256": sha(SOURCE / SELF_REL),
        "log": str(OUT / "controller.log"),
    }
    atom(OUT / "launch.json", record)
    return record


def worker(engine, catalog: dict, current: dict, expected: str) -> None:
    fcntl = importlib.import_module("fcntl")
    launch_record = json.loads((OUT / "launch.json").read_text())
    if (
        launch_record["source_head"] != expected
        or launch_record["source_sha256"] != sha(SOURCE / SELF_REL)
        or json.loads((OUT / "pilot.json").read_text())["status"] != "PILOT_PASS"
    ):
        raise RuntimeError("unrecognized source or pilot for width worker")
    with (OUT / ".worker.lock").open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another width sweep worker owns lock") from exc
        try:
            for code in NEW:
                bind(engine, current, code)
                for side in SIDES:
                    status("RUNNING", width_pp=str(WIDTHS[code]), side=side,
                           days_done=0, days_total=183,
                           source_head=expected)
                    while True:
                        state, _last, _sh = engine.load_parts(engine.PARTS / side.lower())
                        days = state["next_day"] if state is not None else 0
                        if days == 183:
                            break
                        result = resume.run5(
                            engine, side, expected, max_days=min(5, 183 - days)
                        )
                        now, _last, _sh = engine.load_parts(engine.PARTS / side.lower())
                        if now is None or now["next_day"] <= days:
                            raise RuntimeError("width portfolio no progress")
                        print(json.dumps({
                            "event": "WIDTH_DAY_BATCH",
                            "band": code, "side": side,
                            "days_done": now["next_day"],
                            "status": result["status"],
                            "free_gib": round(shutil.disk_usage(OUT).free / 2**30, 3),
                        }), flush=True)
                        status("RUNNING", width_pp=str(WIDTHS[code]), side=side,
                               days_done=now["next_day"], days_total=183,
                               source_head=expected)
                    qc = independent_qc(code, side, expected, catalog)
                    print(json.dumps({
                        "event": "WIDTH_INDEPENDENT_QC_PASS",
                        "band": code, "side": side, "cases": qc["cases"],
                    }, sort_keys=True), flush=True)
            allqc = {}
            for code in NEW:
                by_side = {}
                for side in SIDES:
                    by_side[side] = json.loads(
                        (target(code) / ("qc_" + side.lower() + ".json")).read_text()
                    )
                allqc[code] = by_side
            compare = {}
            for side in SIDES:
                baseline = json.loads(
                    (BASE / ("qc_" + side.lower() + ".json")).read_text()
                )
                compare[side] = {}
                for case in CASES:
                    original_case = baseline["cases"][case]
                    compare[side][case] = {
                        "050": {
                            "accepted": original_case["accepted"],
                            "hardstops": original_case["hardstops"],
                            "net_after_modeled_fees_usdt": original_case[
                                "net_after_modeled_fees_usdt"
                            ],
                            "hardstop_per_accepted_pct": str(
                                D(100) * original_case["hardstops"]
                                / original_case["accepted"]
                            ),
                        },
                    }
                    for code in ("025", "075"):
                        reused = json.loads((
                            PREVIOUS / ("band" + code) /
                            ("qc_" + side.lower() + ".json")
                        ).read_text())["cases"][case]
                        compare[side][case][code] = {
                            "accepted": reused["accepted"],
                            "hardstops": reused["hardstops"],
                            "net_after_modeled_fees_usdt": reused["net_after_modeled_fees_usdt"],
                            "hardstop_per_accepted_pct": reused["hardstop_per_accepted_pct"],
                            "censored": reused["censored"],
                        }
                    for code in NEW:
                        compare[side][case][code] = allqc[code][side]["cases"][case]
            summary = {
                "status": "COMPLETED_INDEPENDENT_QC_PASS_WIDTH_EXPANDED_2025_V2",
                "source_head": expected, "source_sha256": sha(SOURCE / SELF_REL),
                "original_baseline_head": json.loads(
                    (BASE / "summary.json").read_text()
                )["source_head"],
                "depth_pct": "0",
                "bands_pp": [str(value) for value in WIDTHS.values()],
                "baseline_050_reused_not_replayed": True,
                "baseline_025_075_reused_not_replayed": True,
                "exact_zero_band_empty_no_replay": True,
                "post_holdout_exploratory_not_oos": True,
                "cases": compare, "qc": allqc,
            }
            write_once(OUT / "summary.json", summary)
            status("COMPLETED_INDEPENDENT_QC_PASS", bands=list(WIDTHS),
                   source_head=expected)
        except BaseException as ex:
            traceback.print_exc()
            counts = {}
            for code in NEW:
                for side in SIDES:
                    folder = target(code) / "checkpoint" / side.lower()
                    counts[code + "_" + side] = len(list(folder.glob("day_*"))) if folder.is_dir() else 0
            status("FAILED", error=str(ex), days=counts, source_head=expected)
            raise


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--pilot", action="store_true")
    mode.add_argument("--launch", action="store_true")
    mode.add_argument("--worker", action="store_true")
    parser.add_argument("--source-head", required=True)
    args = parser.parse_args()
    engine, catalog, current = prepare(args.source_head)
    if args.preflight:
        print(json.dumps(preflight(engine, catalog, args.source_head),
                         sort_keys=True), flush=True)
    elif args.pilot:
        print(json.dumps(pilot(engine, catalog, current, args.source_head),
                         sort_keys=True), flush=True)
    elif args.launch:
        print(json.dumps(launch(engine, catalog, current, args.source_head),
                         sort_keys=True), flush=True)
    else:
        worker(engine, catalog, current, args.source_head)


if __name__ == "__main__":
    main()
