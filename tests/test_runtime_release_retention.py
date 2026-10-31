from __future__ import annotations

from pathlib import Path

import pytest

from operations.infrastructure.runtime_release_retention import (
    RuntimeReleaseRetentionError,
    prune_runtime_releases,
    validate_current,
)

CURRENT = "a" * 40
PREVIOUS = "b" * 40
STALE_1 = "c" * 40
STALE_2 = "d" * 40


def _release(root: Path, sha: str) -> Path:
    path = root / sha
    path.mkdir()
    (path / "INSTALLED_COMMIT").write_text(sha + "\n", encoding="ascii")
    return path


def test_runtime_retention_keeps_exact_current_and_previous(tmp_path: Path) -> None:
    root = tmp_path / "runtime" / "releases"
    root.mkdir(parents=True)
    current = _release(root, CURRENT)
    previous = _release(root, PREVIOUS)
    _release(root, STALE_1)
    _release(root, STALE_2)

    pruned = prune_runtime_releases(
        root=root,
        current_link=current,
        current_sha=CURRENT,
        previous_sha=PREVIOUS,
    )

    assert set(pruned) == {STALE_1, STALE_2}
    assert {path.name for path in root.iterdir()} == {CURRENT, PREVIOUS}
    assert current.is_dir()
    assert previous.is_dir()


def test_runtime_retention_blocks_unexpected_entry_without_pruning(tmp_path: Path) -> None:
    root = tmp_path / "runtime" / "releases"
    root.mkdir(parents=True)
    current = _release(root, CURRENT)
    _release(root, PREVIOUS)
    stale = _release(root, STALE_1)
    unexpected = root / "README.txt"
    unexpected.write_text("not a release\n", encoding="utf-8")

    with pytest.raises(RuntimeReleaseRetentionError, match="unexpected runtime release entry"):
        prune_runtime_releases(
            root=root,
            current_link=current,
            current_sha=CURRENT,
            previous_sha=PREVIOUS,
        )

    assert stale.is_dir()
    assert unexpected.is_file()


def test_runtime_retention_requires_exact_current_identity(tmp_path: Path) -> None:
    root = tmp_path / "runtime" / "releases"
    root.mkdir(parents=True)
    current = _release(root, CURRENT)
    previous = _release(root, PREVIOUS)

    validate_current(root=root, current_link=current, expected_sha=CURRENT)

    with pytest.raises(RuntimeReleaseRetentionError, match="runtime current mismatch"):
        validate_current(root=root, current_link=previous, expected_sha=CURRENT)


def test_runtime_retention_requires_distinct_previous_generation(tmp_path: Path) -> None:
    root = tmp_path / "runtime" / "releases"
    root.mkdir(parents=True)
    current = _release(root, CURRENT)

    with pytest.raises(RuntimeReleaseRetentionError, match="distinct releases"):
        prune_runtime_releases(
            root=root,
            current_link=current,
            current_sha=CURRENT,
            previous_sha=CURRENT,
        )
