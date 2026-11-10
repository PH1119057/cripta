#!/usr/bin/env python3
"""Durable, owner-authorized, single-process 2025 SHORT-only research controller.

This is NOT a trading/runtime service. It launches exactly one detached cripta
worker, outside the SentinelX per-exec wall clock, and repeatedly invokes the
already-proven five-UTC-day portfolio checkpoint writer. Every individual day
remains immutable and SHA-chained. A failed batch stops the controller without
destroying previously committed days. This script never starts LONG or LIVE.

The system does not silently auto-restart after failure/reboot. The completed
daily checkpoints are restartable only after a fresh explicit launch/preflight.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from datetime import UTC, datetime
from pathlib import Path

SOURCE = Path("/srv/cripta/source_checkout")
sys.path.insert(0, str(SOURCE))
from scripts.research import p90l90_bull2025_portfolio_chunked_v2 as chunk  # noqa: E402

CONTROLLER_REL = "scripts/research/p90l90_bull2025_short_autonomous_v1.py"
CTRL_PARENT = chunk.old.RESULTS / "portfolio_controller_2025_v1"
CTRL = CTRL_PARENT / "short_only"
CHECKPOINTS = chunk.PARTS / "short_only"
FINAL = chunk.FINAL / "short_only"
LOCK = CTRL / "controller.lock"
STATUS = CTRL / "status.json"
LOG = CTRL / "controller.log"
LAUNCH = CTRL / "launch.json"
BATCH_DAYS = 5
SIDE = "SHORT_ONLY"
ID = "P90L90_BULL2025_SHORT_AUTONOMOUS_V1"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, value: dict) -> None:
    """Complete JSON via write/fsync/rename; no incomplete status is trusted."""
    fd, tmp_name = tempfile.mkstemp(prefix=".status_pending_", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(value, f, sort_keys=True, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        chunk.fsync_dir(path.parent)
    finally:
        if tmp.exists():
            tmp.unlink()


def snapshot() -> dict:
    state, manifest, digest = chunk.load_parts(CHECKPOINTS)
    if state is None and manifest is None:
        # First SHORT run: there are no checkpoints yet. The existing
        # daily writer creates the first immutable day on the next batch.
        return {
            "completed_days": 0, "total_days": chunk.TOTAL_DAYS,
            "percentage": 0, "last_day": None,
            "next_day": chunk.day_date(0), "native_ticks": 0,
            "last_manifest_sha256": None, "source_head_first": None,
            "source_pins_sha256": None,
            "final_result_published": (FINAL / "result.json").is_file(),
        }
    if state is None or manifest is None:
        raise ValueError("partial SHORT checkpoint state/manifest missing")
    chunk.pin_current(manifest["source_pin_sha256"],
                      manifest["source_head_first"])
    days = state["next_day"]
    if not 0 <= days <= chunk.TOTAL_DAYS:
        raise ValueError("2025 checkpoint day count out of range")
    if len(state["books"]) != 16:
        raise ValueError("2025 checkpoint 16-book state missing")
    return {
        "completed_days": days,
        "total_days": chunk.TOTAL_DAYS,
        "percentage": round(100 * days / chunk.TOTAL_DAYS, 4),
        "last_day": manifest["day"],
        "next_day": chunk.day_date(days) if days < chunk.TOTAL_DAYS else None,
        "native_ticks": state["ticks"],
        "last_manifest_sha256": digest,
        "source_head_first": manifest["source_head_first"],
        "source_pins_sha256": manifest["source_pin_sha256"],
        "final_result_published": (FINAL / "result.json").is_file(),
    }


def validate_actor_and_path() -> None:
    if os.geteuid() != 107 or os.getegid() != 109:
        raise PermissionError("only actual cripta UID107:GID109 may write research")
    # New SHORT checkpoint directory is created only by the original
    # authenticated writer under the existing cripta-owned parts parent.
    paths = (chunk.ROOT, chunk.old.RESULTS, chunk.PARTS)
    if CHECKPOINTS.exists() or CHECKPOINTS.is_symlink():
        paths += (CHECKPOINTS,)
    for path in paths:
        if (path.is_symlink() or not path.is_dir()
                or path.stat().st_uid != 107
                or not os.access(path, os.W_OK | os.X_OK)):
            raise PermissionError(f"2025 research actor/path WRITE denied: {path}")
    if shutil.disk_usage(chunk.ROOT).free < 16 * 2**30:
        raise RuntimeError("research disk below 16GiB free reserve")
    if CTRL_PARENT.is_symlink() or CTRL.is_symlink():
        raise PermissionError("controller research path may not be symlink")


def ensure_controller_dirs() -> None:
    chunk.writer_dir(CTRL_PARENT)
    chunk.writer_dir(CTRL)


def active_portfolio_command(raw: bytes) -> bool:
    if b"python3 -c" in raw:
        return False
    # The sudo launcher's argv mentions the controller file but it is NOT
    # another research worker. Only a real detached --worker is counted.
    return (
        b"/scripts/research/p90l90_bull2025_portfolio_" in raw
        or (
            b" --worker" in raw
            and (
                b"p90l90_bull2025_long_autonomous_v1.py" in raw
                or b"p90l90_bull2025_short_autonomous_v1.py" in raw
            )
        )
    )


def active_portfolio_workers() -> list[int]:
    """Do not start a controller next to an earlier manual research replay."""
    workers = []
    for p in Path("/proc").iterdir():
        if not p.name.isdecimal() or int(p.name) == os.getpid():
            continue
        try:
            raw = (p / "cmdline").read_bytes().replace(b"\0", b" ")
        except OSError:
            continue
        if active_portfolio_command(raw):
            workers.append(int(p.name))
    return workers


def status(stage: str, launched_at: str, **extra) -> dict:
    record = {
        "controller": ID, "stage": stage,
        "actor_uid": os.geteuid(), "pid": os.getpid(),
        "started_utc": launched_at, "updated_utc": utc_now(),
        "source_head_current": chunk.old.entry.git_head(),
        "controller_sha256": chunk.old.sha(SOURCE / CONTROLLER_REL),
        "batch_days": BATCH_DAYS, "side": SIDE,
        "checkpoint": snapshot(), **extra,
    }
    atomic_json(STATUS, record)
    print(json.dumps({
        "controller_stage": stage, "pid": os.getpid(),
        "checkpoint": record["checkpoint"], "updated_utc": record["updated_utc"],
    }, sort_keys=True), flush=True)
    return record


def launch(source_head: str) -> None:
    validate_actor_and_path()
    if chunk.old.entry.git_head() != source_head:
        raise RuntimeError("server source HEAD changed before controller launch")
    before = snapshot()
    if before["completed_days"] >= chunk.TOTAL_DAYS or FINAL.exists():
        raise FileExistsError("full 2025 SHORT already committed or finalized")
    # This only writes under data-disk research; process remains UID107.
    ensure_controller_dirs()
    lock = LOCK.open("a+", encoding="utf-8")
    flock = importlib.import_module("fcntl")  # Linux host only
    try:
        flock.flock(lock.fileno(), flock.LOCK_EX | flock.LOCK_NB)
    except BlockingIOError as exc:
        lock.close()
        raise RuntimeError("2025 SHORT autonomous controller already active") from exc
    try:
        workers = active_portfolio_workers()
        if workers:
            raise RuntimeError(f"another P90L90 2025 worker active: {workers}")
        started = utc_now()
        # Leave a bounded restart identity, even if the worker dies before
        # writing its first heartbeat. Only one process may own LOCK.
        atomic_json(LAUNCH, {
            "controller": ID, "launched_utc": started,
            "source_head": source_head, "initial_checkpoint": before,
            "controller_sha256": chunk.old.sha(SOURCE / CONTROLLER_REL),
            "expected_actor": "cripta:cripta UID107:GID109",
            "output": str(CTRL),
        })
        with LOG.open("a", encoding="utf-8", newline="\n") as out:
            command = [
                sys.executable, "-u", "-B",
                str(SOURCE / CONTROLLER_REL),
                "--worker", "--source-head", source_head,
                "--lock-fd", str(lock.fileno()),
                "--started-at", started,
            ]
            child = subprocess.Popen(
                command, cwd=str(SOURCE), stdin=subprocess.DEVNULL,
                stdout=out, stderr=subprocess.STDOUT, close_fds=True,
                pass_fds=(lock.fileno(),), start_new_session=True,
            )
        print(json.dumps({
            "status": "LAUNCHED_NOT_YET_VERIFIED", "pid": child.pid,
            "controller": ID, "source_head": source_head,
            "initial_days": before["completed_days"],
            "state_file": str(STATUS), "log_file": str(LOG),
            "launch_manifest": str(LAUNCH), "pid_needs_host_confirmation": True,
        }, sort_keys=True), flush=True)
    finally:
        lock.close()


def worker(source_head: str, lock_fd: int, started_at: str) -> None:
    # The launcher passed the SAME locked open-file description across exec.
    # It is held until this worker exits, then the OS releases it.
    if os.fstat(lock_fd).st_ino != LOCK.stat().st_ino:
        raise PermissionError("inherited research controller lock inode differs")
    if os.fstat(lock_fd).st_dev != LOCK.stat().st_dev:
        raise PermissionError("inherited research controller lock device differs")
    flock = importlib.import_module("fcntl")  # Linux host only
    flock.flock(lock_fd, flock.LOCK_EX | flock.LOCK_NB)
    validate_actor_and_path()
    if chunk.old.entry.git_head() != source_head:
        raise RuntimeError("source HEAD changed during controller startup")
    initial = snapshot()
    if initial["completed_days"] >= chunk.TOTAL_DAYS:
        raise FileExistsError("controller cannot replace finalized 2025 job")
    max_batches = (chunk.TOTAL_DAYS - initial["completed_days"] + BATCH_DAYS - 1) // BATCH_DAYS
    # At most one extra finalization-only call after the 183rd checkpoint.
    try:
        for batch_no in range(1, max_batches + 2):
            before = snapshot()
            if before["final_result_published"]:
                status("COMPLETED_INTERNAL_QC_PENDING_INDEPENDENT", started_at,
                       last_batch=batch_no-1)
                return
            if shutil.disk_usage(chunk.ROOT).free < 16 * 2**30:
                raise RuntimeError("research data free below reserved 16GiB")
            if chunk.old.sha(SOURCE / CONTROLLER_REL) != (
                json.loads(LAUNCH.read_text(encoding="utf-8"))["controller_sha256"]
            ):
                raise RuntimeError("effective autonomous controller source bytes changed")
            live_head = chunk.old.entry.git_head()
            status("BATCH_RUNNING", started_at,
                   batch_number=batch_no, previous_days=before["completed_days"])
            outcome = chunk.run(SIDE, live_head, max_days=BATCH_DAYS)
            after = snapshot()
            if after["completed_days"] < before["completed_days"]:
                raise RuntimeError("checkpoint went backwards")
            if outcome["status"] == "INTERNAL_QC_PASS_PENDING_INDEPENDENT":
                if not after["final_result_published"]:
                    raise ValueError("no completed 2025 result manifest on disk")
                status("COMPLETED_INTERNAL_QC_PENDING_INDEPENDENT", started_at,
                       last_batch=batch_no)
                return
            if outcome["status"] != "PARTIAL_DURABLE_RESUME_READY_NOT_FINAL":
                raise ValueError("unknown original checkpoint writer result status")
            if after["completed_days"] != min(
                chunk.TOTAL_DAYS, before["completed_days"] + BATCH_DAYS
            ):
                raise ValueError("bounded research checkpoint did not advance fully")
            status("BATCH_COMMITTED", started_at, last_batch=batch_no)
        raise RuntimeError("2025 bounded batch count exhausted")
    except BaseException:
        err = traceback.format_exc()
        try:
            status("FAILED_STOPPED_CHECKPOINTS_PRESERVED", started_at,
                   failure=err[-10000:])
        except Exception as status_error:
            # A corrupt checkpoint must not leave a stale RUNNING status.
            # Preserve the failure without treating partial state as verified.
            try:
                atomic_json(STATUS, {
                    "controller": ID,
                    "stage": "FAILED_STOPPED_CHECKPOINTS_UNVERIFIED",
                    "pid": os.getpid(), "actor_uid": os.geteuid(),
                    "started_utc": started_at, "updated_utc": utc_now(),
                    "source_head_at_failure": chunk.old.entry.git_head(),
                    "checkpoint_verified": False,
                    "failure": err[-10000:],
                    "status_failure": str(status_error),
                })
            except Exception:
                print("controller status write failed", file=sys.stderr, flush=True)
        print(err, file=sys.stderr, flush=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--launch", action="store_true")
    group.add_argument("--worker", action="store_true")
    parser.add_argument("--source-head", required=True)
    parser.add_argument("--lock-fd", type=int)
    parser.add_argument("--started-at")
    args = parser.parse_args()
    if len(args.source_head) != 40 or any(c not in "0123456789abcdef" for c in args.source_head):
        raise ValueError("expected exact 40-hex GitHub main SHA")
    if args.launch:
        if args.lock_fd is not None or args.started_at is not None:
            raise ValueError("launcher cannot inherit worker-only options")
        launch(args.source_head)
    else:
        if args.lock_fd is None or not args.started_at:
            raise ValueError("worker requires inherited exclusive research lock")
        worker(args.source_head, args.lock_fd, args.started_at)


if __name__ == "__main__":
    main()
