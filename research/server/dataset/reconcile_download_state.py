from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/data/cripta/datasets/raw/20260518_20260816")
STATE = ROOT / "download_state_expansion_20260823.json"


def main() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    symbols = state.get("symbols") or []
    actual = 0
    for symbol in symbols:
        actual += len(list((ROOT / symbol / "public_trades").glob("*.csv.gz")))
        actual += len(list((ROOT / symbol / "orderbook").glob("*.data.zip")))
    expected = int(state.get("files_expected") or 0)
    if actual != expected:
        raise SystemExit(f"actual file count {actual} does not match expected {expected}")
    resolved = list(state.get("missing") or [])
    state["files_ready"] = actual
    state["missing"] = []
    state["resolved_missing"] = resolved
    state["status"] = "complete"
    temporary = STATE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(STATE)
    print(f"files_ready={actual} resolved_missing={len(resolved)}")


if __name__ == "__main__":
    main()
