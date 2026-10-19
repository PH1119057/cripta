"""Compare mypy errors against the exact base tree without suppressing new errors."""
from __future__ import annotations

import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

_ERROR = re.compile(
    r"^(.+?):\d+(?::\d+)?: error: (.*?)(?:\s+\[([\w-]+)\])?$"
)


def diagnostics(root: Path) -> Counter[tuple[str, str, str]]:
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "src/bybit_workbench", "--no-incremental"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"mypy execution failed for {root}: {result.stderr}")
    found: Counter[tuple[str, str, str]] = Counter()
    for line in result.stdout.splitlines():
        matched = _ERROR.match(line)
        if matched:
            path, message, code = matched.groups()
            found[(path.replace("\\", "/"), code or "", message)] += 1
        elif ": error:" in line:
            raise RuntimeError(f"unrecognized mypy diagnostic in {root}: {line}")
    if result.returncode == 1 and not found:
        raise RuntimeError(f"mypy failed without parsed errors in {root}: {result.stdout}")
    return found


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python mypy_regression_gate.py <baseline-directory>", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[2]
    baseline_root = Path(sys.argv[1]).resolve()
    if not (baseline_root / "src/bybit_workbench").is_dir():
        print("mypy baseline source is missing", file=sys.stderr)
        return 2
    try:
        old = diagnostics(baseline_root)
        new = diagnostics(root)
    except RuntimeError as exc:
        print(f"mypy baseline verification failed: {exc}", file=sys.stderr)
        return 2
    introduced = new - old
    print(f"MYPY_BASELINE_ERRORS={old.total()}")
    print(f"MYPY_CURRENT_ERRORS={new.total()}")
    print(f"MYPY_NEW_ERRORS={introduced.total()}")
    for (path, code, message), count in sorted(introduced.items()):
        print(f"NEW {path}: [{code}] {message} (x{count})")
    return 1 if introduced else 0


if __name__ == "__main__":
    raise SystemExit(main())
