#!/usr/bin/env python3
"""Independent systemd-only resume adapter for immutable P90L90 width sweep #617.

This controller is NOT a new Entry/Exit/Strategy and never edits the frozen
original source. The original 2025 source HEAD and checkpoint chain remain
pinned. The only idempotency exception permits an already-published QC file
when full independent recomputation yields exactly the same JSON object.

Operational contract:
 EFFECTIVE_ACTOR: cripta UID107:GID109 (systemd User=cripta, Group=cripta)
 READ: exact frozen source worktree, source catalogs, native tick archives,
       existing research baseline, all old SHA checkpoints and QC
 WRITE/CREATE/RENAME: existing #617 OUT/status.json, later checkpoint/full/
       qc/summary under OUT and append-only separate systemd stdout log
 DELETE: none; QC and prior ledgers are never deleted or overwritten
 SYSTEMD: independent transient .service, ProtectSystem=strict,
          ReadWritePaths=<research-results parent>, NoNewPrivileges=yes
 DB/Exchange/LIVE: none
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

FROZEN_HEAD = "ea170ba1c08e4cbd16f8c328834217623e365278"
FROZEN_SRC_SHA256 = "af41b9271d8318a45e268e6f1932c35daba3735a890b95339079903c3fb78120"
FROZEN_ROOT = Path("/data/cripta/research/worktrees/p90l90_prev7d_expanded_20251112")
FROZEN_REL = "scripts/research/p90l90_bull2025_prev7d_width_expanded_v2.py"
OUT = Path(
    "/data/cripta/research/runs/p90l90_bull_2025_holdout_v1/results/"
    "portfolio_depthzero_prev7d_expanded_2025_v2"
)
BANDS = ("010", "020", "080", "090", "100", "150")
SIDES = ("long_only", "short_only")
EXPECTED_DAYS = {
    ("010", "long_only"): 183,
    ("010", "short_only"): 100,
    **{(code, side): (1 if side == "long_only" else 0)
       for code in BANDS if code != "010" for side in SIDES},
}
EXPECTED_ERROR = (
    "immutable output already exists: " +
    str(OUT / "band010" / "qc_long_only.json")
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(2**20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frozen_module():
    # Import the reviewed immutable old worktree, not code from the controller
    # checkout. The provenance of that source is independently reverified.
    if str(FROZEN_ROOT) not in sys.path:
        sys.path.insert(0, str(FROZEN_ROOT))
    from scripts.research import p90l90_bull2025_prev7d_width_expanded_v2 as sweep
    if sweep.SOURCE.resolve() != FROZEN_ROOT.resolve() or sweep.OUT != OUT:
        raise RuntimeError("frozen original source/output identity mismatch")
    return sweep


def guard(sweep, controller_digest: str):
    if (os.geteuid(), os.getegid()) != (107, 109):
        raise PermissionError("expected actual service User=cripta Group=cripta")
    if sha256(Path(__file__)) != controller_digest:
        raise ValueError("controller source SHA256 drift")
    frozen = FROZEN_ROOT / FROZEN_REL
    if sha256(frozen) != FROZEN_SRC_SHA256:
        raise ValueError("original frozen research source changed")
    head = FROZEN_ROOT / ".git" / "HEAD"
    if not head.is_file() or head.read_text().strip() != FROZEN_HEAD:
        raise ValueError("original research detached Git HEAD changed")
    if OUT.is_symlink() or not OUT.is_dir() or not os.access(OUT, os.W_OK | os.X_OK):
        raise PermissionError("original run output not writable by cripta")
    if OUT.parent.is_symlink() or not os.access(OUT.parent, os.W_OK | os.X_OK):
        raise PermissionError("original result parent not writable by cripta")
    if shutil.disk_usage(OUT).free < 5 * 2**30:
        raise RuntimeError("research disk below owner-approved 5 GiB reserve")
    launch = json.loads((OUT / "launch.json").read_text())
    if launch.get("source_head") != FROZEN_HEAD or launch.get("source_sha256") != FROZEN_SRC_SHA256:
        raise ValueError("original launch provenance mismatch")
    if Path(f"/proc/{launch['pid']}").exists():
        raise RuntimeError("original worker may still be alive")
    pilot = json.loads((OUT / "pilot.json").read_text())
    if pilot.get("status") != "PILOT_PASS" or pilot.get("source_head") != FROZEN_HEAD:
        raise ValueError("original 6-band real-data pilot not verified")
    status = json.loads((OUT / "status.json").read_text())
    if (status.get("stage"), status.get("source_head"), status.get("error")) != (
        "FAILED", FROZEN_HEAD, EXPECTED_ERROR
    ):
        raise RuntimeError("not the exact previously failed immutable-QC resume")
    if (OUT / "summary.json").exists():
        raise FileExistsError("already completed output: no second worker")
    lock_file = OUT / ".worker.lock"
    if not lock_file.is_file() or lock_file.is_symlink():
        raise RuntimeError("original worker lock missing/unsafe")
    with lock_file.open("r+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another worker already owns research lock") from exc
        finally:
            pass  # closing the file releases only our preflight probe lock
    engine, catalog, current = sweep.prepare(FROZEN_HEAD)
    verified = {}
    for code in BANDS:
        sweep.bind(engine, current, code)
        for side in SIDES:
            folder = sweep.target(code) / "checkpoint" / side
            state, _last, _digest = engine.load_parts(folder)
            days = state["next_day"] if state is not None else 0
            if days != EXPECTED_DAYS[(code, side)]:
                raise ValueError(f"unexpected SHA checkpoint day {code} {side}: {days}")
            qc_file = sweep.target(code) / ("qc_" + side + ".json")
            if qc_file.exists():
                if code != "010" or side != "long_only" or qc_file.is_symlink():
                    raise ValueError("unexpected prior QC record")
                qc = json.loads(qc_file.read_text())
                report = sweep.target(code) / "full" / side / "result.json"
                if (
                    qc.get("status") != sweep.QC_STATUS
                    or qc.get("source_head") != FROZEN_HEAD
                    or qc.get("source_sha256") != FROZEN_SRC_SHA256
                    or qc.get("candidate_count") != catalog["counts"][code]["LONG_ONLY"]
                    or qc.get("result_sha256") != sha256(report)
                ):
                    raise ValueError("existing immutable QC/source/result SHA drift")
            verified[code + "_" + side] = days
    return {
        "status": "IMMUTABLE_RESUME_PREFLIGHT_PASS",
        "original_source_head": FROZEN_HEAD,
        "original_source_sha256": FROZEN_SRC_SHA256,
        "controller_sha256": controller_digest,
        "verified_days": verified,
        "existing_qc": "010_LONG_ONLY_PASS_SHA_MATCH",
        "disk_free_gib": round(shutil.disk_usage(OUT).free / 2**30, 3),
    }, engine, catalog, current


def install_exact_qc_reuse(sweep) -> None:
    original = sweep.write_once
    permitted = OUT / "band010" / "qc_long_only.json"

    def qc_write_once(path: Path, value: dict) -> None:
        path = Path(path)
        if path == permitted and path.is_file() and not path.is_symlink():
            # independent_qc has just recomputed all ledger rows, fills,
            # commissions, hardstops, candidate IDs and result SHA. Require
            # exact object equality; no overwrite or waiver of QC.
            if json.loads(path.read_text()) != value:
                raise ValueError("recomputed QC differs from immutable LONG QC")
            print(json.dumps({
                "event": "IMMUTABLE_QC_IDEMPOTENT_VERIFIED",
                "qc_file": str(path), "sha256": sha256(path)
            }), flush=True)
            return
        original(path, value)

    sweep.write_once = qc_write_once


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--worker", action="store_true")
    parser.add_argument("--controller-sha256", required=True)
    args = parser.parse_args()
    sweep = frozen_module()
    facts, engine, catalog, current = guard(sweep, args.controller_sha256)
    print(json.dumps(facts, sort_keys=True), flush=True)
    if args.worker:
        install_exact_qc_reuse(sweep)
        sweep.worker(engine, catalog, current, FROZEN_HEAD)


if __name__ == "__main__":
    main()
