#!/usr/bin/env bash
set -euo pipefail

readonly CURRENT_UNIT="cripta-entry-path-new15-full-after-gate.service"
readonly READY13_UNIT="cripta-entry-path-new15-nofloor-full-ready13.service"
readonly PYTHON_BIN="/srv/cripta/research_runs/minute_entry_book_v1/.venv/bin/python"
readonly SOURCE_ROOT="/srv/cripta/research_runs/universal_entry_v1/source_stage"
readonly RAW_ROOT="/data/cripta/datasets/raw/20260518_20260816"
readonly ENTRY_ROOT="/srv/cripta/reports/universal_entry_v1/NEW15_HOLDOUT_20260824"
readonly OUTPUT_ROOT="/srv/cripta/reports/universal_entry_path_replay_v1/NEW15_NO_FLOOR_FULL_20260824"

while systemctl is-active --quiet "${CURRENT_UNIT}" || systemctl is-active --quiet "${READY13_UNIT}"; do
  sleep 20
done

for symbol in NEARUSDT XLMUSDT; do
  test -f "/srv/cripta/reports/universal_entry_path_replay_v1/NEW15_FULL_20260824/${symbol}/RUN_COMPLETE.json"
done

export PYTHONPATH="${SOURCE_ROOT}/src"
exec "${PYTHON_BIN}" -m bybit_workbench.research.universal_entry_path_replay \
  --raw-root "${RAW_ROOT}" \
  --entry-root "${ENTRY_ROOT}" \
  --output-root "${OUTPUT_ROOT}" \
  --symbols "NEARUSDT,XLMUSDT" \
  --workers 2 \
  --fraction 1 \
  --policy no_floor
