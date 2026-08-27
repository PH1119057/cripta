from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

INDICATORS = {"BTCUSDT", "ETHUSDT"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _metrics(rows: list[dict[str, str]], scenario: str) -> dict[str, Any]:
    outcomes = Counter(row[f"{scenario}_outcome"] for row in rows)
    resolved = outcomes["target"] + outcomes["initial_stop"]
    rate = outcomes["target"] / resolved if resolved else 0.0
    z = 1.95996398454
    denominator = 1.0 + z * z / resolved if resolved else 1.0
    center = (rate + z * z / (2.0 * resolved)) / denominator if resolved else 0.0
    half = (
        z * math.sqrt(rate * (1.0 - rate) / resolved + z * z / (4.0 * resolved**2))
        / denominator if resolved else 0.0
    )
    return {
        "signals": len(rows),
        "filled": sum(row[f"{scenario}_fill_status"] == "filled" for row in rows),
        "outcomes": dict(outcomes),
        "resolved_win_rate_pct": 100.0 * rate,
        "wilson_95_pct": [100.0 * (center - half), 100.0 * (center + half)],
        "illustrative_pnl_usd": 10 * outcomes["target"] - 11 * outcomes["initial_stop"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Стаканный фильтр без минутной зоны")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    with args.source.open("r", encoding="utf-8-sig", newline="") as handle:
        all_rows = list(csv.DictReader(handle))
    selected = [row for row in all_rows if row["book_confirms"] == "True"]
    trade = [row for row in selected if row["symbol"] not in INDICATORS]
    summary = {
        "version": "ORDERBOOK_ONLY_SUMMARY_V1",
        "source_sha256": _sha256(args.source),
        "rule_ru": "только причинное подтверждение стакана в момент исходного сигнала",
        "all9": {scenario: _metrics(selected, scenario) for scenario in ("e0", "e20")},
        "trading7": {scenario: _metrics(trade, scenario) for scenario in ("e0", "e20")},
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "ОТОБРАННЫЕ_СИГНАЛЫ.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(selected[0]))
        writer.writeheader()
        writer.writerows(selected)
    (args.output_dir / "ИТОГ.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
