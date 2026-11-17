from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from bybit_workbench.universal_entry import p90l90_real_source_compatibility as compat

DIGEST = "b0707d2a116e6b725c2c27fa82dc62b997477bf5657d110eaab367e6282bb11e"
HISTORICAL = (
    "d11dc60b3be541bd0dc6c3a5a45d65fbb0e182ae",
    "bcd8a0a7b465619c4ef2c7128d85b8564b79e64f",
)
TARGET = "599ae464339f865996a0506c138b5c0ce6d69d5b"


def _git_has(commit: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(_git_root()), "cat-file", "-e", commit + "^{commit}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def _git_root() -> Path:
    root = Path.cwd()
    if (root / ".git").is_dir():
        return root
    worktrees = subprocess.check_output(
        ["git", "worktree", "list", "--porcelain"],
        text=True,
    ).splitlines()
    roots = [
        Path(line.removeprefix("worktree "))
        for line in worktrees
        if line.startswith("worktree ")
    ]
    return next(
        (candidate for candidate in roots if (candidate / ".git").is_dir()),
        root,
    )


def _git_digest(commit: str) -> str:
    root = _git_root()
    inventory = compat._consumer_inventory_from_git(root, commit)
    digest = hashlib.sha256(compat.CONTRACT_VERSION.encode())
    for relative in sorted(inventory):
        digest.update(relative.encode() + b"\0")
        digest.update(hashlib.sha256(inventory[relative]).digest())
    return digest.hexdigest()


@pytest.mark.parametrize("release", HISTORICAL)
def test_exact_b070_release_is_historical_and_git_bound(release: str) -> None:
    assert compat._REVIEWED_HISTORICAL_CONSUMERS[release] == DIGEST
    if not _git_has(release):
        pytest.skip("full Git history required for immutable historical digest proof")
    assert _git_digest(release) == DIGEST


def test_new_824_target_is_not_relabelled_as_historical() -> None:
    assert TARGET not in compat._REVIEWED_HISTORICAL_CONSUMERS


def test_unknown_release_never_inherits_b070_class() -> None:
    assert "0" * 40 not in compat._REVIEWED_HISTORICAL_CONSUMERS
