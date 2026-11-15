from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


def summarize(rows: list[dict[str, str]], key) -> list[dict[str, object]]:
    groups: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    result = []
    for name in sorted(groups):
        items = groups[name]
        resolved = [row for row in items if row["outcome"] != "unresolved_24h"]
        wins = sum(row["outcome"] == "target_first" for row in resolved)
        result.append(
            {
                "group": name,
                "signals": len(items),
                "resolved": len(resolved),
                "target_first": wins,
                "stop_first": len(resolved) - wins,
                "target_pct_resolved": round(100 * wins / len(resolved), 4)
                if resolved
                else None,
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    rows: list[dict[str, str]] = []
    for path in sorted(root.glob("*/events.csv")):
        with path.open("r", newline="", encoding="utf-8") as handle:
            rows.extend(csv.DictReader(handle))
    if len(rows) != 8652:
        raise ValueError(f"expected 8652 rows, got {len(rows)}")
    activated = [row for row in rows if row["activation_at"]]
    resolved_activated = [row for row in activated if row["outcome"] != "unresolved_24h"]
    wins = sum(row["outcome"] == "target_first" for row in resolved_activated)
    early = [row for row in activated if row["first_structure_early_60m"] == "True"]
    payload = {
        "architecture": "exact_touch_zone_structure_v1",
        "cohort": "8652 exact_touch signals from old nine-asset panel",
        "horizon_hours": 24,
        "activation_pct": 0.10,
        "target_pct": 1.10,
        "stop_pct": 1.00,
        "signals": len(rows),
        "activated": len(activated),
        "activated_resolved": len(resolved_activated),
        "activated_baseline_target_first": wins,
        "activated_baseline_stop_first": len(resolved_activated) - wins,
        "activated_baseline_target_pct": round(100 * wins / len(resolved_activated), 4),
        "first_structure_full_path": summarize(
            activated, lambda row: row["first_structure_state"] or "none"
        ),
        "first_structure_early_60m": summarize(
            early, lambda row: row["first_structure_state"]
        ),
        "structural_balance": summarize(activated, lambda row: row["structural_balance"]),
        "by_symbol": summarize(activated, lambda row: row["symbol"]),
    }
    with (root / "panel_events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (root / "panel_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for name in ("first_structure_full_path", "first_structure_early_60m", "structural_balance", "by_symbol"):
        table = payload[name]
        with (root / f"{name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(table[0]))
            writer.writeheader()
            writer.writerows(table)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
