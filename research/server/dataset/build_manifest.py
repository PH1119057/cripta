from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

PERIOD = "20260518_20260816"
ROOT = Path(os.environ.get("CRIPTA_DATASET_ROOT", "/data/cripta/datasets/raw")) / PERIOD
STATE_FILES = ("download_state.json", "download_state_expansion_20260823.json")


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(8 * 1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def write_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    states = {name: read_json(ROOT / name) for name in STATE_FILES}
    incomplete = [name for name, state in states.items() if state.get("status") != "complete"]
    if incomplete:
        raise SystemExit(f"download states are not complete: {', '.join(incomplete)}")
    files = sorted(path for path in ROOT.glob("*/*/*") if path.is_file() and not path.name.endswith(".part"))
    expected = sum(int(state.get("files_expected") or state.get("files_ready") or 0) for state in states.values())
    if len(files) != expected:
        raise SystemExit(f"file count mismatch: actual={len(files)} expected={expected}")
    started = int(time.time())
    entries = []
    total_bytes = 0
    for index, path in enumerate(files, 1):
        size = path.stat().st_size
        total_bytes += size
        entries.append({"path": path.relative_to(ROOT).as_posix(), "bytes": size, "sha256": digest(path)})
        if index % 10 == 0:
            write_json(ROOT / "manifest_progress.json", {"state": "running", "ready": index, "expected": len(files), "current": entries[-1]["path"], "started_at_epoch": started})
    manifest = {"period": PERIOD, "created_at_epoch": int(time.time()), "files": len(entries), "bytes": total_bytes, "states": states, "entries": entries}
    write_json(ROOT / "MANIFEST.sha256.json", manifest)
    write_json(ROOT / "manifest_progress.json", {"state": "verified", "ready": len(entries), "expected": len(entries), "bytes": total_bytes, "finished_at_epoch": int(time.time())})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
