#!/usr/bin/env python3
"""#638: standalone reviewed independent QC for immutable 183d V2 replay.

The historical replay and original QC source pins stay unchanged. This short
one-shot verifier imports the ORIGINAL pinned producer from its detached,
read-only research checkout, but loads the REVIEWED QC source from this new
GitHub checkout by exact file path. It never reruns native trading or rewrites
checkpoints, result.json, trade_ledger.jsonl, or source snapshots.

Effective actor: cripta (UID 107, GID 109).
READ: original 183d source/market data, old producer checkout, QC checkout,
      immutable 183d research result and SHA checkpoints.
WRITE in finalize mode: exclusive new qc.json and atomic status.json update
      under the original 183d Data-disk result leaf only.
No database, system disk, production runtime, or Exchange mutations.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

PRODUCER = Path("/data/cripta/research/worktrees/p90l90_unified_depth_v2_20261113")
PRODUCER_HEAD = "d90e0a93117c5e7edc99ef6bd6ad3b0423e6c7ea"
ROOT = Path(
    "/data/cripta/research/runs/p90l90_bull_2025_holdout_v1/results/"
    "p90l90_unified_depth0_vs_0to15_capital_20261113_v2"
)
AUDITOR = Path(__file__).resolve().with_name(
    "p90l90_unified_depth_three_period_qc_v1.py"
)
GIB = 1024 ** 3


def git_blob_sha1(payload: bytes) -> str:
    """Verify the exact reviewed file blob from the authoritative GitHub PR."""
    return hashlib.sha1(
        ("blob %d\0" % len(payload)).encode("ascii") + payload
    ).hexdigest()


def preflight(args: argparse.Namespace) -> object:
    if (os.geteuid(), os.getegid()) != (107, 109):
        raise PermissionError("QC finalizer requires exact cripta UID107/GID109")
    if ROOT.is_symlink() or PRODUCER.is_symlink():
        raise ValueError("source/result root symlink forbidden")
    if not ROOT.is_dir() or not PRODUCER.is_dir() or AUDITOR.is_symlink():
        raise ValueError("pinned producer, immutable result or auditor missing")
    if not (ROOT / ".writer.lock").is_file():
        raise ValueError("original V2 writer lock file missing")
    if AUDITOR.parents[2] == PRODUCER:
        raise ValueError("auditor must not overwrite frozen producer checkout")
    if not os.access(ROOT, os.W_OK | os.X_OK):
        raise PermissionError("actual writer cannot create QC or atomic status")
    if not os.access(ROOT / "status.json", os.W_OK):
        raise PermissionError("actual writer cannot update status")
    if shutil.disk_usage(ROOT).free < 5 * GIB:
        raise RuntimeError("research Data partition below 5 GiB safety reserve")
    body = AUDITOR.read_bytes()
    if git_blob_sha1(body) != args.auditor_blob_sha:
        raise ValueError("reviewed GitHub QC blob SHA mismatch")
    status = json.loads((ROOT / "status.json").read_text())
    manifest = json.loads((ROOT / "manifest.json").read_text())
    if (
        status.get("stage") != "COMPLETE_PENDING_INDEPENDENT_QC"
        or status.get("days_done") != 183
        or status.get("days_total") != 183
        or status.get("source_head") != PRODUCER_HEAD
        or manifest.get("source_head") != PRODUCER_HEAD
        or manifest.get("period") != "183d"
        or (ROOT / "qc.json").exists()
        or (ROOT / "qc.json").is_symlink()
        or not (ROOT / "result.json").is_file()
        or not (ROOT / "trade_ledger.jsonl").is_file()
    ):
        raise ValueError("not the pinned incomplete 183d QC, or already finalized")
    for filename, status_field in (
        ("result.json", "result_sha256"),
        ("trade_ledger.jsonl", "ledger_sha256"),
    ):
        observed = hashlib.sha256((ROOT / filename).read_bytes()).hexdigest()
        if observed != status.get(status_field):
            raise ValueError(filename + " immutable SHA drift")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(PRODUCER))
    spec = importlib.util.spec_from_file_location(
        "_cripta_638_reviewed_independent_qc", str(AUDITOR)
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("reviewed QC loader unavailable")
    qc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qc)
    if qc.run.SOURCE.resolve() != PRODUCER.resolve():
        raise ValueError("QC imported non-original producer source")
    return qc


def main() -> None:
    # This finalizer runs on Linux only. Import lazily so cross-platform CI
    # can safely import its source and validate the contract.
    if os.name != "posix":
        raise OSError("independent research QC finalizer requires Linux")
    import fcntl

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("preflight", "finalize"), required=True)
    parser.add_argument("--auditor-head", required=True)
    parser.add_argument("--auditor-blob-sha", required=True)
    args = parser.parse_args()
    if len(args.auditor_head) != 40 or len(args.auditor_blob_sha) != 40:
        raise ValueError("expected 40-hex GitHub commit/blob")
    qc = preflight(args)

    # Same lock file used by the native replay. Never create a new result leaf.
    with (ROOT / ".writer.lock").open("r") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = qc.audit(
            "183d", ROOT, PRODUCER_HEAD, dry_run=True,
            auditor_head=args.auditor_head,
        )
        if args.mode == "finalize":
            # All independent checks passed before any write. This runs the
            # same audit again, and only then creates qc.json exclusively.
            report = qc.audit(
                "183d", ROOT, PRODUCER_HEAD, dry_run=False,
                auditor_head=args.auditor_head,
            )
        print(json.dumps({
            "mode": args.mode,
            "result": report,
            "producer_head": PRODUCER_HEAD,
            "auditor_head": args.auditor_head,
            "auditor_blob_sha": args.auditor_blob_sha,
        }, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
