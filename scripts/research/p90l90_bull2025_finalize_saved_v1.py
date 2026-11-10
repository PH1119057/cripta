#!/usr/bin/env python3
"""Finish 183 immutable P90L90 2025 LONG checkpoints, without native replay.

Original Entry/Exit and original portfolio_v1.Book.finish are NOT modified.
Only one terminal reserve-equality artifact from finite-precision Decimal
subtraction is reconciled on a newly reconstructed IN-MEMORY book if smaller
than original model's 1e-12 USDT money quantum; exact before/after evidence
is published. No trading policy or already committed checkpoint is changed.

This emits INTERNAL QC ONLY. A separate source-bound verifier must publish
INDEPENDENT QC before reporting full-period economic conclusions.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import sys
import tempfile
from decimal import Decimal as D
from pathlib import Path

SOURCE = Path("/srv/cripta/source_checkout")
sys.path.insert(0, str(SOURCE))
from scripts.research import p90l90_bull2025_portfolio_chunked_v2 as ch  # noqa: E402

old = ch.old
SELF_REL = "scripts/research/p90l90_bull2025_finalize_saved_v1.py"
RESEARCH = "P90L90_BULL2025_SAVED_FINAL_ONLY_V1"
SIDE = "LONG_ONLY"
PINNED = (*ch.PINNED, SELF_REL)
EXPECTED_ROWS_ALL = 204446371


def normalize_final_reserve(book) -> dict:
    """Correct only a provable sub-money-quantum terminal Decimal dust value."""
    raw = book.reserved
    actual = sum(book.margin.values(), D(0))
    gap = raw - actual
    if abs(gap) >= old.Q:
        raise ValueError("material reserved capital mismatch; finalization blocked")
    if book.wallet < D(0):
        raise ValueError("negative modeled wallet; do not publish")
    # Mutates only a reconstructed Book in memory, never saved day183.
    book.reserved = actual
    return {
        "scenario": book.scenario,
        "cash_mode": book.mode,
        "original_reserve_usdt": str(raw),
        "sum_open_margins_usdt": str(actual),
        "reserve_roundoff_usdt": str(gap),
        "absolute_roundoff_lt_original_money_quantum": True,
        "in_memory_adjusted": gap != 0,
        "wallet_and_ledger_unchanged": True,
    }


def validate_frozen_state(state: dict, manifest: dict, data: dict,
                          units: dict, source_head: str) -> None:
    if (
        state["next_day"] != ch.TOTAL_DAYS
        or manifest["index"] != ch.TOTAL_DAYS - 1
        or manifest["day"] != "2025-09-30"
        or manifest["side"] != SIDE
    ):
        raise ValueError("complete 2025 LONG checkpoint is absent")
    if len(state["books"]) != len(old.SCENARIOS) * len(old.CASH_MODES):
        raise ValueError("16 original portfolio cash cases missing")
    if sum(state["next_seq"].values()) != EXPECTED_ROWS_ALL:
        raise ValueError("193-day native Bybit source row count mismatch")
    expected_native = 0
    for symbol in old.SYMBOLS:
        inputs = data[symbol]["manifest"]["inputs"]
        if (
            state["next_seq"][symbol] != sum(int(x["rows"]) for x in inputs)
            or state["seen_events"][symbol] != data[symbol]["manifest"]["events"]
            or state["shadow_rows"][symbol] != units[symbol]["rows"]
            or manifest["unit_ledger_sha256"][symbol] != units[symbol]["sha"]
        ):
            raise ValueError("full 2025 UNIT shadow/source parity mismatch " + symbol)
        expected_native += sum(int(x["rows"]) for x in inputs[10:])
    if state["ticks"] != expected_native:
        raise ValueError("full five-coin native tick count mismatch")
    ch.pin_current(manifest["source_pin_sha256"], manifest["source_head_first"])
    if old.entry.git_head() != source_head:
        raise RuntimeError("source checkout drift during independent 2025 finalize")


def source_pins(checkpoint_pins: dict) -> dict:
    if ch.old.sha(SOURCE / SELF_REL) == "":
        raise RuntimeError("missing versioned source of final-only reader")
    return {
        **checkpoint_pins,
        SELF_REL: old.sha(SOURCE / SELF_REL),
    }


def no_other_work() -> None:
    """Flock below is primary writer control; also block active controller."""
    for path in Path("/proc").iterdir():
        if not path.name.isdecimal() or int(path.name) == os.getpid():
            continue
        try:
            cmd = (path / "cmdline").read_bytes().replace(b"\0", b" ")
        except OSError:
            continue
        if b"python3 -c" in cmd:
            continue
        if (
            b"p90l90_bull2025_long_autonomous_v1.py" in cmd
            or b"p90l90_bull2025_portfolio_chunked_v2.py" in cmd
            or b"p90l90_bull2025_portfolio_v1.py" in cmd
        ):
            raise RuntimeError("another P90L90 2025 portfolio writer is active")


def prepare(source_head: str):
    if os.geteuid() != 107 or os.getegid() != 109:
        raise PermissionError("final saved research must run as cripta UID107:GID109")
    if old.entry.git_head() != source_head:
        raise RuntimeError("exact current GitHub main/source HEAD required")
    if ch.FINAL.is_symlink() or (ch.FINAL / SIDE.lower()).exists():
        raise FileExistsError("already published or symlink full LONG output")
    if shutil.disk_usage(ch.ROOT).free < 16 * 2**30:
        raise RuntimeError("data disk below 16 GiB research reserve")
    no_other_work()
    data, units, _ = old.verify_and_load(SIDE, source_head)
    folder = ch.PARTS / SIDE.lower()
    # Also re-check all 183 immutable day hashes and daily source manifest
    # provenance in one pass; never recalculate original native ticks.
    state, manifest, digest = ch.load_parts(folder, data)
    if state is None or manifest is None or not digest:
        raise ValueError("2025 fully saved checkpoint chain absent")
    validate_frozen_state(state, manifest, data, units, source_head)
    pins = source_pins(manifest["source_pin_sha256"])
    return state, manifest, digest, data, units, pins


def build_report(state: dict, manifest: dict, last_sha: str,
                 units: dict, source_head: str,
                 all_pins: dict) -> tuple[dict, dict, dict]:
    schedule = old.shadow_schedule(units, SIDE)
    books = ch.new_books(SIDE, schedule)
    ch.restore_books(books, state["books"])
    results = {}
    roundoff = {}
    ledger_texts = {}
    for k, book in books.items():
        public_key = f"{book.scenario}_{book.mode}"
        if k != ch.key(book.scenario, book.mode):
            raise ValueError("full-period scenario/cash key changed")
        evidence = normalize_final_reserve(book)
        result = book.finish(state["prices"], old.END_TS)
        if (
            D(result["wallet_usdt"]) != state["books"][k]["wallet"]
            or D(result["closed_net_after_fees_usdt"])
            != state["books"][k]["m"]["closed_net_usdt"]
            or D(result["reserved_margin_usdt"])
            != sum(state["books"][k]["margin"].values(), D(0))
        ):
            raise ValueError("model wallet/fees or reserved margin drift at finish")
        rows = state["books"][k]["trades"]
        if len(rows) != result["ledger_rows"] or rows != book.trades:
            raise ValueError("original 2025 trade ledger changed in finalization")
        results[public_key] = result
        roundoff[public_key] = evidence
        ledger_texts[public_key] = "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        )
    source_sha = {Path(k).name: v for k, v in all_pins.items()}
    if len(source_sha) != len(all_pins):
        raise ValueError("source snapshot basename collision")
    report = {
        "research": RESEARCH,
        "status": "INTERNAL_QC_PASS_PENDING_INDEPENDENT",
        "side": SIDE,
        "evaluation_utc": [old.EVAL_START.isoformat(), old.EVAL_END.isoformat()],
        "evaluation_days": ch.TOTAL_DAYS,
        "source_input_days": 193,
        "source_rows_including_warmup": EXPECTED_ROWS_ALL,
        "native_ticks_evaluation": state["ticks"],
        "symbols": list(old.SYMBOLS),
        "scenarios": list(old.SCENARIOS),
        "cash_modes": list(old.CASH_MODES),
        "start_wallet_each_case_usdt": str(old.START_WALLET),
        "fixed_margin_usdt": str(old.FIXED_MARGIN),
        "leverage": str(old.LEVERAGE),
        "fee_taker": str(old.TAKER),
        "fee_maker_resting_tp": str(old.MAKER),
        "source_head_first": manifest["source_head_first"],
        "source_head_at_finalization": source_head,
        "source_fastforward_only": True,
        "source_pins_sha256": all_pins,
        "source_snapshot_sha256": source_sha,
        "unit_shadow_sha256": {symbol: units[symbol]["sha"] for symbol in old.SYMBOLS},
        "unlimited_no_pause_per_coin_shadow_parity": True,
        "checkpoint_root": str(ch.PARTS / SIDE.lower()),
        "checkpoint_days": ch.TOTAL_DAYS,
        "checkpoint_last_manifest_sha256": last_sha,
        "finalization_method": {
            "name": "terminal_in_memory_reserve_subquantum_canonicalization_only",
            "money_quantum_usdt": str(old.Q),
            "original_book_finish_unchanged": True,
            "all_183_saved_checkpoint_bytes_untouched": True,
            "entry_exit_cash_and_trade_events_unchanged": True,
            "original_v2_full_result_unpublished_before_this_run": True,
        },
        "W7": {
            "thresholds_bps": list(old.THRESHOLDS),
            "min_prior_complete_7d_closes": 20,
            "pause_hours": 24,
            "trigger_only_new_no_pause_shadow_hardstop": True,
            "number_signals_by_threshold": {
                str(bps): len(signals) for bps, signals in schedule.items()
            },
        },
        "SECOND48": {
            "trigger": ">=2 actual scenario hardstops in rolling prior 48h",
            "embargo": "new entries only 48h; no forced flatten",
        },
        "censored_not_forced_closed": True,
        "daily_drawdown_is_eod_sampled_not_intraday": True,
        "unmodeled": [
            "funding", "bid_ask_spread", "slippage", "latency",
            "tick_size_and_lot_size_rounding", "exchange_liquidation",
            "orderbook_queue", "actual_venue_order_fills",
        ],
        "results": results,
        "reserve_roundoff_evidence": roundoff,
    }
    return report, ledger_texts, roundoff


def publish(source_head: str) -> dict:
    state, manifest, last_sha, _data, units, pins = prepare(source_head)
    folder = ch.PARTS / SIDE.lower()
    lock_path = folder / ".writer.lock"
    with lock_path.open("a+", encoding="utf-8") as locked:
        flock = importlib.import_module("fcntl")
        try:
            flock.flock(locked.fileno(), flock.LOCK_EX | flock.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("concurrent 2025 day-checkpoint writer") from exc
        # Re-load under same lock to prevent TOCTOU checkpoint drift.
        state2, manifest2, same_sha = ch.load_parts(folder)
        if same_sha != last_sha or state2["next_day"] != state["next_day"]:
            raise RuntimeError("2025 checkpoint changed after preflight")
        if manifest2["source_pin_sha256"] != manifest["source_pin_sha256"]:
            raise RuntimeError("checkpoint research source pin changed")
        report, ledgers, evidence = build_report(
            state, manifest, last_sha, units, source_head, pins,
        )
        ch.pin_current(manifest["source_pin_sha256"], manifest["source_head_first"])
        if old.sha(SOURCE / SELF_REL) != pins[SELF_REL]:
            raise RuntimeError("2025 final-only source changed")
        if ch.FINAL.is_symlink() or (ch.FINAL / SIDE.lower()).exists():
            raise FileExistsError("immutable portfolio result already exists")
        ch.writer_dir(ch.FINAL)
        tmp = Path(tempfile.mkdtemp(prefix=".pending_", dir=str(ch.FINAL)))
        hashes = {}
        for k, text in ledgers.items():
            filename = f"ledger_{k}.jsonl"
            ch.write_sync(tmp / filename, text)
            hashes[k] = old.sha(tmp / filename)
        report["ledger_sha256"] = hashes
        evidence_text = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
        ch.write_sync(tmp / "reserve_roundoff_evidence.json", evidence_text)
        report["reserve_roundoff_evidence_sha256"] = old.sha(
            tmp / "reserve_roundoff_evidence.json"
        )
        snap = tmp / "source_snapshot"
        snap.mkdir(mode=0o2770)
        for rel, expected in pins.items():
            original = SOURCE / rel
            target = snap / Path(rel).name
            shutil.copyfile(original, target)
            if old.sha(target) != expected or old.sha(original) != expected:
                raise RuntimeError("source snapshot byte identity at publish failed")
        ch.fsync_dir(snap)
        report_file = tmp / "result.json"
        ch.write_sync(report_file, json.dumps(report, sort_keys=True, indent=2) + "\n")
        qc = {
            "status": "INTERNAL_QC_PASS_PENDING_INDEPENDENT",
            "research": RESEARCH,
            "side": SIDE, "checkpoint_days": ch.TOTAL_DAYS,
            "checkpoint_last_manifest_sha256": last_sha,
            "result_sha256": old.sha(report_file),
            "ledger_sha256": hashes,
            "reserve_roundoff_evidence_sha256": report[
                "reserve_roundoff_evidence_sha256"
            ],
            "original_book_finish_used": True,
            "checkpoint_unchanged_after_finish": (
                old.sha(folder / ch.part_name(ch.TOTAL_DAYS - 1) / "manifest.json")
                == last_sha
            ),
            "independent_qc": "PENDING",
        }
        if not qc["checkpoint_unchanged_after_finish"]:
            raise RuntimeError("checkpoint changed while finalizing")
        ch.write_sync(tmp / "qc.json", json.dumps(qc, sort_keys=True, indent=2) + "\n")
        ch.fsync_dir(tmp)
        dest = ch.FINAL / SIDE.lower()
        if dest.exists() or dest.is_symlink():
            raise FileExistsError("concurrent finalization output")
        tmp.rename(dest)
        ch.fsync_dir(ch.FINAL)
        return {
            "status": qc["status"], "day_count": ch.TOTAL_DAYS,
            "ticks": state["ticks"], "cases": len(report["results"]),
            "source_head": source_head,
            "output": str(dest), "result_sha256": qc["result_sha256"],
            "independent_qc": "PENDING",
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-head", required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.source_head), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
