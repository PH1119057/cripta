#!/usr/bin/env python3
"""Independent financial, lineage, checkpoint and candidate QC for research #633.

READ ONLY until ALL checks pass. Writes a new immutable qc.json and atomically
updates only the private research status.json. Does NOT replay/alter native
Entry/Exit, Source, Strategy, DB, Exchange, or trading runtime.
Actor cripta UID107:GID109; no elevated privileges.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal as D, ROUND_HALF_EVEN
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))
SELF_REL = "scripts/research/p90l90_depthzero_prev7d_width_confirm_qc_v1.py"
LEAF = "p90l90_depthzero_prev7d_width_confirm_20261112_v1"
RESEARCH = "P90L90_DEPTHZERO_PREV7D_WIDTH_EXTERNAL_REPLICATION_2026_V1"
BANDS = {
    "010": D("0.10"), "020": D("0.20"), "025": D("0.25"),
    "050": D("0.50"), "075": D("0.75"), "080": D("0.80"),
    "090": D("0.90"), "100": D("1.00"), "150": D("1.50"),
}
SIDES = ("LONG_ONLY", "SHORT_ONLY")
MODES = ("NO_PAUSE", "SECOND48")
SYMBOLS = ("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT")
NOTIONAL = D("10")
TAKER = D("0.00055")
MAKER = D("0.0002")
Q = D("0.000000000001")
ROOT = {
    "90d": Path("/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024"),
    "29d": Path("/data/cripta/research/runs/p90l90_prev7d_29d_20261025"),
}
TOTAL_DAYS = {"90d": 90, "29d": 29}
SUFFIX = {"90d": "FULL90", "29d": "SEP29"}
EXPECT = {
    "REFERENCE",
    *(f"w{band}_{side}_{mode}"
      for band in BANDS for side in SIDES for mode in MODES),
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(2**20), b""):
            h.update(block)
    return h.hexdigest()


def net_components(row: dict) -> tuple[D, D, D, D]:
    entry, exit_ = D(row["entry_price"]), D(row["exit_price"])
    if entry <= 0 or exit_ <= 0:
        raise ValueError("nonpositive exchange prices")
    side = row["side"]
    maker = row["maker_exit"]
    if maker != (row["exit_reason"] == "RESTING_TP"):
        raise ValueError("maker classification/reason mismatch")
    gross = NOTIONAL * (exit_ / entry - 1)
    if side == "SHORT":
        gross = -gross
    elif side != "LONG":
        raise ValueError("unexpected position side")
    gross = gross.quantize(Q, rounding=ROUND_HALF_EVEN)
    fee_in = (NOTIONAL * TAKER).quantize(Q, rounding=ROUND_HALF_EVEN)
    fee_out = (NOTIONAL * exit_ / entry * (MAKER if maker else TAKER)).quantize(
        Q, rounding=ROUND_HALF_EVEN
    )
    return gross, fee_in, fee_out, gross - fee_in - fee_out


def expected_reference(period: str) -> dict[str, list]:
    root = ROOT[period] / "results"
    expected = {s: [] for s in SYMBOLS}
    if period == "90d":
        for symbol in SYMBOLS:
            dir_ = root / ("exit_" + symbol.removesuffix("USDT") + "_FULL90")
            result = json.loads((dir_ / "result.json").read_text())
            ledger = dir_ / ("trade_ledger_" + symbol + ".jsonl")
            if sha(ledger) != result["trade_ledger_sha256"]:
                raise ValueError("original 90d independent Exit ledger SHA drift")
            with ledger.open() as stream:
                for line in stream:
                    row = json.loads(line)
                    if row["scenario"] == "PREV7D_TRAIL035":
                        expected[symbol].append(row)
    else:
        folder = root / "compare_exit_sep29_v1"
        result = json.loads((folder / "result.json").read_text())
        ledger = folder / "trade_ledger.jsonl"
        if sha(ledger) != result["ledger_sha256"]:
            raise ValueError("original 29d independent Exit ledger SHA drift")
        with ledger.open() as stream:
            for line in stream:
                row = json.loads(line)
                if row["portfolio_scenario"] == "BASELINE":
                    expected[row["symbol"]].append({
                        k: v for k, v in row.items() if k != "portfolio_scenario"
                    })
    return expected


def source_cohorts(period: str, reported: dict) -> tuple[dict, dict]:
    total = {b: {s: 0 for s in SIDES} for b in BANDS}
    ids = {f"w{b}_{s}": set() for b in BANDS for s in SIDES}
    root = ROOT[period] / "results"
    for symbol in SYMBOLS:
        folder = root / ("entry_" + symbol.removesuffix("USDT") + "_" + SUFFIX[period])
        manifest = json.loads((folder / "manifest.json").read_text())
        file = folder / ("candidates_" + symbol + ".jsonl")
        if (manifest["symbol"] != symbol
                or manifest["PREV7D_WIDTH_LOOKAHEAD"] is not False
                or manifest["events_sha256"] != sha(file)
                or manifest["events_sha256"] != reported["candidate_source_sha256_by_symbol"][symbol]):
            raise ValueError("independent frozen candidate source SHA drift")
        medians = {day: D(str(value))
                   for day, value in manifest["medians_PREV7D"].items()}
        with file.open() as stream:
            for line in stream:
                event = json.loads(line)
                mday = datetime.fromtimestamp(int(event["state_effective_ts"]), UTC).date().isoformat()
                diff = abs(D(str(event["width_pct"])) - medians[mday])
                if bool(event["PREV7D"]) != (diff <= BANDS["050"]):
                    raise ValueError("independent frozen PREV7D label parity failure")
                if D(str(event["depth_pct"])) != 0:
                    continue
                side = event["side"] + "_ONLY"
                for code, width in BANDS.items():
                    if diff <= width:
                        total[code][side] += 1
                        key = f"w{code}_{side}"
                        if event["event_id"] in ids[key]:
                            raise ValueError("duplicate exact Entry event_id")
                        ids[key].add(event["event_id"])
    if total != reported["cohort_candidate_counts"]:
        raise ValueError("independent strict-zero cohort counts drift")
    return total, ids


def audit(period: str, expected_source_head: str) -> dict:
    if period not in ROOT:
        raise ValueError("period must be 90d or 29d")
    if (os.geteuid(), os.getegid()) != (107, 109):
        raise PermissionError("QC must use actual cripta writer")
    root = ROOT[period] / "results" / LEAF
    if not root.is_dir() or root.is_symlink():
        raise ValueError("exact run output directory missing")
    result_file = root / "result.json"
    ledger_file = root / "trade_ledger.jsonl"
    report = json.loads(result_file.read_text())
    if (report["research"] != RESEARCH or report["period"] != period
            or report["status"] != "COMPLETE_PENDING_EXTERNAL_INDEPENDENT_QC"
            or report["source_head"] != expected_source_head
            or report["days"] != TOTAL_DAYS[period]
            or report["symbols"] != list(SYMBOLS)
            or set(report["cases"]) != EXPECT
            or report["notional_per_symbol_usdt"] != "10"
            or report["fees"] != {"taker": str(TAKER), "maker_resting_tp": str(MAKER)}
            or not report["post_holdout_replicative_not_pristine_blinded_oos"]):
        raise ValueError("research version/model/period contract mismatch")
    for rel, digest in report["pinned_code_sha256"].items():
        if sha(SOURCE / rel) != digest:
            raise ValueError("code provenance SHA changed: " + rel)
    if sha(ledger_file) != report["ledger_sha256"]:
        raise ValueError("immutable final ledger SHA drift")
    last_digest = ""
    last = None
    for day in range(1, TOTAL_DAYS[period] + 1):
        p = root / "checkpoints" / ("day_%03d.json.gz" % day)
        if not p.is_file() or p.is_symlink():
            raise ValueError("missing/unsafe exact-day checkpoint")
        packed = p.read_bytes()
        item = json.loads(gzip.decompress(packed))
        if (item.get("research") != RESEARCH or item.get("period") != period
                or item.get("days_done") != day
                or item.get("pins") != report["pinned_code_sha256"]
                or item.get("previous_checkpoint_sha256") != last_digest
                or set(item.get("books", {})) != EXPECT):
            raise ValueError("independent checkpoint/source SHA chain failure")
        last_digest = hashlib.sha256(packed).hexdigest()
        last = item
    if last_digest != report["checkpoint_last_sha256"]:
        raise ValueError("final checkpoint digest does not match results")
    if last is None or last["source_events_seen"] != report["source_events_seen"]:
        raise ValueError("original Entry source count checkpoint mismatch")
    _, ids = source_cohorts(period, report)
    all_rows = {name: [] for name in EXPECT}
    with ledger_file.open() as stream:
        for line in stream:
            rec = json.loads(line)
            name = rec.pop("replication_scenario")
            if name not in all_rows:
                raise ValueError("unknown ledger scenario")
            all_rows[name].append(rec)
    reference = {s: [] for s in SYMBOLS}
    for row in all_rows["REFERENCE"]:
        reference[row["symbol"]].append(row)
    if reference != expected_reference(period):
        raise ValueError("original full-universe baseline trade-by-trade parity FAILED")
    audited = {}
    for name in sorted(EXPECT):
        rows = all_rows[name]
        scenario = report["cases"][name]
        if rows != last["books"][name]["trades"]:
            raise ValueError("checkpoint final ledger bytes diverge from result")
        if len(rows) != scenario["closed_trades"]:
            raise ValueError("scenario closed trade count mismatch")
        net = D(0)
        original_maker_savings = D(0)
        gross_sum = fee_in_sum = fee_out_sum = D(0)
        hardstops = 0
        by_coin = defaultdict(lambda: {"closed": 0, "hardstops": 0, "net": D(0)})
        seen_ids = set()
        for row in rows:
            gross, entry_fee, exit_fee, trade_net = net_components(row)
            if (D(row["gross_usdt"]) != gross
                    or D(row["entry_fee_usdt"]) != entry_fee
                    or D(row["exit_fee_usdt"]) != exit_fee
                    or D(row["net_usdt"]) != trade_net):
                raise ValueError("independent per-trade fee/net mismatch " + name)
            if row["symbol"] not in SYMBOLS:
                raise ValueError("foreign symbol in trade ledger")
            if int(row["exit_source_order"]) <= int(row["entry_source_order"]):
                raise ValueError("future/zero ordering Exit violation")
            if name != "REFERENCE":
                side = "LONG_ONLY" if "_LONG_ONLY_" in name else "SHORT_ONLY"
                if row["side"] + "_ONLY" != side:
                    raise ValueError("direction contamination")
                eligible = ids["w" + name[1:4] + "_" + side]
                if row["candidate_event_id"] not in eligible:
                    raise ValueError("closed trade violates strict depth/width")
                if row["candidate_event_id"] in seen_ids:
                    raise ValueError("duplicate Entry within scenario")
                seen_ids.add(row["candidate_event_id"])
            gross_sum += gross
            fee_in_sum += entry_fee
            fee_out_sum += exit_fee
            net += trade_net
            hardstops += row["exit_reason"] == "HARD_STOP"
            sr = by_coin[row["symbol"]]
            sr["closed"] += 1
            sr["hardstops"] += row["exit_reason"] == "HARD_STOP"
            sr["net"] += trade_net
            if row["maker_exit"]:
                original_maker_savings += (
                    NOTIONAL * D(row["exit_price"]) / D(row["entry_price"])
                    * (TAKER - MAKER)
                ).quantize(Q, rounding=ROUND_HALF_EVEN)
        if (net != D(scenario["net_after_modeled_fees_usdt"])
                or net != D(scenario["money"]["net_usdt"])
                or gross_sum != D(scenario["money"]["gross_usdt"])
                or fee_in_sum != D(scenario["money"]["entry_fees_usdt"])
                or fee_out_sum != D(scenario["money"]["exit_fees_usdt"])
                or net - original_maker_savings
                    != D(scenario["net_taker_taker_sensitivity_usdt"])
                or hardstops != scenario["counts"].get("close_HARD_STOP", 0)
                or len(rows) + len(scenario["censored"])
                    != scenario["counts"]["accepted"]):
            raise ValueError("independent portfolio reconciliation mismatch " + name)
        for symbol in SYMBOLS:
            observed = scenario["by_coin"][symbol]
            m = by_coin[symbol]
            if (observed["closed"] != m["closed"]
                    or observed["hardstops"] != m["hardstops"]
                    or D(observed["net_after_modeled_fees_usdt"]) != m["net"]):
                raise ValueError("symbol-level independent net/stop mismatch")
        if name != "REFERENCE":
            side = "LONG_ONLY" if "_LONG_ONLY_" in name else "SHORT_ONLY"
            if scenario["counts"]["qualified_candidate"] != report["cohort_candidate_counts"][name[1:4]][side]:
                raise ValueError("candidate count drift vs source cohort")
            eligible = ids["w" + name[1:4] + "_" + side]
            for censored in scenario["censored"]:
                if censored["entry_event_id"] not in eligible:
                    raise ValueError("censored trade outside exact cohort")
                if censored["entry_event_id"] in seen_ids:
                    raise ValueError("duplicate closed/censored Entry ID")
                seen_ids.add(censored["entry_event_id"])
        audited[name] = {
            "accepted": scenario["counts"]["accepted"],
            "closed": len(rows), "censored": len(scenario["censored"]),
            "hardstops": hardstops,
            "net_after_modeled_fees_usdt": str(net),
            "taker_taker_net_usdt": str(net - original_maker_savings),
        }
    qc = {
        "status": "INDEPENDENT_QC_PASS_P90L90_DEPTHZERO_PREV7D_2026_REPLICATION_V1",
        "research": RESEARCH, "period": period,
        "source_head": expected_source_head,
        "audit_source_sha256": sha(SOURCE / SELF_REL),
        "result_sha256": sha(result_file),
        "ledger_sha256": sha(ledger_file),
        "checkpoint_last_sha256": last_digest,
        "days": TOTAL_DAYS[period],
        "per_scenario": audited,
        "original_reference_trade_by_trade_parity": True,
        "candidate_width_depth_parity": True,
        "independent_native_source_sha_reference": True,
        "model_lacks_slippage_funding_real_fills": True,
        "not_a_blinded_oos": True,
    }
    target = root / "qc.json"
    if target.exists() or target.is_symlink():
        raise FileExistsError("independent QC result already exists, no overwrite")
    with target.open("x", encoding="utf-8") as file:
        json.dump(qc, file, sort_keys=True, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    status = root / "status.json"
    temp = root / "status.json.tmp"
    if temp.exists() or temp.is_symlink():
        raise FileExistsError("status transition race")
    with temp.open("x") as f:
        json.dump({
            "stage": "COMPLETED_INDEPENDENT_QC_PASS",
            "period": period, "source_head": expected_source_head,
            "days_done": TOTAL_DAYS[period],
            "days_total": TOTAL_DAYS[period],
            "result_sha256": qc["result_sha256"],
            "ledger_sha256": qc["ledger_sha256"],
            "qc_sha256": sha(target),
        }, f, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    temp.replace(status)
    return {
        "period": period, "status": qc["status"],
        "result_sha256": qc["result_sha256"],
        "qc_sha256": sha(target),
        "primary": {
            name: audited[name]
            for name in ("w080_SHORT_ONLY_SECOND48", "w090_SHORT_ONLY_SECOND48")
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--period", choices=("90d", "29d", "both"), required=True)
    args = parser.parse_args()
    chosen = ("29d", "90d") if args.period == "both" else (args.period,)
    for period in chosen:
        print(json.dumps(audit(period, args.source_head), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
