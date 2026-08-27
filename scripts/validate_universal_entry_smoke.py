from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


root = Path(sys.argv[1])
summary = json.loads((root / "summary.json").read_text(encoding="utf-8-sig"))
complete = json.loads((root / "RUN_COMPLETE.json").read_text(encoding="utf-8-sig"))
assert summary["engine"] == "universal-entry-15m5m-v1"
assert complete["engine"] == summary["engine"]
assert complete["signals_sha256"] == sha256(root / "signals.csv")
assert summary["signals_sha256"] == complete["signals_sha256"]
with (root / "signals.csv").open(newline="", encoding="utf-8-sig") as handle:
    header = next(csv.reader(handle))
required = {"symbol", "direction", "entry_at", "entry_price"}
assert required.issubset(header), sorted(required - set(header))
print("TECHNICAL_VALIDATION_OK")
