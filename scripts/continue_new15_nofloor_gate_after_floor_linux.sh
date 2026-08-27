#!/usr/bin/env bash
set -euo pipefail

readonly PREVIOUS_UNIT="cripta-entry-path-new15-full-after-gate.service"
readonly PREVIOUS_STATUS="/srv/cripta/reports/universal_entry_path_replay_v1/NEW15_FULL_20260824/POOL_STATUS.json"
readonly OUTPUT_ROOT="/srv/cripta/reports/universal_entry_path_replay_v1/NEW15_NO_FLOOR_GATE_10P_20260824"
readonly LAUNCHER="/srv/cripta/research/research_universal_entry_paths_new15_linux.sh"

while systemctl is-active --quiet "${PREVIOUS_UNIT}"; do sleep 15; done

/srv/cripta/research_runs/minute_entry_book_v1/.venv/bin/python - "${PREVIOUS_STATUS}" <<'PY'
import json, sys
from pathlib import Path
rows = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
failed = [row for row in rows if row.get("status") == "failed"]
done = [row for row in rows if row.get("status") in {"completed", "reused"}]
if len(rows) != 15 or len(done) != 15 or failed:
    raise SystemExit(f"previous full run rejected: jobs={len(rows)} done={len(done)} failed={len(failed)}")
print("previous full run accepted: jobs=15 failed=0", flush=True)
PY

export CRIPTA_WORKERS=10
export CRIPTA_FRACTION=0.1
export CRIPTA_PATH_POLICY=no_floor
export CRIPTA_OUTPUT_ROOT="${OUTPUT_ROOT}"
exec "${LAUNCHER}"
