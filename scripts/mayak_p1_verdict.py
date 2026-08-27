from __future__ import annotations

import argparse
import json
from pathlib import Path

from bybit_workbench.mayak.research.verdict import finalize_p1


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify freeze and enforce MAYAK P1 gate")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verdict = finalize_p1(args.output.resolve())
    print(json.dumps(verdict, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
