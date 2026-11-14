from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_ROOT_DIRS = {
    ".github",
    "archive",
    "config",
    "docs",
    "operations",
    "research",
    "scripts",
    "src",
    "tests",
}
EXPECTED_ROOT_FILES = {
    ".gitattributes",
    ".gitignore",
    ".python-version",
    "AGENTS.md",
    "README.md",
    "bybit_workbench.spec",
    "pyproject.toml",
    "uv.lock",
}


def _tracked_root() -> tuple[set[str], set[str]]:
    result = subprocess.run(
        ["git", "ls-tree", "HEAD"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    dirs: set[str] = set()
    files: set[str] = set()
    for line in result.stdout.splitlines():
        meta, name = line.split("\t", 1)
        object_type = meta.split()[1]
        if object_type == "tree":
            dirs.add(name)
        elif object_type == "blob":
            files.add(name)
    return dirs, files


def test_tracked_repository_root_matches_explicit_topology() -> None:
    dirs, files = _tracked_root()
    assert dirs == EXPECTED_ROOT_DIRS
    assert files == EXPECTED_ROOT_FILES
