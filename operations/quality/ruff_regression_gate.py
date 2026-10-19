"""Fail CI only on Ruff diagnostics introduced relative to an exact baseline tree.

The existing repository debt is not silently declared clean. Both source trees
are checked with the same Ruff interpreter/version and exact config.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


def diagnostics(root: Path) -> Counter[tuple[str, str, str]]:
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "src", "tests", "--output-format=json"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"Ruff failed in {root}: {result.stderr}")
    try:
        records = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ruff did not provide JSON for {root}") from exc
    found: Counter[tuple[str, str, str]] = Counter()
    for entry in records:
        path = Path(entry["filename"]).resolve().relative_to(root.resolve()).as_posix()
        found[(path, str(entry["code"]), str(entry["message"]))] += 1
    return found


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python ruff_regression_gate.py <baseline-directory>", file=sys.stderr)
        return 2
    current_root = Path(__file__).resolve().parents[2]
    baseline_root = Path(sys.argv[1]).resolve()
    if not (baseline_root / "src").is_dir() or not (baseline_root / "tests").is_dir():
        print("baseline source tree is missing", file=sys.stderr)
        return 2
    try:
        baseline = diagnostics(baseline_root)
        current = diagnostics(current_root)
    except (RuntimeError, ValueError) as exc:
        print(f"Ruff baseline verification failed: {exc}", file=sys.stderr)
        return 2
    new = current - baseline
    print(f"RUFF_BASELINE_DIAGNOSTICS={baseline.total()}")
    print(f"RUFF_CURRENT_DIAGNOSTICS={current.total()}")
    print(f"RUFF_NEW_DIAGNOSTICS={new.total()}")
    if new:
        for (path, code, message), occurrences in sorted(new.items()):
            print(f"NEW {path}: {code} {message} (x{occurrences})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
