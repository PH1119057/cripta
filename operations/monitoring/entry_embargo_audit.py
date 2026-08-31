from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

QUERY = """
WITH lifecycle AS (
    SELECT candidate_id, symbol,
      max(CASE WHEN event_type='CORE_SIGNAL' THEN 1 ELSE 0 END) AS core_signal,
      max(CASE WHEN event_type IN ('TOUCH_VETO','TOUCH_BLOCKED') THEN 1 ELSE 0 END) AS rejected,
      min(CASE WHEN event_type='MILESTONE_MINUS_1_00' THEN occurred_at END) AS minus_1_at,
      min(CASE WHEN event_type='MILESTONE_PLUS_1_00' THEN occurred_at END) AS plus_1_at,
      max(CASE WHEN event_type='TOUCH_VETO'
          AND json_extract(payload_json,'$.accepted_after_embargo')=0 THEN 1 ELSE 0 END)
          AS blocked_by_embargo
    FROM entry_bot_candidate_events
    WHERE candidate_id IS NOT NULL
    GROUP BY candidate_id, symbol
)
SELECT
  count(1) AS candidates,
  sum(core_signal) AS actual_core_signals,
  sum(CASE WHEN core_signal=1 AND minus_1_at IS NOT NULL THEN 1 ELSE 0 END)
      AS embargo_after_core_signal,
  sum(CASE WHEN core_signal=0 AND rejected=1 AND minus_1_at IS NOT NULL THEN 1 ELSE 0 END)
      AS invalid_embargo_after_rejected_candidate,
  sum(blocked_by_embargo) AS subsequent_candidates_blocked,
  sum(CASE WHEN blocked_by_embargo=1 AND plus_1_at IS NOT NULL
      AND (minus_1_at IS NULL OR plus_1_at<minus_1_at) THEN 1 ELSE 0 END)
      AS subsequent_good_paths_potentially_lost,
  sum(CASE WHEN blocked_by_embargo=1 AND minus_1_at IS NOT NULL
      AND (plus_1_at IS NULL OR minus_1_at<plus_1_at) THEN 1 ELSE 0 END)
      AS subsequent_bad_paths_avoided,
  count(DISTINCT CASE WHEN core_signal=0 AND rejected=1 AND minus_1_at IS NOT NULL
      THEN symbol END) AS affected_symbols
FROM lifecycle
"""


def audit(database: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=30)
    try:
        cursor = connection.execute(QUERY)
        row = cursor.fetchone()
        columns = [item[0] for item in cursor.description]
        symbol_rows = connection.execute("""
            WITH lifecycle AS (
              SELECT candidate_id,symbol,
                max(CASE WHEN event_type='CORE_SIGNAL' THEN 1 ELSE 0 END) core_signal,
                max(CASE WHEN event_type IN ('TOUCH_VETO','TOUCH_BLOCKED') THEN 1 ELSE 0 END)
                    rejected,
                max(CASE WHEN event_type='MILESTONE_MINUS_1_00' THEN 1 ELSE 0 END) minus_1
              FROM entry_bot_candidate_events WHERE candidate_id IS NOT NULL
              GROUP BY candidate_id,symbol)
            SELECT symbol,count(1) FROM lifecycle
            WHERE core_signal=0 AND rejected=1 AND minus_1=1
            GROUP BY symbol ORDER BY count(1) DESC
        """).fetchall()
    finally:
        connection.close()
    result = dict(zip(columns, row, strict=True))
    result["symbols"] = {symbol: count for symbol, count in symbol_rows}
    result["orders"] = "NO_DATA"
    result["fills"] = "NO_DATA"
    result["audited_at"] = datetime.now(UTC).isoformat()
    result["defect_confirmed"] = bool(result["invalid_embargo_after_rejected_candidate"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Аудит области действия Entry V1 embargo")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.database)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# Аудит ENTRY_V1_FAILURE_EMBARGO_SCOPE_BUG",
        "",
        f"- Дефект подтверждён: **{result['defect_confirmed']}**",
        f"- Rejected candidates, способных поставить embargo: "
        f"**{result['invalid_embargo_after_rejected_candidate']}**",
        f"- Следующих candidates заблокировано: **{result['subsequent_candidates_blocked']}**",
        f"- Потенциально потерянных хороших путей: "
        f"**{result['subsequent_good_paths_potentially_lost']}**",
        f"- Потенциально избегнутых плохих путей: "
        f"**{result['subsequent_bad_paths_avoided']}**",
        "- Order/fill attribution: **NO_DATA** в shadow SQLite.",
    ]
    (args.output / "REPORT_RU.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
