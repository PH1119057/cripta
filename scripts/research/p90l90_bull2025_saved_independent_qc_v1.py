#!/usr/bin/env python3
"""Independent post-publication 2025 LONG portfolio audit; no native tick replay.

Verifies all 16 saved 183-day scenarios against the immutable checkpoint,
recomputed trade+fee+wallet arithmetic, source SHA and raw finalizer evidence.
Publishes an independently versioned report only when every exact invariant
passes. Does not use or call the finalization script or alter its results.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import sys
from decimal import Decimal as D
from pathlib import Path

SOURCE = Path("/srv/cripta/source_checkout")
sys.path.insert(0, str(SOURCE))
from scripts.research import p90l90_bull2025_portfolio_chunked_v2 as ch  # noqa: E402

old = ch.old
SELF_REL = "scripts/research/p90l90_bull2025_saved_independent_qc_v1.py"
FINALIZER_REL = "scripts/research/p90l90_bull2025_finalize_saved_v1.py"
RESEARCH = "P90L90_BULL2025_SAVED_INDEPENDENT_QC_V1"
FINAL_RESEARCH = "P90L90_BULL2025_SAVED_FINAL_ONLY_V1"
QC_PARENT = old.RESULTS / "portfolio_independent_qc_2025_v1"
SIDE = "LONG_ONLY"


def strict_case(key: str, saved: dict, result: dict,
                rows: list[dict], dust: dict,
                last_prices: dict) -> dict:
    """Arithmetically independent of Book.finish or finalizer implementation."""
    gross = sum((D(t["gross_usdt"]) for t in rows), D(0))
    entry = sum((D(t["entry_fee_usdt"]) for t in rows), D(0))
    exit_fee = sum((D(t["exit_fee_usdt"]) for t in rows), D(0))
    net = sum((D(t["net_usdt"]) for t in rows), D(0))
    for row in rows:
        if row["side"] != "LONG":
            raise ValueError("non-LONG trade in final 2025 ledger: " + key)
        if (
            D(row["gross_usdt"]) - D(row["entry_fee_usdt"])
            - D(row["exit_fee_usdt"]) != D(row["net_usdt"])
        ):
            raise ValueError("invalid gross/fee/net single row: " + key)
    m = saved["m"]
    original_reserve = saved["reserved"]
    expected_reserve = sum(saved["margin"].values(), D(0))
    gap = original_reserve - expected_reserve
    if (
        not abs(gap) < old.Q
        or D(dust["original_reserve_usdt"]) != original_reserve
        or D(dust["sum_open_margins_usdt"]) != expected_reserve
        or D(dust["reserve_roundoff_usdt"]) != gap
        or dust["in_memory_adjusted"] != (gap != 0)
        or not dust["wallet_and_ledger_unchanged"]
    ):
        raise ValueError("final 2025 reserve roundoff evidence mismatch: " + key)
    open_symbols = {s for s, p in saved["pos"].items() if p is not None}
    open_records = {r["symbol"]: r for r in result["open_censored"]}
    if len(open_records) != len(result["open_censored"]) or set(open_records) != open_symbols:
        raise ValueError("censored/open symbol mismatch: " + key)
    floating = D(0)
    for symbol in open_symbols:
        r = open_records[symbol]
        position = saved["pos"][symbol]
        px = D(r["last_price"])
        if (
            D(r["margin"]) != saved["margin"][symbol]
            or D(r["entry_fee_already_debited"]) != saved["open_fee"][symbol]
            or D(r["entry_price"]) != position.entry
            or px != last_prices[symbol]
            or r["entry_ts"] != repr(position.entry_ts)
            or r["side"] != "LONG"
        ):
            raise ValueError("censored position identity mismatch: " + key)
        notional = saved["margin"][symbol] * old.LEVERAGE
        mark = old.money(notional * (px / position.entry - 1))
        if (
            D(r["notional"]) != notional
            or D(r["unrealized_gross_usdt"]) != mark
        ):
            raise ValueError("open mark/unrealized mismatch: " + key)
        floating += mark
    wallet = saved["wallet"]
    all_entry = m.get("all_entry_fee_usdt", D(0))
    if not all([
        gross == m.get("gross_usdt", D(0)),
        entry == m.get("closed_entry_fee_usdt", D(0)),
        exit_fee == m.get("exit_fee_usdt", D(0)),
        net == m.get("closed_net_usdt", D(0)),
        gross - entry - exit_fee == net,
        m.get("positive_closed_net_usdt", D(0))
        + m.get("negative_closed_net_usdt", D(0)) == net,
        all_entry == entry + sum(saved["open_fee"].values(), D(0)),
        wallet - old.START_WALLET == gross - all_entry - exit_fee,
        len(rows) == saved["counts"]["closed"] == result["ledger_rows"],
        saved["counts"]["accepted"] == len(rows) + len(open_symbols),
        saved["counts"]["qualified_candidate"] == (
            saved["counts"]["accepted"]
            + saved["counts"]["busy_or_same_tick"]
            + saved["counts"]["paused_candidate"]
            + saved["counts"]["capital_blocked_candidate"]
        ),
        D(result["wallet_usdt"]) == wallet,
        D(result["wallet_change_includes_open_entry_fees_usdt"])
        == wallet - old.START_WALLET,
        D(result["closed_net_after_fees_usdt"]) == net,
        D(result["reserved_margin_usdt"]) == expected_reserve,
        D(result["unrealized_open_gross_usdt"]) == floating,
        D(result["equity_mark_to_last_native_price_gross_usdt"])
        == wallet + floating,
        len(result["open_censored"]) == len(open_symbols),
        result["start_wallet_usdt"] == str(old.START_WALLET),
        result["cash_model"] in old.CASH_MODES,
    ]):
        raise ValueError("independent cash/fees/coverage QA failure: " + key)
    return {
        "scenario": result["scenario"],
        "cash_mode": result["cash_model"],
        "closed_trades": len(rows),
        "censored_open_positions": len(open_symbols),
        "wallet_end_usdt_after_closed_trade_fees": str(wallet),
        "wallet_change_after_fees_usdt": str(wallet - old.START_WALLET),
        "closed_net_after_fees_usdt": str(net),
        "total_entry_fees_usdt": str(all_entry),
        "exit_fees_usdt": str(exit_fee),
        "unrealized_open_gross_usdt": str(floating),
        "equity_mark_gross_usdt": str(wallet + floating),
        "max_eod_sampled_drawdown_pct": result["daily_mark_drawdown_pct"],
        "reserve_dust_usdt": str(gap),
    }


def validate(source_head: str) -> tuple[dict, str]:
    if os.geteuid() != 107 or os.getegid() != 109:
        raise PermissionError("2025 independent QC requires cripta UID107:GID109")
    if old.entry.git_head() != source_head:
        raise RuntimeError("2025 independent QC source commit mismatch")
    result_dir = ch.FINAL / SIDE.lower()
    if result_dir.is_symlink() or not result_dir.is_dir():
        raise ValueError("2025 internal full result not published")
    report_path = result_dir / "result.json"
    qc_path = result_dir / "qc.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    qc = json.loads(qc_path.read_text(encoding="utf-8"))
    state, manifest, digest = ch.load_parts(ch.PARTS / SIDE.lower())
    if state is None or manifest is None:
        raise ValueError("2025 checkpoint chain absent")
    ch.pin_current(manifest["source_pin_sha256"], manifest["source_head_first"])
    expected_keys = {
        f"{scenario}_{mode}" for scenario in old.SCENARIOS
        for mode in old.CASH_MODES
    }
    if not all([
        report["research"] == FINAL_RESEARCH,
        report["status"] == "INTERNAL_QC_PASS_PENDING_INDEPENDENT",
        qc["status"] == "INTERNAL_QC_PASS_PENDING_INDEPENDENT",
        qc["research"] == FINAL_RESEARCH,
        report["side"] == SIDE == qc["side"],
        state["next_day"] == ch.TOTAL_DAYS == report["checkpoint_days"],
        report["checkpoint_last_manifest_sha256"] == digest
        == qc["checkpoint_last_manifest_sha256"],
        report["native_ticks_evaluation"] == state["ticks"],
        report["source_rows_including_warmup"] == 204446371,
        report["source_input_days"] == 193,
        report["source_pins_sha256"] == {
            **manifest["source_pin_sha256"],
            FINALIZER_REL: old.sha(SOURCE / FINALIZER_REL),
        },
        qc["result_sha256"] == old.sha(report_path),
        qc["reserve_roundoff_evidence_sha256"]
        == old.sha(result_dir / "reserve_roundoff_evidence.json"),
        report["reserve_roundoff_evidence_sha256"]
        == qc["reserve_roundoff_evidence_sha256"],
        report["unlimited_no_pause_per_coin_shadow_parity"] is True,
        qc["checkpoint_unchanged_after_finish"] is True,
        report["finalization_method"]["original_book_finish_unchanged"] is True,
        set(report["results"]) == expected_keys,
        set(report["ledger_sha256"]) == expected_keys,
        set(report["reserve_roundoff_evidence"]) == expected_keys,
        len(state["books"]) == len(expected_keys) == 16,
    ]):
        raise ValueError("independent 2025 input/provenance/final report QC mismatch")
    snap = result_dir / "source_snapshot"
    if snap.is_symlink() or not snap.is_dir():
        raise ValueError("original 2025 source snapshots absent")
    for rel, digest_expected in report["source_pins_sha256"].items():
        snapshot = snap / Path(rel).name
        if (
            old.sha(snapshot) != digest_expected
            or old.sha(SOURCE / rel) != digest_expected
        ):
            raise ValueError("2025 frozen research source bytes changed: " + rel)
    evidence = json.loads(
        (result_dir / "reserve_roundoff_evidence.json").read_text(encoding="utf-8")
    )
    if evidence != report["reserve_roundoff_evidence"]:
        raise ValueError("research rounding evidence file/body mismatch")
    cases = {}
    for scenario in old.SCENARIOS:
        for mode in old.CASH_MODES:
            public = f"{scenario}_{mode}"
            internal = ch.key(scenario, mode)
            ledger = result_dir / f"ledger_{public}.jsonl"
            if (
                old.sha(ledger) != report["ledger_sha256"][public]
                or old.sha(ledger) != qc["ledger_sha256"][public]
            ):
                raise ValueError("published ledger SHA mismatch " + public)
            rows = [
                json.loads(line)
                for line in ledger.read_text(encoding="utf-8").splitlines()
                if line
            ]
            if rows != state["books"][internal]["trades"]:
                raise ValueError("published ledger differs from immutable day183 " + public)
            if (report["results"][public]["scenario"] != scenario
                    or report["results"][public]["cash_model"] != mode):
                raise ValueError("scenario/cash publication identity mismatch")
            cases[public] = strict_case(
                public, state["books"][internal],
                report["results"][public], rows, evidence[public],
                state["prices"],
            )
    details = {
        "research": RESEARCH,
        "status": "INDEPENDENT_QC_PASS_SAVED_2025_LONG_16_CASES",
        "side": SIDE,
        "source_head_at_qc": source_head,
        "checkpoint_days": state["next_day"],
        "checkpoint_last_manifest_sha256": digest,
        "native_ticks_evaluation": state["ticks"],
        "source_rows_including_warmup": 204446371,
        "final_report_sha256": old.sha(report_path),
        "final_internal_qc_sha256": old.sha(qc_path),
        "independent_qc_source_sha256": old.sha(SOURCE / SELF_REL),
        "cases_verified": len(cases),
        "all_16_original_day183_ledgers_identical": True,
        "all_16_wallet_fees_capital_after_commissions_exact": True,
        "all_16_open_position_mark_to_price_verified": True,
        "all_16_reserved_margin_gaps_below_1e_minus_12_usdt": True,
        "no_frozen_entry_exit_or_day_checkpoint_mutation": True,
        "live_equivalence": "NOT_TESTED",
        "financing_slippage_spread_execution": "NOT_MODELED",
        "cases": cases,
    }
    return details, old.sha(report_path)


def publish(source_head: str) -> dict:
    result, before = validate(source_head)
    if shutil.disk_usage(ch.ROOT).free < 16 * 2**30:
        raise RuntimeError("independent QC data disk below 16GiB reserve")
    if QC_PARENT.is_symlink():
        raise PermissionError("independent QC output root must not be symlink")
    dest = QC_PARENT / SIDE.lower()
    if dest.exists() or dest.is_symlink():
        raise FileExistsError("independent full 2025 QC already published")
    ch.writer_dir(QC_PARENT)
    buffer = io.StringIO(newline="")
    columns = [
        "scenario", "cash_mode", "closed_trades", "censored_open_positions",
        "wallet_end_usdt_after_closed_trade_fees",
        "wallet_change_after_fees_usdt",
        "closed_net_after_fees_usdt",
        "total_entry_fees_usdt", "exit_fees_usdt",
        "unrealized_open_gross_usdt", "equity_mark_gross_usdt",
        "max_eod_sampled_drawdown_pct", "reserve_dust_usdt",
    ]
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for result_case in result["cases"].values():
        writer.writerow(result_case)
    payloads = {
        "qc_independent.json": json.dumps(result, indent=2, sort_keys=True) + "\n",
        "financial_summary.csv": buffer.getvalue(),
        "financial_summary.json": json.dumps(
            {"status": result["status"], "cases": result["cases"],
             "economic_caveats": [
                 "wallet change includes entry fees already charged for open positions",
                 "equity uses gross unrealized mark, excludes eventual exit fees",
                 "funding, spread, slippage and real fills not modeled",
             ]}, sort_keys=True, indent=2,
        ) + "\n",
        "source_snapshot_independent_qc.py": (
            (SOURCE / SELF_REL).read_text(encoding="utf-8")
        ),
    }
    ch.commit_bundle(QC_PARENT, SIDE.lower(), payloads)
    after = old.sha(ch.FINAL / SIDE.lower() / "result.json")
    if after != before:
        raise RuntimeError("published 2025 portfolio result changed during independent QC")
    return {
        "status": result["status"],
        "cases_verified": result["cases_verified"],
        "checkpoint_days": result["checkpoint_days"],
        "native_ticks_evaluation": result["native_ticks_evaluation"],
        "output": str(dest),
        "internal_result_sha256": result["final_report_sha256"],
        "independent_qc_sha256": old.sha(dest / "qc_independent.json"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-head", required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.source_head), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
