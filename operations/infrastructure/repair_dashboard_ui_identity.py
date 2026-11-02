#!/usr/bin/env python3
"""Fail-closed repair of the independent Dashboard UI identity ledger."""

from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from datetime import UTC, datetime

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DASHBOARD_FILES = (
    "operations/dashboard/index.html",
    "operations/dashboard/app.py",
)


def require_sha(value: str, label: str) -> str:
    value = value.strip().lower()
    if not SHA_RE.fullmatch(value):
        raise RuntimeError(f"{label}_INVALID")
    return value


def read_sha(path: Path, label: str) -> str:
    if not path.is_file():
        raise RuntimeError(f"{label}_MISSING")
    return require_sha(path.read_text(encoding="utf-8"), label)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def run_git(source: Path, *args: str) -> str:
    command = [
        "runuser",
        "-u",
        "cripta",
        "--",
        "env",
        "GIT_OPTIONAL_LOCKS=0",
        "git",
        "-C",
        str(source),
        *args,
    ]
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def git_bytes(source: Path, commit: str, relpath: str) -> bytes:
    command = [
        "runuser",
        "-u",
        "cripta",
        "--",
        "env",
        "GIT_OPTIONAL_LOCKS=0",
        "git",
        "-C",
        str(source),
        "show",
        f"{commit}:{relpath}",
    ]
    return subprocess.run(command, check=True, capture_output=True).stdout


def atomic_write_identity(path: Path, sha: str, *, uid: int, gid: int) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(sha + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(tmp, uid, gid)
        os.chmod(tmp, 0o640)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("check", "repair"), required=True)
    parser.add_argument("--expected-current", required=True)
    parser.add_argument("--expected-state", required=True)
    parser.add_argument("--expected-runtime", required=True)
    parser.add_argument(
        "--source",
        default=os.environ.get("CRIPTA_SOURCE_CHECKOUT", "/srv/cripta/source_checkout"),
    )
    parser.add_argument(
        "--ui-root",
        default=os.environ.get("CRIPTA_DASHBOARD_UI_ROOT", "/srv/cripta/dashboard-ui"),
    )
    parser.add_argument(
        "--runtime-root",
        default=os.environ.get("CRIPTA_RUNTIME_ROOT", "/srv/cripta/runtime"),
    )
    parser.add_argument(
        "--state-root",
        default=os.environ.get("CRIPTA_RELEASE_STATE_ROOT", "/var/lib/cripta/release"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("ROOT_REQUIRED")

    expected_current = require_sha(args.expected_current, "EXPECTED_CURRENT")
    expected_state = require_sha(args.expected_state, "EXPECTED_STATE")
    expected_runtime = require_sha(args.expected_runtime, "EXPECTED_RUNTIME")

    source = Path(args.source)
    ui_root = Path(args.ui_root)
    runtime_root = Path(args.runtime_root)
    state_root = Path(args.state_root)

    source_head = require_sha(run_git(source, "rev-parse", "HEAD"), "SOURCE_HEAD")
    remote_line = run_git(source, "ls-remote", "origin", "refs/heads/main")
    remote_main = require_sha(remote_line.split()[0], "REMOTE_MAIN")
    if source_head != remote_main:
        raise RuntimeError("SOURCE_REMOTE_MAIN_DRIFT")
    if run_git(source, "status", "--porcelain=v1"):
        raise RuntimeError("SOURCE_DIRTY")
    subprocess.run(
        [
            "runuser",
            "-u",
            "cripta",
            "--",
            "env",
            "GIT_OPTIONAL_LOCKS=0",
            "git",
            "-C",
            str(source),
            "merge-base",
            "--is-ancestor",
            expected_current,
            source_head,
        ],
        check=True,
    )

    current_link = ui_root / "current"
    current_resolved = current_link.resolve(strict=True)
    expected_release = (ui_root / "releases" / expected_current).resolve(strict=True)
    if current_resolved != expected_release:
        raise RuntimeError("CURRENT_UI_RELEASE_MISMATCH")

    embedded = read_sha(current_resolved / "DASHBOARD_UI_COMMIT", "EMBEDDED_UI_COMMIT")
    if embedded != expected_current:
        raise RuntimeError("EMBEDDED_UI_COMMIT_MISMATCH")

    runtime_commit = read_sha(runtime_root / "current" / "INSTALLED_COMMIT", "RUNTIME_COMMIT")
    if runtime_commit != expected_runtime:
        raise RuntimeError("RUNTIME_COMMIT_MISMATCH")

    hashes: dict[str, str] = {}
    for relpath in DASHBOARD_FILES:
        live_path = current_resolved / Path(relpath).name
        if not live_path.is_file():
            raise RuntimeError(f"UI_FILE_MISSING:{relpath}")
        git_hash = sha256_bytes(git_bytes(source, expected_current, relpath))
        live_hash = sha256_file(live_path)
        if git_hash != live_hash:
            raise RuntimeError(f"UI_GIT_BYTES_MISMATCH:{relpath}")
        hashes[relpath] = live_hash

    state_file = state_root / "DASHBOARD_UI_COMMIT"
    state_commit = read_sha(state_file, "STATE_UI_COMMIT")
    if state_commit == expected_current:
        print("DASHBOARD_UI_IDENTITY_STATE=ALREADY_CORRECT")
        print(f"DASHBOARD_UI_COMMIT={expected_current}")
        return 0
    if state_commit != expected_state:
        raise RuntimeError("STATE_UI_COMMIT_UNEXPECTED")
    if args.mode == "check":
        print("DASHBOARD_UI_IDENTITY_REPAIR_REQUIRED=YES")
        print(f"STATE_UI_COMMIT={state_commit}")
        print(f"PHYSICAL_UI_COMMIT={expected_current}")
        return 0

    gid = grp.getgrnam("cripta").gr_gid
    atomic_write_identity(state_file, expected_current, uid=0, gid=gid)
    evidence = {
        "repaired_at": datetime.now(UTC).isoformat(),
        "previous_dashboard_ui_commit": state_commit,
        "dashboard_ui_commit": expected_current,
        "runtime_commit": runtime_commit,
        "source_head": source_head,
        "remote_main": remote_main,
        "files": hashes,
    }
    evidence_path = state_root / "DASHBOARD_UI_IDENTITY_REPAIR.json"
    tmp_evidence = evidence_path.with_name(
        f".{evidence_path.name}.{os.getpid()}.tmp"
    )
    try:
        with tmp_evidence.open("w", encoding="utf-8") as handle:
            json.dump(evidence, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(tmp_evidence, 0, gid)
        os.chmod(tmp_evidence, 0o640)
        os.replace(tmp_evidence, evidence_path)
    finally:
        if tmp_evidence.exists():
            tmp_evidence.unlink()

    if read_sha(state_file, "STATE_UI_COMMIT_AFTER") != expected_current:
        raise RuntimeError("STATE_UI_COMMIT_REPAIR_NOT_PERSISTED")
    print("DASHBOARD_UI_IDENTITY_REPAIR=PASS")
    print(f"DASHBOARD_UI_COMMIT={expected_current}")
    print(f"PREVIOUS_DASHBOARD_UI_COMMIT={state_commit}")
    print("TRADING_STATE_MUTATION=NO")
    print("SERVICE_RESTART=NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
