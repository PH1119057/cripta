from __future__ import annotations

import argparse
import sys
from pathlib import Path

from archive_v2 import verify_bundle


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Проверка воспроизводимости архива проекта V2.1"
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument(
        "--without-code-tests",
        action="store_true",
        help="Проверить структуру и хэши без запуска тестов из 01_CODE.zip",
    )
    arguments = parser.parse_args()
    try:
        index = verify_bundle(
            arguments.archive,
            run_code_tests=not arguments.without_code_tests,
        )
    except Exception as exc:
        print(f"ARCHIVE_SMOKE=FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(
        "ARCHIVE_SMOKE=PASS "
        f"version={index.get('archive_version')} "
        f"profile={index.get('profile')} "
        f"components={len(index.get('components', []))}"
    )


if __name__ == "__main__":
    main()
