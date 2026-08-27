from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np


def _day(timestamp: str | int) -> str:
    return datetime.fromtimestamp(int(timestamp), tz=UTC).date().isoformat()


def _read(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def main() -> int:
    parser = argparse.ArgumentParser(description="Ежедневный отчёт денежного потока одной монеты")
    parser.add_argument("--symbol-dir", type=Path, required=True)
    args = parser.parse_args()
    symbol = args.symbol_dir.name.upper()
    minutes = _read(args.symbol_dir / "minute_money_flow.csv.gz")
    events = _read(args.symbol_dir / "extreme_second_events.csv.gz")
    by_day: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    events_by_day: defaultdict[str, list[dict[str, str]]] = defaultdict(list)
    for row in minutes:
        by_day[_day(row["timestamp"])].append(row)
    for row in events:
        events_by_day[_day(row["timestamp"])].append(row)
    output: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    for day, rows in sorted(by_day.items()):
        buy = np.asarray([float(r["buy_usd"]) for r in rows])
        sell = np.asarray([float(r["sell_usd"]) for r in rows])
        net = buy - sell
        first, last = rows[0], rows[-1]
        day_events = events_by_day.get(day, [])
        strongest_buy = max(day_events, key=lambda r: float(r["net_buy_usd"]), default=None)
        strongest_sell = min(day_events, key=lambda r: float(r["net_buy_usd"]), default=None)
        record = {
            "symbol": symbol,
            "day_utc": day,
            "minutes": len(rows),
            "total_buy_usd": float(buy.sum()),
            "total_sell_usd": float(sell.sum()),
            "net_buy_usd": float(net.sum()),
            "average_buy_usd_per_minute": float(buy.mean()),
            "average_sell_usd_per_minute": float(sell.mean()),
            "average_net_buy_usd_per_minute": float(net.mean()),
            "median_abs_net_usd_per_minute": float(np.median(np.abs(net))),
            "p99_abs_net_usd_per_minute": float(np.quantile(np.abs(net), 0.99)),
            "open": float(first["open"]),
            "close": float(last["close"]),
            "day_return_pct": 100.0 * (float(last["close"]) / float(first["open"]) - 1.0),
            "extreme_second_events": len(day_events),
            "strongest_buy_second_utc": "" if strongest_buy is None else datetime.fromtimestamp(int(strongest_buy["timestamp"]), tz=UTC).isoformat(),
            "strongest_buy_net_usd": "" if strongest_buy is None else float(strongest_buy["net_buy_usd"]),
            "strongest_sell_second_utc": "" if strongest_sell is None else datetime.fromtimestamp(int(strongest_sell["timestamp"]), tz=UTC).isoformat(),
            "strongest_sell_net_usd": "" if strongest_sell is None else float(strongest_sell["net_buy_usd"]),
        }
        output.append(record)
        for event in day_events:
            anomalies.append({"symbol": symbol, "day_utc": day, **event})
    with (args.symbol_dir / "daily_money_flow.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)
    with gzip.open(args.symbol_dir / "daily_anomalies.csv.gz", "wt", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(anomalies[0]))
        writer.writeheader()
        writer.writerows(anomalies)
    summary = {
        "symbol": symbol,
        "days": len(output),
        "highest_net_inflow_day": max(output, key=lambda r: float(r["net_buy_usd"])),
        "highest_net_outflow_day": min(output, key=lambda r: float(r["net_buy_usd"])),
        "largest_positive_day": max(output, key=lambda r: float(r["day_return_pct"])),
        "largest_negative_day": min(output, key=lambda r: float(r["day_return_pct"])),
    }
    (args.symbol_dir / "daily_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"symbol": symbol, "days": len(output), "anomalies": len(anomalies)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
