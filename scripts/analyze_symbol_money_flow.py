from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

HORIZONS = (1, 5, 15, 60, 300)


def _new_bucket(timestamp: int, price: float) -> dict[str, float | int]:
    return {
        "timestamp": timestamp,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "buy_usd": 0.0,
        "sell_usd": 0.0,
        "trades": 0,
    }


def _update(bucket: dict[str, float | int], side: str, notional: float, price: float) -> None:
    bucket["high"] = max(float(bucket["high"]), price)
    bucket["low"] = min(float(bucket["low"]), price)
    bucket["close"] = price
    bucket["trades"] = int(bucket["trades"]) + 1
    key = "buy_usd" if side == "Buy" else "sell_usd"
    bucket[key] = float(bucket[key]) + notional


def _read_day(path: Path) -> tuple[list[dict[str, float | int]], list[dict[str, float | int]]]:
    seconds: dict[int, dict[str, float | int]] = {}
    minutes: dict[int, dict[str, float | int]] = {}
    with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            timestamp = int(float(row["timestamp"]))
            price = float(row["price"])
            notional = float(row.get("foreignNotional") or float(row["size"]) * price)
            second = seconds.setdefault(timestamp, _new_bucket(timestamp, price))
            minute_ts = timestamp - timestamp % 60
            minute = minutes.setdefault(minute_ts, _new_bucket(minute_ts, price))
            _update(second, row["side"], notional, price)
            _update(minute, row["side"], notional, price)
    return list(seconds.values()), list(minutes.values())


def _flow_fields(bucket: dict[str, float | int]) -> dict[str, float]:
    buy = float(bucket["buy_usd"])
    sell = float(bucket["sell_usd"])
    total = buy + sell
    return {
        "total_usd": total,
        "net_buy_usd": buy - sell,
        "imbalance": (buy - sell) / total if total else 0.0,
    }


def _return_pct(start: float, end: float) -> float:
    return 100.0 * (end / start - 1.0)


def _extreme_events(seconds: list[dict[str, float | int]], quantile: float) -> list[dict[str, Any]]:
    if not seconds:
        return []
    ts = np.asarray([int(row["timestamp"]) for row in seconds], dtype=np.int64)
    close = np.asarray([float(row["close"]) for row in seconds], dtype=np.float64)
    net = np.asarray([_flow_fields(row)["net_buy_usd"] for row in seconds], dtype=np.float64)
    threshold = float(np.quantile(np.abs(net), quantile))
    indexes = np.flatnonzero(np.abs(net) >= threshold)
    output: list[dict[str, Any]] = []
    for index in indexes:
        row = seconds[int(index)]
        flow = _flow_fields(row)
        direction = 1.0 if net[index] > 0 else -1.0
        event: dict[str, Any] = {
            **row,
            **flow,
            "flow_side": "BUY" if direction > 0 else "SELL",
            "within_second_return_pct": _return_pct(float(row["open"]), float(row["close"])),
            "day_extreme_threshold_usd": threshold,
        }
        for horizon in HORIZONS:
            target = int(np.searchsorted(ts, ts[index] + horizon, side="left"))
            raw = math.nan if target >= len(ts) else _return_pct(close[index], close[target])
            event[f"return_{horizon}s_pct"] = raw
            event[f"flow_aligned_return_{horizon}s_pct"] = raw * direction
        output.append(event)
    return output


def _write_gzip_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _summarize(events: list[dict[str, Any]], symbol: str, quantile: float) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "version": "SYMBOL_MONEY_FLOW_V1",
        "symbol": symbol,
        "extreme_quantile_per_day": quantile,
        "events": len(events),
        "sides": {},
    }
    for side in ("BUY", "SELL"):
        selected = [row for row in events if row["flow_side"] == side]
        scope: dict[str, Any] = {"events": len(selected)}
        for horizon in HORIZONS:
            values = np.asarray(
                [float(row[f"flow_aligned_return_{horizon}s_pct"]) for row in selected],
                dtype=np.float64,
            )
            values = values[np.isfinite(values)]
            scope[f"{horizon}s"] = {
                "observations": int(values.size),
                "mean_aligned_return_pct": float(values.mean()) if values.size else None,
                "median_aligned_return_pct": float(np.median(values)) if values.size else None,
                "continuation_rate": float(np.mean(values > 0.0)) if values.size else None,
                "p10": float(np.quantile(values, 0.1)) if values.size else None,
                "p90": float(np.quantile(values, 0.9)) if values.size else None,
            }
        summary["sides"][side] = scope
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Помонетный анализ агрессивного денежного потока")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--raw-symbol-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--extreme-quantile", type=float, default=0.999)
    args = parser.parse_args()
    if not 0.9 <= args.extreme_quantile < 1.0:
        parser.error("--extreme-quantile должен быть в [0.9, 1.0)")
    archives = sorted((args.raw_symbol_dir / "public_trades").glob(f"{args.symbol.upper()}*.csv.gz"))
    if not archives:
        raise FileNotFoundError(args.raw_symbol_dir)
    all_minutes: list[dict[str, Any]] = []
    all_events: list[dict[str, Any]] = []
    for done, archive in enumerate(archives, 1):
        seconds, minutes = _read_day(archive)
        all_events.extend(_extreme_events(seconds, args.extreme_quantile))
        all_minutes.extend({**row, **_flow_fields(row)} for row in minutes)
        print(f"[{args.symbol}] {done}/{len(archives)} {archive.name}", flush=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_gzip_csv(args.output_dir / "minute_money_flow.csv.gz", all_minutes)
    _write_gzip_csv(args.output_dir / "extreme_second_events.csv.gz", all_events)
    summary = _summarize(all_events, args.symbol.upper(), args.extreme_quantile)
    summary["archives"] = len(archives)
    summary["minutes"] = len(all_minutes)
    summary["completed_at"] = datetime.now(UTC).isoformat()
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "RUN_COMPLETE.json").write_text(
        json.dumps({"version": summary["version"], "symbol": args.symbol.upper()}, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
