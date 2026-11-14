from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path


def quantile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def classify(value: float) -> str:
    if value <= 0:
        return "0% (never positive)"
    if value <= 0.10:
        return "0-0.10%"
    if value <= 0.25:
        return "0.10-0.25%"
    if value <= 0.50:
        return "0.25-0.50%"
    if value <= 0.75:
        return "0.50-0.75%"
    return "0.75-1.10%"


def summarize(rows: list[dict[str, str]], symbol: str) -> dict[str, object]:
    stopped = [row for row in rows if row["outcome"] == "stop_first"]
    target = sum(row["outcome"] == "target_first" for row in rows)
    unresolved = sum(row["outcome"] == "unresolved_24h" for row in rows)
    depths = [float(row["max_favorable_before_event_pct"]) for row in stopped]
    positive = [value for value in depths if value > 0]
    times = [
        float(row["seconds_to_max_favorable"])
        for row in stopped
        if row["seconds_to_max_favorable"]
    ]
    decisive = target + len(stopped)
    bands = [
        "0% (never positive)",
        "0-0.10%",
        "0.10-0.25%",
        "0.25-0.50%",
        "0.50-0.75%",
        "0.75-1.10%",
    ]
    band_counts = {band: 0 for band in bands}
    for value in depths:
        band_counts[classify(value)] += 1
    return {
        "symbol": symbol,
        "signals": len(rows),
        "target_first": target,
        "stop_first": len(stopped),
        "unresolved_24h": unresolved,
        "target_pct_decisive": round(100 * target / decisive, 4) if decisive else None,
        "stop_pct_decisive": round(100 * len(stopped) / decisive, 4) if decisive else None,
        "never_positive": sum(value <= 0 for value in depths),
        "ever_positive": len(positive),
        "ever_positive_pct_of_stops": round(100 * len(positive) / len(stopped), 4)
        if stopped
        else None,
        "mfe_mean_pct": round(statistics.fmean(depths), 8) if depths else None,
        "mfe_median_pct": round(quantile(depths, 0.5) or 0, 8) if depths else None,
        "mfe_p25_pct": round(quantile(depths, 0.25) or 0, 8) if depths else None,
        "mfe_p75_pct": round(quantile(depths, 0.75) or 0, 8) if depths else None,
        "mfe_p90_pct": round(quantile(depths, 0.9) or 0, 8) if depths else None,
        "median_minutes_to_mfe": round((quantile(times, 0.5) or 0) / 60, 3)
        if times
        else None,
        **{f"band_{band}": count for band, count in band_counts.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    all_rows: list[dict[str, str]] = []
    summaries: list[dict[str, object]] = []
    for events_path in sorted(root.glob("*/events.csv")):
        symbol = events_path.parent.name
        with events_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        all_rows.extend(rows)
        summaries.append(summarize(rows, symbol))
    if len(all_rows) != 8652:
        raise ValueError(f"expected 8652 events, got {len(all_rows)}")
    summaries.append(summarize(all_rows, "ALL9"))

    with (root / "panel_events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(all_rows[0]))
        writer.writeheader()
        writer.writerows(all_rows)
    with (root / "panel_asset_summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    payload = {
        "architecture": "exact_touch_pre_stop_mfe_v1",
        "horizon_hours": 24,
        "target_pct": 1.10,
        "stop_pct": 1.00,
        "cohort": "8652 exact_touch signals from old nine-asset panel",
        "assets": summaries[:-1],
        "all9": summaries[-1],
    }
    (root / "panel_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload["all9"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
