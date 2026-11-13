#!/usr/bin/env python3
"""Independent read-only-first financial/lineage QC for #638 unified P90L90.

Writer: original research cripta UID107:GID109. READ frozen native candidate
sources/checkpoints and first-party result.json; WRITE only new immutable
qc.json plus atomic status.json transition in the same new data-disk result
child. Never modify source, existing evidence, DB, Exchange or trading.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

from scripts.research import p90l90_unified_depth_three_period_v1 as run

STATUS = "INDEPENDENT_QC_PASS_P90L90_UNIFIED_DEPTH_THREE_PERIODS_V2"


def money(val: D) -> D:
    return run.old.money(val)


def trade_calculation(row: dict) -> tuple[D, D, D, D]:
    entry, px = D(row["entry_price"]), D(row["exit_price"])
    margin, nominal = D(row["margin_usdt"]), D(row["notional_usdt"])
    if (entry <= 0 or px <= 0 or margin <= 0 or nominal != 5 * margin
            or row["side"] not in ("LONG", "SHORT")):
        raise ValueError("native trade price/margin/leverage/side invalid")
    if bool(row["maker_exit"]) != (row["exit_reason"] == "RESTING_TP"):
        raise ValueError("maker classification not supported by native TP")
    gross = money(nominal * (px / entry - 1) * (
        D(1) if row["side"] == "LONG" else D(-1)))
    entry_fee = money(nominal * D("0.00055"))
    exit_fee = money(nominal * px / entry * (
        D("0.0002") if row["maker_exit"] else D("0.00055")))
    return gross, entry_fee, exit_fee, gross - entry_fee - exit_fee


def candidates(period: str) -> dict:
    """Recompute every accepted/censored Entry eligibility from frozen native Entry."""
    root = Path(run.WINDOW[period]["root"]) / "results"
    found = {case: set() for case in ("ZERO", "ORIGINAL_0TO15")}
    stats = Counter()
    for symbol in run.SYMBOLS:
        if period == "183d":
            folder = root / "entry_full_2025_v1" / symbol
        else:
            folder = root / ("entry_" + symbol.removesuffix("USDT")
                             + ("_FULL90" if period == "90d" else "_SEP29"))
        manifest = json.loads((folder / "manifest.json").read_text())
        file = folder / ("candidates_" + symbol + ".jsonl")
        if run.sha(file) != manifest["events_sha256"]:
            raise ValueError("independent original Entry source SHA mismatch")
        med7 = {k: D(str(v)) for k, v in manifest["medians_PREV7D"].items()}
        for ln in file.open():
            e = json.loads(ln)
            day = datetime.fromtimestamp(int(e["state_effective_ts"]), UTC).date().isoformat()
            diff = abs(D(e["width_pct"]) - med7[day])
            if bool(e["PREV7D"]) != (diff <= D("0.50")):
                raise ValueError("independent PREV7D source label mismatch")
            if e["PREV7D"]:
                depth = D(e["depth_pct"])
                if not D(0) <= depth <= D(15):
                    raise ValueError("outside original physical H9 depth")
                for scenario in found:
                    if scenario == "ORIGINAL_0TO15" or depth == 0:
                        if e["event_id"] in found[scenario]:
                            raise ValueError("duplicate Entry event identity")
                        found[scenario].add(e["event_id"])
                        stats[scenario] += 1
    return {"eligible": found, "candidate_count": dict(stats)}


def verify_capital_denials(counts: dict, expected: int) -> None:
    """A missing Counter key represents zero, never an unverified nonzero."""
    if counts.get("capital_blocked_candidate", 0) != expected:
        raise ValueError("capital denials not reconciled")


def audit(period: str, root: Path, commit: str, *,
          dry_run: bool = False, auditor_head: str | None = None) -> dict:
    if period not in run.WINDOW:
        raise ValueError("QC invalid period")
    if (os.geteuid(), os.getegid()) != (107, 109):
        raise PermissionError("independent QC requires exact research actor cripta")
    if root != run.output(period) or not root.is_dir() or root.is_symlink():
        raise ValueError("noncanonical research result path")
    manifest = json.loads((root / "manifest.json").read_text())
    result_path = root / "result.json"
    ledger_path = root / "trade_ledger.jsonl"
    result = json.loads(result_path.read_text())
    if (result["research"] != run.RESEARCH
            or result["period"] != period or result["source_head"] != commit
            or result["source_pins"] != manifest["source_pins"]
            or set(result["cases"]) != set(run.BOOK_NAMES)
            or result["case_count"] != 8
            or result["one_combined_portfolio"] is not True
            or result["one_slot_per_coin"] is not True
            or result["cash_model"] != {
                "initial_wallet_usdt": "1000", "fixed_margin_usdt": "200",
                "compound_fraction_of_current_wallet": "0.2",
                "leverage": "5", "taker_fee": "0.00055",
                "resting_maker_fee": "0.0002", "capital_gate": True,
            } or result["ledger_sha256"] != run.sha(ledger_path)):
        raise ValueError("source/financial/portfolio-version contract mismatch")
    for rel, digest in result["source_pins"].items():
        if run.sha(run.SOURCE / rel) != digest:
            raise ValueError("executed code bytes do not match frozen source")
        if run.sha(root / "source_snapshot" / Path(rel).name) != digest:
            raise ValueError("frozen source snapshot bytes drift")
    if result["reference_trade_by_trade_parity"] != {
        name: {"status": ("EXACT_2025_UNIT_TRADE_PARITY" if period == "183d"
                          else "EXACT_2026_COMBINED_TRADE_PARITY"),
               "trades": int(result["reference_trade_by_trade_parity"][name]["trades"])}
        for name in run.baseline_booknames(period)
    }:
        raise ValueError("original reference source parity missing")
    previous_digest = ""
    latest = None
    for day in range(1, run.WINDOW[period]["days"] + 1):
        f = root / "checkpoints" / ("day_%03d.json.gz" % day)
        if f.is_symlink() or not f.exists():
            raise ValueError("missing native day checkpoint or unsafe symlink")
        blob = f.read_bytes()
        saved = json.loads(__import__("gzip").decompress(blob))
        if (saved.get("research") != run.RESEARCH
                or saved.get("days_done") != day
                or saved.get("source_pins") != result["source_pins"]
                or saved.get("previous_checkpoint_sha256") != previous_digest
                or set(saved.get("books", {})) != set(run.BOOK_NAMES)
                or set(saved.get("reference_books", {}))
                    != set(run.baseline_booknames(period))):
            raise ValueError("independent daily native SHA chain mismatch")
        previous_digest = hashlib.sha256(blob).hexdigest()
        latest = saved
    if (latest is None or previous_digest != result["final_checkpoint_sha256"]
            or latest["source_seen"] != result["source_events_seen"]
            or latest["last_native_prices"] != result["last_native_prices"]):
        raise ValueError("final checkpoints/source day proof mismatch")
    source = candidates(period)
    reported_counts = Counter()
    for vals in manifest["candidate_counts"].values():
        reported_counts.update(vals)
    for depth in run.DEPTHS:
        if reported_counts[depth] != source["candidate_count"][depth]:
            raise ValueError("independently counted causal candidate source differs")
    rows = defaultdict(list)
    for line in ledger_path.open():
        item = json.loads(line)
        name = item.pop("research_case")
        if name not in result["cases"]:
            raise ValueError("unknown portfolio ledger case")
        rows[name].append(item)
    checked = {}
    for name in run.BOOK_NAMES:
        depth, cash, scenario = name.split("__")
        summary = result["cases"][name]
        frozen = latest["books"][name]
        trades = rows[name]
        if trades != frozen["trades"]:
            raise ValueError("final immutable checkpoint ledger differs " + name)
        if len(trades) != summary["counts"]["closed"]:
            raise ValueError("closed count mismatch " + name)
        total_gross = total_entry = total_exit = total_net = D(0)
        coin_net = defaultdict(lambda: D(0))
        closed_ids = set()
        for row in trades:
            e_id = row["candidate_event_id"]
            if e_id not in source["eligible"][depth] or e_id in closed_ids:
                raise ValueError("closed native Entry violates depth/width or duplicate")
            closed_ids.add(e_id)
            if row["symbol"] not in run.SYMBOLS or row["exit_source_order"] <= row["entry_source_order"]:
                raise ValueError("foreign coin or noncausal Exit")
            if cash == "fixed" and D(row["margin_usdt"]) != run.FIXED_MARGIN:
                raise ValueError("fixed margin not 200 USDT")
            g, ef, xf, net = trade_calculation(row)
            if (g, ef, xf, net) != tuple(D(row[key]) for key in (
                    "gross_usdt", "entry_fee_usdt", "exit_fee_usdt", "net_usdt")):
                raise ValueError("independent every-trade modeled fee/PnL mismatch")
            total_gross += g
            total_entry += ef
            total_exit += xf
            total_net += net
            coin_net[row["symbol"]] += net
        open_positions = summary["open_censored"]
        expected_open_fee = D(0)
        pending_ids = set()
        for open_row in open_positions:
            e_id = open_row["entry_event_id"]
            if e_id not in source["eligible"][depth] or e_id in closed_ids or e_id in pending_ids:
                raise ValueError("open censored Entry invalid under H9 depth")
            pending_ids.add(e_id)
            margin = D(open_row["margin"])
            if margin <= 0 or cash == "fixed" and margin != run.FIXED_MARGIN:
                raise ValueError("censored margin drift")
            if D(open_row["notional"]) != margin * run.LEVERAGE:
                raise ValueError("censored source notional/leverage mismatch")
            expected_open_fee += money(margin * run.LEVERAGE * D("0.00055"))
        all_entry_fee = total_entry + expected_open_fee
        reconstructed_wallet = run.START_WALLET + total_gross - all_entry_fee - total_exit
        if (total_net != D(summary["money"].get("closed_net_usdt", "0"))
                or total_entry != D(summary["money"].get("closed_entry_fee_usdt", "0"))
                or total_exit != D(summary["money"].get("exit_fee_usdt", "0"))
                or total_gross != D(summary["money"].get("gross_usdt", "0"))
                or all_entry_fee != D(summary["money"].get("all_entry_fee_usdt", "0"))
                or reconstructed_wallet != D(summary["wallet_usdt"])
                or reconstructed_wallet - run.START_WALLET != D(summary["wallet_net_usdt"])
                or len(trades) + len(open_positions) != summary["counts"]["accepted"]
                or summary["max_simultaneous_slots"] > 5):
            raise ValueError("independent constrained capital and PnL reconciliation mismatch")
        frozen_open = frozen["pos"]
        if ({s for s, rec in frozen_open.items() if rec is not None}
                != {x["symbol"] for x in open_positions}):
            raise ValueError("snapshot and open-censored coin occupancy differs")
        reserved = sum((D(x["margin"]) for x in open_positions), D(0))
        if abs(reserved - D(summary["reserved_margin_usdt"])) > run.RESERVED_MARGIN_EPSILON:
            raise ValueError("open position margin reservation mismatch")
        if summary["counts"]["qualified_candidate"] != source["candidate_count"][depth]:
            raise ValueError("qualified candidates different from immutable Entry catalog")
        verify_capital_denials(summary["counts"], summary["capital_blocked_candidate_count"])
        if summary["counts"].get("exit_HARD_STOP", 0) != summary["hardstop_count"]:
            raise ValueError("actual completed Hard Stops not reconciled")
        if int(summary["pause_trigger_count"]) != int(summary["counts"].get("second48_pause_triggers", 0)):
            raise ValueError("actual global pause trigger count drift")
        expected_unrealized = D(0)
        for row in open_positions:
            ratio = D(row["last_price"]) / D(row["entry_price"])
            floating = money(D(row["notional"]) * (ratio - 1) * (
                D(1) if row["side"] == "LONG" else D(-1)))
            if floating != D(row["unrealized_gross_usdt"]):
                raise ValueError("open unrealized price mark mismatch")
            expected_unrealized += floating
        # Book.mark() deliberately records unrounded equity during the daily
        # timeline; the final rounded open gross and daily equity mark differ
        # only by rounding. Validate values independently with tight epsilon.
        if (expected_unrealized != D(summary["open_unrealized_gross_usdt"])
                or abs(D(summary["equity_with_open_unrealized_gross_usdt"]) -
                       (reconstructed_wallet + sum((
                           D(row["notional"]) * (D(row["last_price"]) /
                           D(row["entry_price"]) - 1) *
                           (D(1) if row["side"] == "LONG" else D(-1))
                           for row in open_positions), D(0)))) > D("0.000000001")):
            raise ValueError("independent final equity including open mark mismatch")
        checked[name] = {
            "accepted": summary["counts"]["accepted"],
            "closed": len(trades),
            "hardstops": summary["hardstop_count"],
            "censored": len(open_positions),
            "capital_denials": summary["capital_blocked_candidate_count"],
            "max_positions": summary["max_simultaneous_slots"],
            "wallet_usdt": str(reconstructed_wallet),
            "wallet_net_after_all_modeled_fees_usdt": str(reconstructed_wallet - run.START_WALLET),
            "censored_entry_fee_usdt": str(expected_open_fee),
            "by_coin_closed_net_after_fees_usdt": {sym: str(coin_net[sym]) for sym in run.SYMBOLS},
        }
    qc = {
        "status": STATUS, "period": period, "source_head": commit,
        "source_pins": result["source_pins"],
        "result_sha256": run.sha(result_path), "ledger_sha256": run.sha(ledger_path),
        "last_checkpoint_sha256": previous_digest,
        "source_candidate_count_parity": True,
        "original_native_reference_trade_parity": True,
        "native_full_replay_own_cash_state": True,
        "full_8_case_matrix_verified": len(checked) == 8,
        "days": run.WINDOW[period]["days"],
        "cases": checked,
        "research_only_no_actual_exchange_fills": True,
        # The frozen replay pinned the ORIGINAL QC bytes. A separately reviewed
        # auditor may validate that immutable replay without changing its pins.
        "auditor_git_head": auditor_head,
        "auditor_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "producer_source_head": commit,
        "absent_counter_semantics": "missing capital_blocked_candidate means zero",
    }
    if dry_run:
        return {
            "status": "INDEPENDENT_QC_DRY_RUN_PASS",
            "period": period,
            "cases": len(checked),
            "result_sha256": qc["result_sha256"],
            "ledger_sha256": qc["ledger_sha256"],
            "auditor_git_head": auditor_head,
            "auditor_code_sha256": qc["auditor_code_sha256"],
        }
    target = root / "qc.json"
    if target.exists() or target.is_symlink():
        raise FileExistsError("independent immutable QC already exists")
    with target.open("x") as f:
        json.dump(qc, f, sort_keys=True, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    run.prev.atomic_status(root, {
        "stage": "COMPLETED_INDEPENDENT_QC_PASS", "period": period,
        "source_head": commit, "days_done": run.WINDOW[period]["days"],
        "days_total": run.WINDOW[period]["days"],
        "result_sha256": qc["result_sha256"], "ledger_sha256": qc["ledger_sha256"],
        "qc_sha256": run.sha(target),
    })
    return {"status": STATUS, "period": period, "cases": len(checked),
            "qc_sha256": run.sha(target)}
