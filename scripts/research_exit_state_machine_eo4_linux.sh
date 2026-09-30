#!/usr/bin/env bash
set -euo pipefail

REPORT_ROOT="${CRIPTA_REPORT_ROOT:-/data/cripta/research/runs/_legacy_srv_reports_20260911/exit_state_machine_eo4}"
RUN_ID="${EO4_RUN_ID:-EO4_$(date -u +%Y%m%d_%H%M%S)}"
WORKERS="${EO4_WORKERS:-1}"
SAMPLE="${EO4_SAMPLE_FRACTION:-0.10}"
SYMBOLS="${EO4_SYMBOLS:-UNIUSDT}"

exec /data/cripta/research/worktrees/minute_entry_book_v1/.venv/bin/python /data/cripta/research/worktrees/legacy_srv_research/exit_state_machine_eo4.py \
  --eo2-events /data/cripta/research/runs/_legacy_srv_research_inputs_20260929/eo4/eo2_events.csv \
  --zone-events /data/cripta/research/runs/_legacy_srv_research_inputs_20260929/eo4/independent_zone_touch_outcomes.csv \
  --cache-root /data/cripta/research/cache/legacy_srv_research_cache/eo3_full_path_1m_cache \
  --output-dir "$REPORT_ROOT/$RUN_ID" \
  --symbols "$SYMBOLS" \
  --sample-fraction "$SAMPLE" \
  --workers "$WORKERS"