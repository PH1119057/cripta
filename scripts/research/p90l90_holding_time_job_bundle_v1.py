#!/usr/bin/env python3
"""Build an immutable #646 ZIP for the existing independent CRIPTA job runner.

PREPARE only: this program never submits, starts, or supervises a job.
Only cripta UID107:GID109 may create package files on the research data disk.
The intake/runner systemd services, not SentinelX exec, must run the replay.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import re
import subprocess
import zipfile

SOURCE_BY_PERIOD = {
    "29d": "p90l90_holding_time_2026_v1.py",
    "90d": "p90l90_holding_time_2026_v1.py",
    "183d": "p90l90_holding_time_2025_v1.py",
}
DATASET_BY_PERIOD = {
    "29d": "20260921_20261020",
    "90d": "20260518_20260816",
    "183d": "20250401_20251001",
}
PREFIX_BY_PERIOD = {
    "29d": "C2-R646-H29-",
    "90d": "C2-R646-H90-",
    "183d": "C2-R646-H25-",
}
COINS = ("LTCUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT", "INJUSDT")
PHASES = ("ALL", "NEG")
RESEARCH_RUNS = pathlib.Path("/data/cripta/research/runs")
PACKAGE_ROOT = RESEARCH_RUNS / "p90l90_holding_time_646" / "packages"
JOB_ID_PATTERN = re.compile(r"C2-R646-H(?:29|90|25)-[A-Z0-9][A-Z0-9_-]{1,40}\Z")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")


def validate_identity(period: str, phase: str, job_id: str,
                      source_head: str) -> None:
    if period not in SOURCE_BY_PERIOD or phase not in PHASES:
        raise ValueError("invalid independent replay period/phase")
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValueError("invalid C2-R646 job ID")
    if not job_id.startswith(PREFIX_BY_PERIOD[period]):
        raise ValueError("job ID cannot describe another period")
    if not SHA_PATTERN.fullmatch(source_head):
        raise ValueError("source head must be a 40-digit Git SHA")


def launcher(job_id: str, source_name: str, source_sha: str,
             period: str, phase: str) -> bytes:
    cli = (
        f'sys.argv = [str(source), "--period", {period!r}, "--phase", {phase!r}]'
        if period != "183d"
        else f'sys.argv = [str(source), "--phase", {phase!r}]'
    )
    content = f'''#!/usr/bin/env python3
"""Independent CRIPTA job-runner entrypoint; no SentinelX process dependency."""
import hashlib
import json
import os
import pathlib
import runpy
import sys

JOB_ID = {job_id!r}
SOURCE_SHA256 = {source_sha!r}
SOURCE_NAME = {source_name!r}
PERIOD = {period!r}
PHASE = {phase!r}

if (os.geteuid(), os.getegid()) != (107, 109):
    raise PermissionError("expected cripta 107:109")
if os.environ.get("CRIPTA_JOB_ID") != JOB_ID:
    raise RuntimeError("job-runner durable identity mismatch")
root = pathlib.Path.cwd().resolve()
expected = pathlib.Path("/data/cripta/jobs/running") / JOB_ID / "bundle"
if root != expected or root.is_symlink() or not root.is_dir():
    raise PermissionError("not running inside the approved independent job spool")
status_path = root.parent / "status.json"
if status_path.is_symlink() or not status_path.is_file():
    raise RuntimeError("job-runner status receipt missing")
manifest = json.loads(status_path.read_text(encoding="utf-8"))["manifest"]
if (manifest["job_id"] != JOB_ID
        or manifest["entrypoint"] != "job_launcher.py"
        or manifest["parameters"]["phase"] != PHASE
        or manifest["parameters"]["period"] != PERIOD
        or manifest["parameters"]["source_sha256"] != SOURCE_SHA256):
    raise RuntimeError("job manifest does not match frozen entrypoint")
source = root / "source_snapshot" / SOURCE_NAME
if source.is_symlink() or not source.is_file():
    raise RuntimeError("missing frozen source")
if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA256:
    raise RuntimeError("research code changed after intake")
{cli}
runpy.run_path(str(source), run_name="__main__")
'''
    return content.encode("utf-8")


def build_bundle(repo_root: pathlib.Path, period: str, phase: str,
                 job_id: str, source_head: str) -> tuple[bytes, dict]:
    """Pure builder, no filesystem writes; safe for offline contract tests."""
    validate_identity(period, phase, job_id, source_head)
    src_name = SOURCE_BY_PERIOD[period]
    path = repo_root / "scripts" / "research" / src_name
    body = path.read_bytes()
    if not body.startswith(b"#!/usr/bin/env python3\n"):
        raise ValueError("unexpected frozen research source header")
    source_sha = hashlib.sha256(body).hexdigest()
    entrypoint = launcher(job_id, src_name, source_sha, period, phase)
    manifest = {
        "job_id": job_id,
        "title": f"P90L90 holding-age Exit #646 {period} {phase}",
        "line": "legacy-recheck",
        "entrypoint": "job_launcher.py",
        "dataset": {
            "period": DATASET_BY_PERIOD[period],
            "symbols": list(COINS),
        },
        "parameters": {
            "research_issue": 646,
            "period": period,
            "phase": phase,
            "source_git_head": source_head,
            "source_sha256": source_sha,
            "native_exit": "original RAW L5-3 structural TP",
            "same_tick_priority": "existing stop/resting TP before time limit",
            "time_limits_hours": [None, 3, 6, 12, 18, 24, 36, 48, 72],
            "no_cap_original_ledger_parity_required": True,
        },
        "dependencies": [
            "GitHub PH1119057/cripta main synchronized source mirror",
            "verified historical native Bybit data on /data/cripta",
            "existing #635 NO_CAP independent baseline ledgers",
        ],
        "timeout_seconds": 604800 if period == "183d" else 172800,
        "notes": (
            "Historical offline research only; NO LIVE/DB/Exchange. "
            "Independent systemd job intake/runner, cripta actor. "
            "Read source from approved mirror; write only job spool and "
            "approved data-mounted reports. Do not run from SentinelX exec."
        ),
    }
    entries = {
        "job.json": (json.dumps(manifest, ensure_ascii=False,
                                sort_keys=True, indent=2) + "\n").encode("utf-8"),
        "job_launcher.py": entrypoint,
        f"source_snapshot/{src_name}": body,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, entries[name])
    return buffer.getvalue(), manifest


def prepare(period: str, phase: str, job_id: str,
            source_head: str) -> dict:
    """Prepare unsubmitted ZIP; never mutate intake/queued/running state."""
    if (os.geteuid(), os.getegid()) != (107, 109):
        raise PermissionError("package creator must be Unix cripta 107:109")
    validate_identity(period, phase, job_id, source_head)
    repo = pathlib.Path(__file__).resolve().parents[2]
    actual = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if actual != source_head:
        raise RuntimeError("isolated research checkout Git HEAD mismatch")
    if (not RESEARCH_RUNS.is_dir() or RESEARCH_RUNS.is_symlink()
            or RESEARCH_RUNS.stat().st_uid != 107
            or not os.access(RESEARCH_RUNS, os.W_OK | os.X_OK)):
        raise PermissionError("data-disk research/runs actor/path preflight failed")
    v = os.statvfs(RESEARCH_RUNS)
    if v.f_bavail * v.f_frsize < 5 * 2**30:
        raise RuntimeError("data SSD has less than 5 GiB reserve")
    contents, manifest = build_bundle(repo, period, phase, job_id, source_head)
    task_dir = PACKAGE_ROOT.parent
    if task_dir.is_symlink():
        raise RuntimeError("task dir symlink refused")
    if not task_dir.exists():
        task_dir.mkdir(mode=0o2770)
    if (task_dir.stat().st_uid != 107
            or not os.access(task_dir, os.W_OK | os.X_OK)):
        raise PermissionError("task parent actor/path preflight failed")
    if PACKAGE_ROOT.is_symlink():
        raise RuntimeError("package dir symlink refused")
    if not PACKAGE_ROOT.exists():
        PACKAGE_ROOT.mkdir(mode=0o2770)
    if (PACKAGE_ROOT.stat().st_uid != 107
            or not os.access(PACKAGE_ROOT, os.W_OK | os.X_OK)):
        raise PermissionError("package parent actor/path preflight failed")
    target = PACKAGE_ROOT / f"{job_id}.zip"
    pending = PACKAGE_ROOT / f"{job_id}.pending"
    if (target.exists() or target.is_symlink()
            or pending.exists() or pending.is_symlink()):
        raise FileExistsError("job package already exists")
    with pending.open("xb") as stream:
        stream.write(contents)
        stream.flush()
        os.fsync(stream.fileno())
    pending.rename(target)
    return {
        "status": "PACKAGE_PREPARED_NOT_SUBMITTED_NOT_RUNNING",
        "job_id": job_id,
        "package": str(target),
        "package_sha256": hashlib.sha256(contents).hexdigest(),
        "research_source_sha256": manifest["parameters"]["source_sha256"],
        "source_git_head": source_head,
        "actor_uid": os.geteuid(),
        "actor_gid": os.getegid(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", required=True, choices=tuple(SOURCE_BY_PERIOD))
    parser.add_argument("--phase", required=True, choices=PHASES)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--source-head", required=True)
    args = parser.parse_args()
    print(json.dumps(
        prepare(args.period, args.phase, args.job_id, args.source_head),
        sort_keys=True,
    ), flush=True)


if __name__ == "__main__":
    main()
