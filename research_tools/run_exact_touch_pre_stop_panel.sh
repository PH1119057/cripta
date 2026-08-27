#!/usr/bin/env bash
set -euo pipefail

source_root=/srv/cripta/research_runs/universal_entry_v1/source_stage
python_bin=/srv/cripta/research_runs/minute_entry_book_v1/.venv/bin/python
raw_root=/data/cripta/datasets/raw/20260518_20260816
report_root=/srv/cripta/reports/exact_touch_pre_stop_mfe_v1/ALL9_24H_20260825

mkdir -p "$report_root/logs"

run_symbol() {
  symbol="$1"
  cd "$source_root"
  PYTHONPATH=src "$python_bin" -m bybit_workbench.research.exact_touch_pre_stop_mfe \
    --signals "/tmp/${symbol}_exact_touch.csv" \
    --raw-symbol-dir "$raw_root/$symbol" \
    --output-dir "$report_root/$symbol" \
    --symbol "$symbol" \
    --fraction 1.0 \
    --horizon-hours 24 \
    >"$report_root/logs/${symbol}.log" 2>&1
}
export -f run_symbol
export source_root python_bin raw_root report_root

# Longest expected jobs enter the dynamic queue first.  xargs assigns the next
# waiting symbol as soon as any of the seven workers becomes free.
printf '%s\n' \
  BTCUSDT ETHUSDT XRPUSDT DOGEUSDT 1000PEPEUSDT LINKUSDT SOLUSDT UNIUSDT ADAUSDT \
  | xargs -n1 -P7 bash -c 'run_symbol "$1"' _

touch "$report_root/PANEL_COMPLETE"
