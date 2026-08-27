#!/usr/bin/env bash
set -euo pipefail

project_root=${CRIPTA_PROJECT_ROOT:-/srv/cripta/research_runs/universal_entry_v1/source_stage}
raw_root=${CRIPTA_RAW_ROOT:-/data/cripta/datasets/raw/20260518_20260816}
work_root=${CRIPTA_ENTRY_WORK_ROOT:-/srv/cripta/reports/universal_entry_v1/NEW15_HOLDOUT_20260824}
python_bin=${CRIPTA_PYTHON:-/srv/cripta/research_runs/minute_entry_book_v1/.venv/bin/python}
workers=${CRIPTA_WORKERS:-10}
symbols=${CRIPTA_SYMBOLS:-AAVEUSDT,APTUSDT,ARBUSDT,AVAXUSDT,BCHUSDT,BNBUSDT,DOTUSDT,HBARUSDT,INJUSDT,LTCUSDT,NEARUSDT,OPUSDT,SUIUSDT,TRXUSDT,XLMUSDT}

export PYTHONPATH="$project_root/src"
exec "$python_bin" -m bybit_workbench.research.universal_entry_pipeline \
  --raw-root "$raw_root" \
  --work-root "$work_root" \
  --symbols "$symbols" \
  --workers "$workers"
