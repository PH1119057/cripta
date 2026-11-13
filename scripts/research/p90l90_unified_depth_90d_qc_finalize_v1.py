#!/usr/bin/env python3
"""#638: finalize 90d unified P90L90 from frozen native result, no replay.

Strictly separate actor/path/version from 183d finalization. This script
loads the original 90d producer from a detached unchanged GitHub worktree
and the independently reviewed QC module from this separate auditor tree.

FILE ACTOR / OPS:
  cripta UID107:GID109, no DB role, no Exchange, no trading runtime.
  READ: exact producer detached checkout, exact auditor source, immutable
        source data, results, manifest and complete checkpoint SHA chain.
  CREATE: only qc.json exclusively under pre-existing 90d Data-disk leaf.
  REPLACE VIA ATOMIC RENAME: only status.json in that same existing leaf.
  DELETE: none (except own temporary status via existing atomic helper).
  Systemd: no service started; synchronous cripta-scoped command.
Never rewrite result.json, ledger, checkpoints, producer code or source.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PRODUCER = Path("/data/cripta/research/worktrees/p90l90_unified_depth_90d_20261114")
PRODUCER_HEAD = "500e4b381642d98d117c5b57f62fd81418156cb7"
ROOT = Path(
    "/data/cripta/research/runs/p90l90_90d_warmup_v2_20261024/results/"
    "p90l90_unified_depth0_vs_0to15_capital_20261113_v2"
)
AUDITOR = Path(__file__).resolve().with_name(
    "p90l90_unified_depth_three_period_qc_v1.py"
)
GIB = 1024 ** 3


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_blob_sha1(payload: bytes) -> str:
    return hashlib.sha1(
        ("blob %d\0" % len(payload)).encode("ascii") + payload
    ).hexdigest()


def git_head(checkout: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, timeout=15,
    ).stdout.strip()


def preflight(args: argparse.Namespace) -> object:
    if (os.geteuid(), os.getegid()) != (107, 109):
        raise PermissionError("exact research writer cripta UID107 GID109 required")
    if any(p.is_symlink() for p in (ROOT, PRODUCER, AUDITOR, AUDITOR.parents[2])):
        raise ValueError("QC source/result symlink forbidden")
    if not ROOT.is_dir() or not PRODUCER.is_dir() or not AUDITOR.is_file():
        raise FileNotFoundError("original producer/result or reviewer missing")
    if PRODUCER == AUDITOR.parents[2]:
        raise ValueError("reviewer must not change pinned producer source")
    if git_head(PRODUCER) != PRODUCER_HEAD:
        raise ValueError("producer checkout HEAD drift")
    if git_head(AUDITOR.parents[2]) != args.auditor_head:
        raise ValueError("reviewed checkout HEAD drift")
    if not (ROOT / ".writer.lock").is_file():
        raise ValueError("existing native writer lock missing")
    if not os.access(ROOT, os.W_OK | os.X_OK):
        raise PermissionError("writer cannot create immutable QC/status in result leaf")
    if not os.access(ROOT / "status.json", os.W_OK):
        raise PermissionError("writer cannot replace status")
    if shutil.disk_usage(ROOT).free < 5 * GIB:
        raise RuntimeError("research Data disk reserve below 5 GiB")
    if git_blob_sha1(AUDITOR.read_bytes()) != args.auditor_blob_sha:
        raise ValueError("auditor source not the approved GitHub blob")
    status = json.loads((ROOT / "status.json").read_text())
    manifest = json.loads((ROOT / "manifest.json").read_text())
    if (
        status.get("stage") != "COMPLETE_PENDING_INDEPENDENT_QC"
        or status.get("days_done") != 90
        or status.get("days_total") != 90
        or status.get("source_head") != PRODUCER_HEAD
        or manifest.get("source_head") != PRODUCER_HEAD
        or manifest.get("period") != "90d"
        or (ROOT / "qc.json").exists()
        or (ROOT / "qc.json").is_symlink()
        or not (ROOT / "result.json").is_file()
        or not (ROOT / "trade_ledger.jsonl").is_file()
    ):
        raise ValueError("90d frozen result contract differs or QC already finalized")
    for name, key in (
        ("result.json", "result_sha256"),
        ("trade_ledger.jsonl", "ledger_sha256"),
    ):
        if sha256(ROOT / name) != status.get(key):
            raise ValueError("immutable " + name + " SHA drift")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(PRODUCER))
    spec = importlib.util.spec_from_file_location(
        "_cripta_638_90d_reviewed_qc", AUDITOR
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load reviewed independent QC")
    qc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qc)
    if qc.run.SOURCE.resolve() != PRODUCER.resolve():
        raise ValueError("auditor imported wrong original producer")
    return qc


def main() -> None:
    if os.name != "posix":
        raise OSError("frozen native 90d QC finalizer is Linux-only")
    import fcntl

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("preflight", "finalize"), required=True)
    parser.add_argument("--auditor-head", required=True)
    parser.add_argument("--auditor-blob-sha", required=True)
    args = parser.parse_args()
    if any(len(x) != 40 or any(c not in "0123456789abcdef" for c in x)
           for x in (args.auditor_head, args.auditor_blob_sha)):
        raise ValueError("expected exact 40-hex GitHub identities")
    qc = preflight(args)

    # The native replay already owns the lock file, never create a new one.
    with (ROOT / ".writer.lock").open("r") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        dry = qc.audit(
            "90d", ROOT, PRODUCER_HEAD, dry_run=True,
            auditor_head=args.auditor_head,
        )
        if args.mode == "finalize":
            report = qc.audit(
                "90d", ROOT, PRODUCER_HEAD, dry_run=False,
                auditor_head=args.auditor_head,
            )
        else:
            report = dry
        print(json.dumps({
            "mode": args.mode, "result": report,
            "producer_head": PRODUCER_HEAD,
            "auditor_head": args.auditor_head,
            "auditor_blob_sha": args.auditor_blob_sha,
        }, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
