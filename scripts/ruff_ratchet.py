from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASELINE_COMMIT = "ad506e186b2dce57edf327a2d5db1fd4398e765a"
CONFIG = ROOT / "pyproject.toml"

Fingerprint = tuple[str, str, str, str]


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
        encoding="utf-8",
    )


def _relative_filename(filename: str, root: Path) -> str:
    path = Path(filename)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(root.resolve())
        except ValueError:
            return path.as_posix()
    return path.as_posix()


def _source_line(root: Path, relative: str, row: int) -> str:
    path = root / Path(relative)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ""
    if row < 1 or row > len(lines):
        return ""
    return lines[row - 1].strip()


def _ruff_diagnostics(root: Path) -> Counter[Fingerprint]:
    command = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--output-format",
        "json",
        "--config",
        str(CONFIG),
        "src",
        "tests",
    ]
    result = _run(command, cwd=root)
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"Ruff execution failed in {root}: rc={result.returncode}\n{result.stderr.strip()}"
        )
    try:
        payload: list[dict[str, Any]] = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ruff did not return JSON in {root}: {result.stdout!r}") from exc

    diagnostics: Counter[Fingerprint] = Counter()
    for item in payload:
        relative = _relative_filename(str(item.get("filename") or ""), root)
        code = str(item.get("code") or "")
        message = str(item.get("message") or "")
        location = item.get("location") or {}
        row = int(location.get("row") or 0)
        source_line = _source_line(root, relative, row)
        diagnostics[(relative, code, message, source_line)] += 1
    return diagnostics


def main() -> int:
    baseline_check = _run(
        ["git", "cat-file", "-e", f"{BASELINE_COMMIT}^{{commit}}"],
        cwd=ROOT,
    )
    if baseline_check.returncode != 0:
        print(f"RUFF_RATCHET=FAIL missing baseline commit {BASELINE_COMMIT}")
        return 2

    current = _ruff_diagnostics(ROOT)
    with tempfile.TemporaryDirectory(prefix="cripta-ruff-ratchet-") as temp_parent:
        baseline_root = Path(temp_parent) / "baseline"
        added = False
        try:
            add = _run(
                ["git", "worktree", "add", "--detach", "--quiet", str(baseline_root), BASELINE_COMMIT],
                cwd=ROOT,
            )
            if add.returncode != 0:
                print(f"RUFF_RATCHET=FAIL unable to materialize baseline: {add.stderr.strip()}")
                return 2
            added = True
            baseline = _ruff_diagnostics(baseline_root)
        finally:
            if added:
                _run(["git", "worktree", "remove", "--force", str(baseline_root)], cwd=ROOT)

    new_diagnostics = current - baseline
    print(f"RUFF_BASELINE_COMMIT={BASELINE_COMMIT}")
    print(f"RUFF_BASELINE_DIAGNOSTICS={sum(baseline.values())}")
    print(f"RUFF_CURRENT_DIAGNOSTICS={sum(current.values())}")
    print(f"RUFF_NEW_DIAGNOSTICS={sum(new_diagnostics.values())}")

    if new_diagnostics:
        print("RUFF_RATCHET=FAIL")
        for (path, code, message, source_line), count in sorted(new_diagnostics.items()):
            suffix = f" x{count}" if count > 1 else ""
            print(f"NEW {path} {code}: {message}{suffix}")
            if source_line:
                print(f"    {source_line}")
        return 1

    print("RUFF_RATCHET=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
