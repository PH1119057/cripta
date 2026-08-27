from __future__ import annotations

import argparse
import csv
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np


def _seed(symbol: str, offset: float) -> int:
    raw = hashlib.sha256(f"{symbol}:{offset:.1f}".encode()).digest()
    return int.from_bytes(raw[:8], "little")


def _day(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()


def _analyze(symbol_dir: str, output_dir: str, iterations: int) -> dict[str, Any]:
    source = Path(symbol_dir) / "events.csv"
    symbol = Path(symbol_dir).name
    with source.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    result: dict[str, Any] = {"symbol": symbol, "source": str(source), "iterations": iterations}
    offsets: dict[str, Any] = {}
    for offset in (0.0, 0.1, 0.2):
        selected = [r for r in rows if abs(float(r["adverse_offset_pct"]) - offset) < 1e-9]
        resolved = [r for r in selected if r["exit_reason"] in {"target", "initial_stop"}]
        by_day: dict[str, list[float]] = {}
        for row in resolved:
            pnl = float(row["pnl_usd_100_margin_10x"])
            by_day.setdefault(_day(row["touch_at"]), []).append(pnl)
        days = sorted(by_day)
        day_sum = np.asarray([sum(by_day[d]) for d in days], dtype=np.float64)
        day_n = np.asarray([len(by_day[d]) for d in days], dtype=np.float64)
        rng = np.random.default_rng(_seed(symbol, offset))
        batch = 10_000
        ev_samples: list[np.ndarray] = []
        remaining = iterations
        while remaining:
            take = min(batch, remaining)
            picks = rng.integers(0, len(days), size=(take, len(days)))
            totals = day_sum[picks].sum(axis=1)
            counts = day_n[picks].sum(axis=1)
            ev_samples.append(totals / counts)
            remaining -= take
        samples = np.concatenate(ev_samples)
        pnl_values = np.asarray([float(r["pnl_usd_100_margin_10x"]) for r in resolved])
        offsets[f"{offset:.1f}"] = {
            "signals": len(selected),
            "filled": sum(r["fill_status"] == "filled" for r in selected),
            "resolved": len(resolved),
            "days": len(days),
            "targets": sum(r["exit_reason"] == "target" for r in resolved),
            "stops": sum(r["exit_reason"] == "initial_stop" for r in resolved),
            "observed_ev_usd": float(pnl_values.mean()),
            "day_block_bootstrap_ev_usd_p025": float(np.quantile(samples, 0.025)),
            "day_block_bootstrap_ev_usd_p50": float(np.quantile(samples, 0.5)),
            "day_block_bootstrap_ev_usd_p975": float(np.quantile(samples, 0.975)),
            "probability_ev_positive": float(np.mean(samples > 0.0)),
        }
    result["offsets"] = offsets
    destination = Path(output_dir) / symbol
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "bootstrap_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Помонетный day-block bootstrap E0/E10/E20")
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--iterations", type=int, default=1_000_000)
    args = parser.parse_args()
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    args.output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=min(args.workers, len(symbols))) as pool:
        futures = {
            pool.submit(_analyze, str(args.input_root / symbol), str(args.output_root), args.iterations): symbol
            for symbol in symbols
        }
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(f"[bootstrap] {result['symbol']} complete", flush=True)
    results.sort(key=lambda item: item["symbol"])
    (args.output_root / "index.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
