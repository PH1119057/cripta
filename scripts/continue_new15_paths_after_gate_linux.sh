#!/usr/bin/env bash
set -euo pipefail

readonly GATE_UNIT="cripta-entry-path-new15-10p-v2.service"
readonly GATE_STATUS="/srv/cripta/reports/universal_entry_path_replay_v1/NEW15_GATE_10P_20260824/POOL_STATUS.json"
readonly FULL_OUTPUT="/srv/cripta/reports/universal_entry_path_replay_v1/NEW15_FULL_20260824"
readonly LAUNCHER="/srv/cripta/research/research_universal_entry_paths_new15_linux.sh"

while systemctl is-active --quiet "${GATE_UNIT}"; do
  sleep 15
done

/srv/cripta/research_runs/minute_entry_book_v1/.venv/bin/python - "${GATE_STATUS}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
rows = json.loads(path.read_text(encoding="utf-8"))
failed = [row for row in rows if row.get("status") == "failed"]
complete = [row for row in rows if row.get("status") in {"completed", "reused"}]
if len(rows) != 15 or failed or len(complete) != 15:
    raise SystemExit(f"10% gate rejected: jobs={len(rows)} complete={len(complete)} failed={len(failed)}")
print("10% gate accepted: jobs=15 failed=0", flush=True)
PY

export CRIPTA_WORKERS=10
export CRIPTA_FRACTION=1
export CRIPTA_OUTPUT_ROOT="${FULL_OUTPUT}"
exec "${LAUNCHER}"
