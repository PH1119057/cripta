#!/usr/bin/env bash
set -euo pipefail

readonly PYTHON_BIN="/srv/cripta/research_runs/minute_entry_book_v1/.venv/bin/python"
readonly SOURCE_ROOT="/srv/cripta/research_runs/universal_entry_v1/source_stage"
readonly RAW_ROOT="/data/cripta/datasets/raw/20260518_20260816"
readonly ENTRY_ROOT="/srv/cripta/reports/universal_entry_v1/NEW15_HOLDOUT_20260824"
readonly OUTPUT_ROOT="${CRIPTA_OUTPUT_ROOT:-/srv/cripta/reports/universal_entry_path_replay_v1/NEW15_HOLDOUT_20260824}"
readonly SYMBOLS="AAVEUSDT,APTUSDT,ARBUSDT,AVAXUSDT,BCHUSDT,BNBUSDT,DOTUSDT,HBARUSDT,INJUSDT,LTCUSDT,NEARUSDT,OPUSDT,SUIUSDT,TRXUSDT,XLMUSDT"

export PYTHONPATH="${SOURCE_ROOT}/src"
exec "${PYTHON_BIN}" -m bybit_workbench.research.universal_entry_path_replay \
  --raw-root "${RAW_ROOT}" \
  --entry-root "${ENTRY_ROOT}" \
  --output-root "${OUTPUT_ROOT}" \
  --symbols "${SYMBOLS}" \
  --workers "${CRIPTA_WORKERS:-10}" \
  --fraction "${CRIPTA_FRACTION:-1}" \
  --policy "${CRIPTA_PATH_POLICY:-eo1_floor}"
