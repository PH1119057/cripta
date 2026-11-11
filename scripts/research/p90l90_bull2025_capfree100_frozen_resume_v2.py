#!/usr/bin/env python3
"""Resume the unchanged CAPFREE100 2025 causal replay from a frozen research checkout.

The original eight historical replay modules and the capfree driver remain
byte-identical to the 35 existing SHA-chained checkpoints. This wrapper changes
only the *source location* and enforces a single immutable detached Git commit.
It never loads research Python from the rolling production source mirror.
No trading, service, DB, or Exchange mutation.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

SOURCE_REL = "scripts/research/p90l90_bull2025_capfree100_frozen_resume_v2.py"
FROZEN_PARENT = Path("/data/cripta/research/worktrees")
RESULTS = Path("/data/cripta/research/runs/p90l90_bull_2025_holdout_v1/results")
OUT = RESULTS / "portfolio_capfree100_2025_v1"
MODULES = (
    "p90l90_entry_90d_v2",
    "p90l90_l53_clean_v2",
    "p90l90_exit_90d_v2",
    "p90l90_bull2025_entry_v1",
    "p90l90_bull2025_exit_unit_v1",
    "p90l90_bull2025_portfolio_v1",
    "p90l90_bull2025_portfolio_chunked_v2",
    "p90l90_bull2025_capfree100_v1",
)


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True, capture_output=True, check=False,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )


def frozen_source(root: Path, expected: str) -> None:
    """Fail closed if the source is not a clean, detached, exact commit."""
    if os.geteuid() != 107 or os.getegid() != 109:
        raise PermissionError("only effective cripta UID107:GID109 may run research")
    if not expected or len(expected) != 40 or set(expected) - set("0123456789abcdef"):
        raise ValueError("exact 40-character Git SHA required")
    if root.is_symlink() or root.parent != FROZEN_PARENT or not root.is_dir():
        raise PermissionError("source must be a dedicated research worktree on data disk")
    head = git(root, "rev-parse", "HEAD")
    if head.returncode or head.stdout.strip() != expected:
        raise RuntimeError("frozen research source HEAD mismatch")
    branch = git(root, "symbolic-ref", "--quiet", "HEAD")
    if branch.returncode != 1:
        raise RuntimeError("research checkout must be detached, never track moving branch")
    dirty = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if dirty.returncode or dirty.stdout.strip():
        raise RuntimeError("frozen research checkout is dirty or unknown")
    if not (root / SOURCE_REL).is_file() or (root / SOURCE_REL).is_symlink():
        raise RuntimeError("approved frozen research resume wrapper missing")


def load_frozen_modules(root: Path, expected: str):
    """Bind only original byte-pinned modules to frozen checkout, not active main."""
    frozen_source(root, expected)
    modules = []
    for short in MODULES:
        # Historical modules insert source_checkout in sys.path on import.
        # Preload dependencies in causal import order; assert every resolved
        # implementation is from this immutable worktree before executing.
        sys.path.insert(0, str(root))
        mod = importlib.import_module("scripts.research." + short)
        location = Path(mod.__file__).resolve()
        if location != root / "scripts" / "research" / (short + ".py"):
            raise RuntimeError("mixed frozen/main Python module: " + short)
        modules.append(mod)
    for mod in modules:
        if hasattr(mod, "SOURCE"):
            mod.SOURCE = root

    original_exit, entry, unit, portfolio, engine, driver = (
        modules[2], modules[3], modules[4], modules[5], modules[6], modules[7]
    )
    if (driver.engine is not engine or engine.old is not portfolio
            or portfolio.entry is not entry or portfolio.unit is not unit
            or portfolio.original is not original_exit):
        raise RuntimeError("historical source module identity mismatch")

    # On every chunk/day check the *frozen* checkout, never rolling main.
    original_pin = unit.verify_source_fast_forward

    def verify_frozen(start_sha: str, pins: dict[str, str]) -> str:
        frozen_source(root, expected)
        checked = original_pin(start_sha, pins)
        if checked != expected:
            raise RuntimeError("research worktree moved during replay")
        return checked

    unit.verify_source_fast_forward = verify_frozen
    driver.configure()
    return driver, engine


def read_status() -> dict:
    return json.loads((OUT / "status.json").read_text(encoding="utf-8"))


def validate_resume(driver, engine, expected: str) -> dict:
    facts = driver.preflight(expected)
    study = json.loads((OUT / "study.json").read_text(encoding="utf-8"))
    if study["source_head_first"] != "edccf24a3eb6f51fc7384419c12d9b66880f2d80":
        raise RuntimeError("unexpected original CAPFREE100 study lineage")
    if read_status().get("stage") != "FAILED":
        raise RuntimeError("prior worker not confirmed FAILED; never duplicate")
    previous = json.loads((OUT / "launch.json").read_text(encoding="utf-8"))
    pid = int(previous["pid"])
    if pid > 0 and Path(f"/proc/{pid}").exists():
        raise RuntimeError("prior CAPFREE100 process still exists")
    state, last, _ = engine.load_parts(engine.PARTS / "long_only")
    if state is None or last is None or not (0 < state["next_day"] < 183):
        raise RuntimeError("no partial LONG_ONLY checkpoint available to resume")
    if (engine.PARTS / "short_only").exists():
        raise RuntimeError("unexpected started SHORT_ONLY; manual reconciliation")
    if list(engine.FINAL.iterdir()):
        raise RuntimeError("existing final output requires separate reconciliation")
    return {
        "status": "RESUME_PREFLIGHT_PASS_NO_MUTATION",
        "source_head": expected,
        "source_root": str(Path(__file__).resolve().parents[2]),
        "days_done": state["next_day"],
        "next_day": engine.day_date(state["next_day"]),
        "last_checkpoint_sha256": engine.old.sha(
            engine.PARTS / "long_only" / engine.part_name(state["next_day"] - 1) / "manifest.json"
        ),
        "original_driver_sha256": facts["script_sha256"],
    }


def resume(root: Path, expected: str, driver, engine) -> dict:
    proof = validate_resume(driver, engine, expected)
    launch_path = OUT / "resume_launch_v2.json"
    if launch_path.exists() or launch_path.is_symlink():
        raise FileExistsError("resume was already launched; reconcile existing job")
    import fcntl  # Linux only; keeps Windows test discovery importable

    with (OUT / "worker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("CAPFREE100 worker already owns the lock") from exc
        # Publish durable single-launch intent BEFORE spawning. The worker lock
        # is released before the child attempts to acquire its own lock.
        intent = {**proof, "status": "RESUME_LAUNCH_INTENT", "pid": None}
        with launch_path.open("x", encoding="utf-8") as handle:
            json.dump(intent, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

    with (OUT / "controller.log").open("a", encoding="utf-8") as log:
        cmd = [sys.executable, "-B", str(root / SOURCE_REL), "--worker",
               "--source-head", expected]
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True, cwd=str(root),
        )
    record = {**proof, "status": "RESUME_LAUNCHED_NOT_YET_VERIFIED",
              "pid": proc.pid, "log": str(OUT / "controller.log")}
    temporary = OUT / "resume_launch_v2.tmp"
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(record, handle, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(launch_path)
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-head", required=True)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--preflight", action="store_true")
    operation.add_argument("--resume", action="store_true")
    operation.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    driver, engine = load_frozen_modules(root, args.source_head)
    if args.preflight:
        print(json.dumps(validate_resume(driver, engine, args.source_head), sort_keys=True),
              flush=True)
    elif args.resume:
        print(json.dumps(resume(root, args.source_head, driver, engine), sort_keys=True),
              flush=True)
    else:
        driver.worker(args.source_head)


if __name__ == "__main__":
    main()
