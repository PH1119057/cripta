#!/usr/bin/env python3
"""#865: exact Git-archive runtime candidate smoke, NO release/production mutation.

Bounded server rehearsal under Unix cripta on the Data SSD. The official
production installer remains the only owner of /srv/cripta/runtime/releases,
systemd, PostgreSQL production migrations and current symlinks.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

_REPO = Path("/data/cripta/research/worktrees/p90_causal_c2_865_20261119")
_PARENT = Path("/data/cripta/research/testenvs/p90_causal_candidate_865")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_FILES = (
    "src", "operations", "config",
    "research/server/backup", "research/server/connectivity",
    "research/server/monitoring", "research/server/control", "pyproject.toml",
    "tests/test_p90_causal_release_preflight_865.py",
    "tests/test_p90_causal_pg16_865.py",
    "tests/postgres_trade_lifecycle_bootstrap.sql",
    ".github/workflows/trade-lifecycle-postgres.yml",
)


class CandidateBlocked(RuntimeError):
    pass


def _checked(command: list[str], *, timeout: int = 120, env=None):
    completed = subprocess.run(
        command, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, check=False, timeout=timeout,
    )
    if completed.returncode:
        raise CandidateBlocked(
            "CHILD_FAILED:" + Path(command[0]).name
            + ":rc=" + str(completed.returncode)
        )
    return completed.stdout.strip()


def _path_owned(path: Path, *, create: bool = False):
    if path.is_symlink():
        raise CandidateBlocked("SYMLINK_DENIED")
    if create and not path.exists():
        path.mkdir(mode=0o700)
    if not path.is_dir():
        raise CandidateBlocked("EXPECTED_DIRECTORY_MISSING")
    stat = path.stat()
    if stat.st_uid != os.geteuid() or stat.st_mode & 0o077:
        raise CandidateBlocked("EXPECTED_PRIVATE_OWNER_MODE")


def _archive(candidate: Path, sha: str):
    git = subprocess.Popen(
        ["git", "-C", str(_REPO), "archive", "--format=tar", sha, "--", *_FILES],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        tar = subprocess.run(
            ["tar", "--no-same-owner", "-xf", "-", "-C", str(candidate)],
            stdin=git.stdout, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, timeout=80,
        )
        if git.stdout:
            git.stdout.close()
        git_err = git.stderr.read() if git.stderr else b""
        git_rc = git.wait(timeout=15)
        if git_rc or tar.returncode:
            raise CandidateBlocked(
                "GIT_ARCHIVE_OR_TAR_FAILED:"
                + str(git_rc) + ":" + str(tar.returncode)
                + ":stderr_bytes=" + str(len(git_err) + len(tar.stderr))
            )
    finally:
        if git.poll() is None:
            git.kill()
            git.wait()


def _verify_archive(candidate: Path, sha: str):
    cmd = [
        sys.executable,
        str(_REPO / "operations/infrastructure/verify_release_candidate_git.py"),
        "--repo", str(_REPO), "--commit", sha,
        "--candidate", str(candidate), "--label", "runtime",
    ]
    for root in _FILES:
        cmd.extend(("--path", root))
    output = _checked(cmd, timeout=100)
    if "RELEASE_CANDIDATE_GIT_VERIFY=PASS" not in output:
        raise CandidateBlocked("GIT_BLOB_SET_NOT_VERIFIED")
    print(output, flush=True)


def _run_candidate(candidate: Path, sha: str):
    python = candidate / ".venv/bin/python"
    lock = candidate / "operations/runtime/runtime_requirements.lock"
    if not lock.is_file() or lock.is_symlink():
        raise CandidateBlocked("DEPENDENCY_HASH_LOCK_MISSING")
    print("CANDIDATE_VENV=BUILDING", flush=True)
    _checked(
        ["/usr/bin/python3", "-m", "venv", "--system-site-packages",
         str(candidate / ".venv")],
        timeout=100,
    )
    _checked(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check",
         "--no-cache-dir", "--only-binary=:all:", "--require-hashes",
         "-r", str(lock)],
        timeout=400,
    )
    imports = _checked(
        [str(python), "-c",
         "import pydantic,psycopg,sqlalchemy,websocket;"
         "import bybit_workbench.universal_exit.engine;"
         "assert pydantic.__version__=='2.13.5';"
         "print('CANDIDATE_IMPORTS=PASS')"],
        env={**os.environ, "PYTHONPATH": str(candidate / "src"),
             "PYTHONDONTWRITEBYTECODE": "1"},
        timeout=30,
    )
    print(imports, flush=True)
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("PG") and k not in {"PYTHONPATH", "PYTHONHOME"}
    }
    env["PYTHONPATH"] = str(candidate) + ":" + str(candidate / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [
        str(python), "-B",
        str(candidate / "operations/infrastructure/p90_causal_release_preflight.py"),
        "--candidate-root", str(candidate), "--expected-commit", sha,
        "--staged-candidate",
    ]
    print("CANDIDATE_C0_C1_C2=RUNNING", flush=True)
    output = _checked(command, env=env, timeout=110)
    print(output[-9000:], flush=True)
    if "P90_CANDIDATE_PREFLIGHT=PASS_OFFLINE_ARCHIVE_C0_C1_C2" not in output:
        raise CandidateBlocked("CANDIDATE_PREFLIGHT_NOT_PROVEN")
    return output


def run(sha: str) -> int:
    started = datetime.now(UTC)
    run_id = started.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:10]
    manifest = {
        "id": run_id, "expected_commit": sha,
        "started_utc": started.isoformat(), "state": "UNKNOWN",
        "mode": "OFFLINE_GIT_ARCHIVE_VERIFIED_RUNTIME_REHEARSAL",
        "prod_symlink_changed": False, "prod_services_changed": False,
        "prod_db_changed": False, "exchange_mutation": False,
    }
    previous_mask = os.umask(0o077)
    try:
        if os.geteuid() != 107:
            raise CandidateBlocked("ACTOR_NOT_CRIPTA")
        if not _SHA.fullmatch(sha):
            raise CandidateBlocked("COMMIT_SHA_INVALID")
        if _REPO.is_symlink() or not _REPO.is_dir():
            raise CandidateBlocked("WORKTREE_INVALID")
        head = _checked(
            ["git", "-C", str(_REPO), "rev-parse", "HEAD"], timeout=20,
        )
        dirty = _checked(
            ["git", "-C", str(_REPO), "status", "--porcelain=v1"], timeout=20,
        )
        if head != sha or dirty:
            raise CandidateBlocked("WORKTREE_SHA_OR_CLEAN_MISMATCH")
        _path_owned(_PARENT.parent)
        _path_owned(_PARENT, create=True)
        if shutil.disk_usage(_PARENT).free < 2 * 1024**3:
            raise CandidateBlocked("DATA_DISK_FREE_LT_2G")
        with (_PARENT / ".build.lock").open("a+") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CandidateBlocked("CANDIDATE_BUILD_CONCURRENT") from exc
            candidate = _PARENT / sha
            if candidate.exists() or candidate.is_symlink():
                raise CandidateBlocked("CANDIDATE_ALREADY_EXISTS_RECONCILE")
            _path_owned(candidate, create=True)
            print("CANDIDATE_ARCHIVE=START exact_sha=" + sha, flush=True)
            _archive(candidate, sha)
            _verify_archive(candidate, sha)
            (candidate / "INSTALLED_COMMIT").write_text(sha + "\n")
            print("CANDIDATE_ARCHIVE_HASHES=PASS", flush=True)
            _run_candidate(candidate, sha)
            manifest["state"] = "PASS"
            manifest["candidate_root"] = str(candidate)
            print("CANDIDATE_OFFLINE_END_TO_END=PASS", flush=True)
    except Exception as exc:
        manifest["state"] = "FAIL"
        manifest["reason"] = (
            str(exc) if isinstance(exc, CandidateBlocked)
            else "UNCLASSIFIED_" + type(exc).__name__
        )
        print("CANDIDATE_OFFLINE_END_TO_END=FAIL:" + manifest["reason"], flush=True)
        return 2
    finally:
        manifest["finished_utc"] = datetime.now(UTC).isoformat()
        if _PARENT.is_dir() and _PARENT.stat().st_uid == os.geteuid():
            runs = _PARENT / "runs"
            _path_owned(runs, create=True)
            final = runs / (run_id + ".json")
            tmp = final.with_suffix(".tmp")
            tmp.write_text(json.dumps(manifest, sort_keys=True, indent=2))
            os.replace(tmp, final)
            print("CANDIDATE_MANIFEST=" + str(final), flush=True)
        os.umask(previous_mask)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-commit", required=True)
    options = parser.parse_args(argv)
    return run(options.expected_commit)


if __name__ == "__main__":
    raise SystemExit(main())
