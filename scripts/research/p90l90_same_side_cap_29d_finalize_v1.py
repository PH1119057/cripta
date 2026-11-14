#!/usr/bin/env python3
"""#638 independent recovery from existing 29/29 native checkpoint and ledger.

Original pinned producer finished ALL native ticks and wrote all 29 immutable
checkpoints and the 8-case trade ledger. It failed on original result metadata
lookup: result_sha256 belongs in original status.json, not result.json.

Reader modes:
 --mode preflight: only read, validate every original checkpoint SHA, producer
 source snapshot, original independent QC, all 8 reconstructed closed ledgers,
 cap5 exact original trade/result parity; NO native tick replay.
 --mode finalize: the same full preflight, then CREATE missing result.json,
 atomically REPLACE own status.json, run the immutable pinned producer's QC in a
 NEW independent Python interpreter. Existing checkpoints/ledger/manifest and
 imported source bytes never overwritten. If QC fails, evidence remains PENDING.

Actor/path: cripta UID107/GID109; source checkout GitHub main auditor verified,
original producer separate detached 8c452130..., read frozen 29d inputs/QC
and this exact 29d Data research leaf. Mutations only result.json + qc.json
creation, status.json atomic rename in existing single versioned result leaf;
no raw data, trading runtime, systemd restart, DB, Exchange, LIVE, deletion.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

AUDITOR = Path(__file__).resolve().parents[2]
PRODUCER = Path("/data/cripta/research/worktrees/p90l90_sidecap_638_20261114")
ORIGINAL_HEAD = "8c45213038b922272cfcd067f51dd6245ba96977"
PERIOD = "29d"
DEST = (
    Path("/data/cripta/research/runs/p90l90_prev7d_29d_20261025/results")
    / "p90l90_unified_sidecap_2_3_4_5_takertaker_20261114_v1"
)
PRODUCER_FILE = (
    PRODUCER / "scripts/research/p90l90_same_side_cap_counterfactual_v1.py"
)
AUDITOR_FILE = Path(__file__).resolve()

def load_original_producer() -> None:
    """Lazy import: absolute-path CLI --help remains portable in Windows CI."""
    global prior
    sys.path.insert(0, str(PRODUCER))
    from scripts.research import p90l90_same_side_cap_counterfactual_v1
    prior = p90l90_same_side_cap_counterfactual_v1


def file_sha(path: Path) -> str:
    d = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(2 ** 20), b""):
            d.update(block)
    return d.hexdigest()


def head(root: Path) -> str:
    p = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        capture_output=True, text=True, check=True, timeout=20
    )
    return p.stdout.strip()


def frozen_result_sha(status: dict, result: dict) -> str:
    expected = status.get("result_sha256")
    if (not isinstance(expected, str) or len(expected) != 64
            or any(c not in "0123456789abcdef" for c in expected)
            or "result_sha256" in result):
        raise ValueError("original result SHA must exist only in QC status")
    return expected


def preflight_identity(auditor_head: str) -> dict:
    if os.name != "posix" or (os.geteuid(), os.getegid()) != (107, 109):
        raise PermissionError("exact actor cripta UID107/GID109 required")
    if head(AUDITOR) != auditor_head or head(PRODUCER) != ORIGINAL_HEAD:
        raise ValueError("auditor/producer exact Git identity mismatch")
    if (not AUDITOR_FILE.is_file() or AUDITOR_FILE.is_symlink()
            or not PRODUCER_FILE.is_file() or PRODUCER_FILE.is_symlink()):
        raise ValueError("unverifiable auditor/producer Python source")
    if (not DEST.is_dir() or DEST.is_symlink()
            or DEST.stat().st_uid != 107
            or not os.access(DEST, os.R_OK | os.W_OK | os.X_OK)
            or shutil.disk_usage(DEST).free < 5 * 2**30):
        raise PermissionError("producer Data leaf actor/space mismatch")
    for name in ("result.json", "qc.json", "qc_failure.json"):
        if (DEST / name).exists() or (DEST / name).is_symlink():
            raise FileExistsError("would overwrite existing immutable " + name)
    lock = DEST / ".writer.lock"
    if lock.is_symlink() or not lock.is_file():
        raise ValueError("producer writer flock absent")
    s = json.loads((DEST / "status.json").read_text())
    m = json.loads((DEST / "manifest.json").read_text())
    if (s.get("stage") != "RUNNING"
            or int(s.get("days_done", -1)) != 29
            or int(s.get("days_total", -1)) != 29
            or s.get("source_head") != ORIGINAL_HEAD
            or s.get("period") != PERIOD
            or m.get("source_head") != ORIGINAL_HEAD
            or m.get("research") != prior.RESEARCH
            or m.get("period") != PERIOD
            or m.get("evaluation_days") != 29
            or set(m.get("cases", [])) != set(prior.CASES)):
        raise ValueError("not exact failed native 29/29 producer lineage")
    pins = prior.pins_for(ORIGINAL_HEAD)
    if m.get("source_pins") != pins:
        raise ValueError("producer source pins/manifest differ")
    for path, checksum in pins.items():
        f = DEST / "source_snapshot" / Path(path).name
        if not f.is_file() or f.is_symlink() or file_sha(f) != checksum:
            raise ValueError("stored original source snapshot SHA drift")
    return {"status": s, "manifest": m, "pins": pins}


def recover(context: dict) -> dict:
    snap, last_sha = prior.scan_checkpoints(DEST, PERIOD, context["pins"])
    if (snap is None or snap.get("days_done") != 29
            or last_sha != context["status"].get("last_checkpoint_sha256")):
        raise ValueError("full native 29d SHA chain/status mismatch")
    books, refs = prior.make_books(PERIOD)
    for name, value in snap["books"].items():
        prior.restore(books[name], value)
    for name, value in snap["reference_books"].items():
        prior.engine.prev.restore_book(refs[name], value)
    source_status, source_result, reference_ledger = prior.provenance(PERIOD)
    source_result_sha = frozen_result_sha(source_status, source_result)
    if file_sha(prior.engine.output(PERIOD) / "result.json") != source_result_sha:
        raise ValueError("original QC source result SHA mismatch")
    if (snap["source_seen"] != source_result["source_events_seen"]
            or snap["last_native_prices"] != source_result["last_native_prices"]):
        raise ValueError("native tick source event/price coverage drift")
    refchecks = prior.engine.compare_reference(PERIOD, refs)
    file = DEST / "trade_ledger.jsonl"
    if not file.is_file() or file.is_symlink():
        raise ValueError("completed original cap ledger absent")
    ledger = {name: [] for name in prior.CASES}
    with file.open() as f:
        for line in f:
            row = json.loads(line)
            name = row.pop("research_case")
            if name not in ledger:
                raise ValueError("foreign case in native ledger")
            ledger[name].append(row)
    if sum(map(len, ledger.values())) == 0:
        raise ValueError("empty native 8-case ledger")
    for name, book in books.items():
        if (book.trades != ledger[name]
                or book.max_same_side_slots > book.cap
                or book.counts["accepted"] != book.counts["closed"] +
                sum(p is not None for p in book.pos.values())
                or sum((D(r["net_usdt"]) for r in ledger[name]), D(0))
                != book.m["closed_net_usdt"]):
            raise ValueError("checkpoint/ledger/cap/after-fees mismatch " + name)
    baseline = "CAP5__ORIGINAL_MAKER_TP"
    if ledger[baseline] != reference_ledger:
        raise ValueError("CAP5 original native V2 ledger parity mismatch")
    last_prices = {name: D(value) for name, value in snap["last_native_prices"].items()}
    if set(last_prices) != set(prior.engine.SYMBOLS):
        raise ValueError("missing native price state")
    end = (
        datetime.fromisoformat(prior.engine.WINDOW[PERIOD]["start"])
        .replace(tzinfo=UTC) + timedelta(days=29)
    ).timestamp()
    cases = {name: book.finish_unified(last_prices, end)
             for name, book in books.items()}
    native = source_result["cases"][prior.NATIVE_CASE]
    for key in ("wallet_usdt", "wallet_net_usdt",
                "equity_with_open_unrealized_gross_usdt",
                "reserved_margin_usdt", "open_censored", "money",
                "hardstop_count", "max_simultaneous_slots"):
        if cases[baseline][key] != native[key]:
            raise ValueError("original cap5 final native parity mismatch " + key)
    for name, item in cases.items():
        if (item["max_same_side_slots"] > item["same_side_cap"]
                or item["counts"]["closed"] != len(ledger[name])
                or D(item["money"]["closed_net_usdt"]) !=
                sum((D(r["net_usdt"]) for r in ledger[name]), D(0))):
            raise ValueError("recovered native case mismatch " + name)
    return {
        "cases": cases, "source_seen": snap["source_seen"],
        "last_prices": snap["last_native_prices"],
        "reference_checks": refchecks,
        "original_result_sha256": source_result_sha,
        "original_ledger_sha256": source_status["ledger_sha256"],
        "final_checkpoint_sha256": last_sha,
        "ledger_sha256": file_sha(file),
        "case_rows": {k: len(v) for k, v in ledger.items()},
    }


def result_from_original(ctx: dict, data: dict, reviewer_head: str) -> dict:
    return {
        "status": "COMPLETE_PENDING_INDEPENDENT_QC",
        "research": prior.RESEARCH,
        "period": PERIOD, "source_head": ORIGINAL_HEAD,
        "source_pins": ctx["pins"],
        "cases": data["cases"], "case_count": len(prior.CASES),
        "original_cap5_trade_by_trade_parity": True,
        "original_reference_trade_parity": data["reference_checks"],
        "original_frozen_ledger_sha256": data["original_ledger_sha256"],
        "original_frozen_result_sha256": data["original_result_sha256"],
        "last_source_prices": data["last_prices"],
        "source_events_seen": data["source_seen"],
        "final_checkpoint_sha256": data["final_checkpoint_sha256"],
        "ledger_sha256": data["ledger_sha256"],
        "fee_baseline": "TAKER_TAKER",
        "fee_sensitivity": "ORIGINAL_MAKER_TP",
        "shared_wallet_initial": "1000", "one_slot_per_coin": True,
        "native_execution_not_real": True,
        "model_exclusions": [
            "spread", "slippage", "funding", "latency", "actual_exchange_fills",
            "future_exit_fees_of_open_terminal_positions"
        ],
        "research_only": True, "not_pristine_blinded_oos": True,
        "independent_rescue": {
            "auditor_head": reviewer_head,
            "auditor_code_sha256": file_sha(AUDITOR_FILE),
            "original_producer_head": ORIGINAL_HEAD,
            "reason": "original frozen result_sha256 exists only in status.json",
            "native_replay_reexecuted": False,
            "source_ledger_modified": False,
            "source_checkpoint_modified": False,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=("preflight", "finalize"))
    ap.add_argument("--auditor-head", required=True)
    ap.add_argument("--producer-head", required=True)
    args = ap.parse_args()
    if args.producer_head != ORIGINAL_HEAD:
        raise ValueError("not exact original source that created checkpoint")
    if len(args.auditor_head) != 40 or any(
        c not in "0123456789abcdef" for c in args.auditor_head
    ):
        raise ValueError("invalid reviewed auditor source SHA")
    load_original_producer()
    import fcntl  # unavailable on Windows, valid only for real POSIX actor
    ctx = preflight_identity(args.auditor_head)
    with (DEST / ".writer.lock").open("r") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        recovered = recover(ctx)
        evidence = {
            "stage": "INDEPENDENT_RESUME_PREFLIGHT_PASS",
            "period": PERIOD, "days": 29,
            "cases": len(recovered["cases"]),
            "trades_per_case": recovered["case_rows"],
            "last_checkpoint_sha256": recovered["final_checkpoint_sha256"],
            "ledger_sha256": recovered["ledger_sha256"],
            "cap5_exact_original_native_parity": True,
            "reviewer_head": args.auditor_head,
            "producer_head": ORIGINAL_HEAD,
            "native_replay_reexecuted": False,
        }
        if args.mode == "preflight":
            print(json.dumps(evidence, sort_keys=True))
            return
        output = result_from_original(ctx, recovered, args.auditor_head)
        # All validations already completed. New output file only (x).
        prior.write_json_immutable(DEST / "result.json", output)
        prior.engine.prev.atomic_status(DEST, {
            "stage": "COMPLETE_PENDING_INDEPENDENT_QC",
            "source_head": ORIGINAL_HEAD, "period": PERIOD,
            "days_done": 29, "days_total": 29,
            "result_sha256": file_sha(DEST / "result.json"),
            "ledger_sha256": recovered["ledger_sha256"],
        })
    # Reviewed original audit code, fresh Python process, no native tick replay.
    qc = subprocess.run(
        [sys.executable, "-B", str(PRODUCER_FILE),
         "--period", PERIOD, "--source-head", ORIGINAL_HEAD, "--mode", "qc"],
        cwd=str(PRODUCER), capture_output=True, text=True,
        timeout=1200, check=False,
    )
    if qc.returncode:
        raise RuntimeError("INDEPENDENT_QC_FAILED_PRESERVED_PENDING: "
                           + qc.stderr[-6500:])
    final = json.loads((DEST / "status.json").read_text())
    audited = json.loads((DEST / "qc.json").read_text())
    if (final.get("stage") != "COMPLETED_INDEPENDENT_QC_PASS"
            or audited.get("status") != "INDEPENDENT_QC_PASS_P90L90_SAME_SIDE_CAP_V1"
            or audited.get("producer_head") != ORIGINAL_HEAD
            or audited.get("native_frozen_cap5_original_full_parity") is not True
            or final.get("qc_sha256") != file_sha(DEST / "qc.json")
            or final.get("result_sha256") != file_sha(DEST / "result.json")
            or final.get("ledger_sha256") != recovered["ledger_sha256"]):
        raise ValueError("final original independent QC proof did not pass")
    print(json.dumps({
        **evidence, "stage": "COMPLETED_INDEPENDENT_QC_PASS",
        "qc_sha256": final["qc_sha256"],
        "result_sha256": final["result_sha256"],
        "ledger_sha256": final["ledger_sha256"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
