#!/usr/bin/env python3
"""Verify a freshly built release candidate against exact Git blobs.

Read-only verifier. It never mutates Git, runtime symlinks, DB, services,
permissions, trading state, or Exchange state.
"""
from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")


class CandidateIntegrityError(RuntimeError):
    pass


def _git(repo: Path, *args: str, binary: bool = False):
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise CandidateIntegrityError("GIT_COMMAND_FAILED")
    return proc.stdout if binary else proc.stdout.decode("utf-8").strip()


def _expected(repo: Path, commit: str, roots: tuple[str, ...]):
    raw = _git(repo, "ls-tree", "-r", "-z", commit, "--", *roots, binary=True)
    result: dict[str, tuple[str, str]] = {}
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            meta, path_raw = item.split(b"\t", 1)
            mode_raw, kind_raw, oid_raw = meta.split(b" ", 2)
            mode = mode_raw.decode("ascii")
            kind = kind_raw.decode("ascii")
            oid = oid_raw.decode("ascii")
            path = path_raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise CandidateIntegrityError("GIT_MANIFEST_INVALID") from None
        if kind != "blob" or mode not in {"100644", "100755", "120000"}:
            raise CandidateIntegrityError("GIT_MANIFEST_TYPE_UNSUPPORTED")
        if path in result or path.startswith("/") or ".." in Path(path).parts:
            raise CandidateIntegrityError("GIT_MANIFEST_PATH_INVALID")
        result[path] = (mode, oid)
    if not result:
        raise CandidateIntegrityError("GIT_MANIFEST_EMPTY")
    return result


def _actual_paths(candidate: Path) -> set[str]:
    found: set[str] = set()
    for root, dirs, files in os.walk(candidate, followlinks=False):
        root_path = Path(root)
        for name in tuple(dirs):
            path = root_path / name
            if path.is_symlink():
                found.add(path.relative_to(candidate).as_posix())
                dirs.remove(name)
        for name in files:
            path = root_path / name
            found.add(path.relative_to(candidate).as_posix())
    return found


def verify(repo: Path, commit: str, candidate: Path, roots: tuple[str, ...]) -> int:
    if (
        not repo.is_dir()
        or not candidate.is_dir()
        or candidate.is_symlink()
        or SHA.fullmatch(commit) is None
        or not roots
    ):
        raise CandidateIntegrityError("ARGUMENT_OR_PATH_INVALID")
    resolved = _git(repo, "rev-parse", f"{commit}^{{commit}}")
    if resolved != commit:
        raise CandidateIntegrityError("COMMIT_IDENTITY_INVALID")
    expected = _expected(repo, commit, roots)
    if _actual_paths(candidate) != set(expected):
        raise CandidateIntegrityError("CANDIDATE_PATH_SET_MISMATCH")

    for relative, (mode, oid) in expected.items():
        path = candidate / relative
        if mode == "120000":
            if not path.is_symlink():
                raise CandidateIntegrityError("CANDIDATE_BLOB_TYPE_MISMATCH")
            target = _git(repo, "cat-file", "blob", oid)
            if os.readlink(path) != target:
                raise CandidateIntegrityError("CANDIDATE_SYMLINK_BLOB_MISMATCH")
            continue
        if not path.is_file() or path.is_symlink():
            raise CandidateIntegrityError("CANDIDATE_BLOB_TYPE_MISMATCH")
        actual = _git(repo, "hash-object", "--no-filters", str(path))
        if actual != oid:
            raise CandidateIntegrityError("CANDIDATE_GIT_BLOB_MISMATCH")
        executable = bool(path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
        if executable != (mode == "100755"):
            raise CandidateIntegrityError("CANDIDATE_GIT_MODE_MISMATCH")
    return len(expected)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--label", required=True, choices=("runtime", "tooling"))
    parser.add_argument("--path", action="append", dest="paths", required=True)
    args = parser.parse_args()
    try:
        count = verify(
            Path(args.repo),
            args.commit.strip().lower(),
            Path(args.candidate),
            tuple(args.paths),
        )
    except CandidateIntegrityError as exc:
        print(f"RELEASE_CANDIDATE_GIT_VERIFY=BLOCKED label={args.label} reason={exc}")
        return 2
    print(
        f"RELEASE_CANDIDATE_GIT_VERIFY=PASS label={args.label} "
        f"commit={args.commit.strip().lower()} blobs={count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
