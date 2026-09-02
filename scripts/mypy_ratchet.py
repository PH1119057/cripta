from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE_COMMIT = "ad506e186b2dce57edf327a2d5db1fd4398e765a"
CONFIG = ROOT / "pyproject.toml"
ERROR_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+)(?::(?P<column>\d+))?: error: "
    r"(?P<message>.*?)(?:  \[(?P<code>[^\]]+)\])?$"
)

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


def _mypy_diagnostics(root: Path) -> Counter[Fingerprint]:
    command = [
        sys.executable,
        "-m",
        "mypy",
        "--config-file",
        str(CONFIG),
        "--no-error-summary",
        "--show-error-codes",
        "--no-pretty",
        "src/bybit_workbench",
    ]
    result = _run(command, cwd=root)
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"mypy execution failed in {root}: rc={result.returncode}\n{result.stderr.strip()}"
        )

    diagnostics: Counter[Fingerprint] = Counter()
    for raw_line in result.stdout.splitlines():
        match = ERROR_RE.match(raw_line.strip())
        if not match:
            continue
        relative = _relative_filename(match.group("path"), root)
        row = int(match.group("line"))
        code = match.group("code") or ""
        message = match.group("message")
        source_line = _source_line(root, relative, row)
        diagnostics[(relative, code, message, source_line)] += 1

    if result.returncode == 1 and not diagnostics:
        raise RuntimeError(f"mypy failed without parseable diagnostics:\n{result.stdout}\n{result.stderr}")
    return diagnostics


def main() -> int:
    baseline_check = _run(
        ["git", "cat-file", "-e", f"{BASELINE_COMMIT}^{{commit}}"],
        cwd=ROOT,
    )
    if baseline_check.returncode != 0:
        print(f"MYPY_RATCHET=FAIL missing baseline commit {BASELINE_COMMIT}")
        return 2

    current = _mypy_diagnostics(ROOT)
    with tempfile.TemporaryDirectory(prefix="cripta-mypy-ratchet-") as temp_parent:
        baseline_root = Path(temp_parent) / "baseline"
        added = False
        try:
            add = _run(
                ["git", "worktree", "add", "--detach", "--quiet", str(baseline_root), BASELINE_COMMIT],
                cwd=ROOT,
            )
            if add.returncode != 0:
                print(f"MYPY_RATCHET=FAIL unable to materialize baseline: {add.stderr.strip()}")
                return 2
            added = True
            baseline = _mypy_diagnostics(baseline_root)
        finally:
            if added:
                _run(["git", "worktree", "remove", "--force", str(baseline_root)], cwd=ROOT)

    new_diagnostics = current - baseline
    print(f"MYPY_BASELINE_COMMIT={BASELINE_COMMIT}")
    print(f"MYPY_BASELINE_DIAGNOSTICS={sum(baseline.values())}")
    print(f"MYPY_CURRENT_DIAGNOSTICS={sum(current.values())}")
    print(f"MYPY_NEW_DIAGNOSTICS={sum(new_diagnostics.values())}")

    if new_diagnostics:
        print("MYPY_RATCHET=FAIL")
        for (path, code, message, source_line), count in sorted(new_diagnostics.items()):
            suffix = f" x{count}" if count > 1 else ""
            code_text = f" [{code}]" if code else ""
            print(f"NEW {path}{code_text}: {message}{suffix}")
            if source_line:
                print(f"    {source_line}")
        return 1

    print("MYPY_RATCHET=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
