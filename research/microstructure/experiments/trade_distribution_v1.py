#!/usr/bin/env python3
"""RESEARCH ONLY: historical fill-size and inter-timestamp distribution. stdlib; read-only.

This script measures executions as records, NOT independent taker orders.
A common exchange timestamp may include many fills from one or many orders.
Ex-post p99 threshold is descriptive and MUST NOT be used as an intraday causal signal.
"""
import argparse
import csv
import datetime as dt
import glob
import gzip
import json
import statistics
from pathlib import Path

COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
ROOT = "/data/cripta/datasets/raw/20260518_20260816"


def calculate(symbol, date, root=ROOT, minutes=1440):
    if symbol not in COHORT or not 1 <= minutes <= 1440:
        raise ValueError("invalid symbol/minutes")
    start = int(dt.datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    path = Path(root) / symbol / "public_trades" / f"{symbol}{date}.csv.gz"
    if not path.is_file():
        raise FileNotFoundError(str(path))
    times, amounts = [], []
    buys = sells = 0
    with gzip.open(path, "rt", newline="") as f:
        for row in csv.DictReader(f):
            t = float(row["timestamp"]) * 1000
            if not start <= t < start + minutes * 60000:
                continue
            if row["symbol"] != symbol or row["side"] not in ("Buy", "Sell"):
                raise ValueError("bad row side or symbol")
            times.append(t)
            amounts.append(float(row["foreignNotional"]))
            if row["side"] == "Buy":
                buys += 1
            else:
                sells += 1
    if not times:
        raise ValueError("no trades")
    if any(b < a for a, b in zip(times, times[1:])):
        raise ValueError("archive timestamps unsorted")
    positive_intervals = [(b-a) for a, b in zip(times, times[1:]) if b > a]
    values = sorted(amounts)
    p95 = values[int(.95*(len(values)-1))]
    p99 = values[int(.99*(len(values)-1))]
    return {
        "status": "RESEARCH_RESULT_NOT_CANON",
        "algorithm": "trade_distribution_v1",
        "source": str(path), "symbol": symbol, "date_utc": date,
        "minutes_requested": minutes, "fills": len(values), "buy_fills": buys,
        "sell_fills": sells,
        "positive_intertimestamp_gap_median_ms": statistics.median(positive_intervals) if positive_intervals else None,
        "fraction_adjacent_same_timestamp": 1 - len(positive_intervals) / max(1,len(times)-1),
        "fill_notional_usd_p95_ex_post": p95, "fill_notional_usd_p99_ex_post": p99,
        "fraction_notional_fills_ge_ex_post_p99": sum(x for x in values if x >= p99) / sum(values),
        "limitations": [
            "Fills are not independent taker orders; co-timestamped fills cannot be attributed to one order reliably.",
            "P99 is computed from entire requested window (ex-post); do not use for within-window predictive tests.",
            "Requires independently tested rolling as-of thresholds and independent time blocks before OOS evaluation.",
        ]
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", choices=COHORT, required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--root", default=ROOT)
    p.add_argument("--minutes", type=int, default=1440)
    a = p.parse_args()
    print(json.dumps(calculate(a.symbol, a.date, a.root, a.minutes), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
