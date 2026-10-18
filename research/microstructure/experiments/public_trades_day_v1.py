#!/usr/bin/env python3
"""Historical Bybit public-trades feature inventory. RESEARCH ONLY, stdlib, no writes.

Examples:
  python3 public_trades_day_v1.py --symbol APTUSDT --date 2026-05-19
  python3 public_trades_day_v1.py --symbol LTCUSDT --date 2026-05-19 --minutes 60

This script is *not* a trading decision rule. Data are read from immutable gzip
archives and no filesystem / database / exchange mutations are performed.
Do not infer OOS results or profitability from descriptive statistics.
"""
from __future__ import annotations
import argparse
import csv
import datetime as dt
import glob
import gzip
import json
import math
import statistics
from pathlib import Path

DEFAULT_ROOT = "/data/cripta/datasets/raw/20260518_20260816"
COHORT = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")


def calculate(symbol: str, day: str, root: str, minutes: int) -> dict:
    start = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    start_ms = int(start.timestamp() * 1000)
    if not 1 <= minutes <= 1440:
        raise ValueError("minutes must be within 1..1440")
    pattern = str(Path(root) / symbol / "public_trades" / f"{symbol}{day}.csv.gz")
    matches = glob.glob(pattern)
    if len(matches) != 1:
        raise FileNotFoundError(f"Expected exactly one archive for {pattern}; found {len(matches)}")
    archive = matches[0]
    # [count, notional USD, signed-notional USD, price*notional,
    #  last_price, max_trade, min_trade]
    m = [[0, 0.0, 0.0, 0.0, None, None, None] for _ in range(minutes)]
    volume_at_price: dict[float, float] = {}
    n = 0
    with gzip.open(archive, "rt", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            timestamp_ms = int(float(row["timestamp"]) * 1000)
            i = (timestamp_ms - start_ms) // 60000
            if i < 0 or i >= minutes:
                continue
            if row["symbol"] != symbol or row["side"] not in ("Buy", "Sell"):
                raise ValueError("Unexpected symbol or taker side")
            price = float(row["price"])
            notional = float(row["foreignNotional"])
            if not (math.isfinite(price) and price > 0 and math.isfinite(notional) and notional >= 0):
                raise ValueError("Invalid price/notional")
            side = 1 if row["side"] == "Buy" else -1
            x = m[i]
            x[0] += 1
            x[1] += notional
            x[2] += side * notional
            x[3] += price * notional
            x[4] = price
            x[5] = max(x[5], price) if x[5] is not None else price
            x[6] = min(x[6], price) if x[6] is not None else price
            volume_at_price[price] = volume_at_price.get(price, 0.0) + notional
            n += 1
    total = sum(x[1] for x in m)
    if n == 0 or total <= 0:
        raise ValueError("No usable trade data")
    rv = []
    for i in range(60, minutes):
        baseline = sum(m[j][1] for j in range(i-60, i)) / 60
        if baseline > 0:
            rv.append(m[i][1] / baseline)
    acceleration = [m[i][2] - m[i-1][2] for i in range(1, minutes)]
    close = []
    last = None
    for x in m:
        if x[4] is not None:
            last = x[4]
        close.append(last)
    returns = [math.log(close[i] / close[i-1]) for i in range(60, minutes)
               if close[i] is not None and close[i-1] is not None]
    def lag1(vals: list[float]):
        if len(vals) < 3:
            return None
        left, right = vals[:-1], vals[1:]
        a, b = statistics.mean(left), statistics.mean(right)
        num = sum((x-a)*(y-b) for x, y in zip(left, right))
        den = math.sqrt(sum((x-a)**2 for x in left)*sum((y-b)**2 for y in right))
        return num/den if den else None
    range20 = []
    for i in range(19, minutes):
        w = close[i-19:i+1]
        if all(p is not None for p in w):
            range20.append(100*(max(w)-min(w))/w[-1])
    poc = max(volume_at_price, key=volume_at_price.get)
    return {
        "status": "RESEARCH_RESULT_NOT_CANON", "algorithm": "public_trades_day_v1",
        "symbol": symbol, "date_utc": day, "minutes_requested": minutes,
        "source": archive, "trades": n, "trade_minutes": sum(x[0]>0 for x in m),
        "notional_usd": total, "signed_delta_cvd_usd": sum(x[2] for x in m),
        "vwap_ex_post": sum(x[3] for x in m)/total,
        "volume_poc_price_ex_post": poc,
        "rvol_60m_median": statistics.median(rv) if rv else None,
        "rvol_60m_p90": sorted(rv)[int(.9*len(rv))] if rv else None,
        "delta_accel_median_usd": statistics.median(acceleration) if acceleration else None,
        "lag1_1min_log_return_autocorrelation": lag1(returns),
        "close_range_20m_median_pct": statistics.median(range20) if range20 else None,
        "limitations": [
          "Day VWAP and volume POC are ex-post; never use for intraday prediction.",
          "CVD origin is window start, not historical market origin.",
          "Short windows may not support 60-minute baseline or return correlation.",
          "No fees, OOS, orderbook, liquidations, OI or cross-venue validation.",
        ],
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", choices=COHORT, required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD in UTC")
    p.add_argument("--minutes", type=int, default=1440)
    p.add_argument("--root", default=DEFAULT_ROOT)
    a = p.parse_args()
    print(json.dumps(calculate(a.symbol, a.date, a.root, a.minutes), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
