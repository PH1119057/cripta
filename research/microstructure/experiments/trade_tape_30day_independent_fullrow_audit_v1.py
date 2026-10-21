#!/usr/bin/env python3
"""CRIПTA research: bounded independent Decimal QC, bounded source-verified May18-June16 control period.

Run read-only as Unix cripta. No writes, no DB, no network, stdout JSON.
Input files: Bybit public_trades CSV.GZ per exact coin/day.
Primary reader: trade_minute_factors_v2_exact_timestamp_floor.py pinned Git blob.
No receipt/observed clock in raw CSV; no RESEARCH-READY or profitability claim.
"""
import csv
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
import math
import statistics
import sys
from decimal import Decimal
from pathlib import Path

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
START_DAY = dt.date(2026, 5, 18)
LAST_DAY = dt.date(2026, 6, 16)
DATES = tuple((START_DAY+dt.timedelta(days=i)).isoformat() for i in range((LAST_DAY-START_DAY).days+1))
ROOT = Path("/data/cripta/datasets/raw/20260518_20260816")
PRIMARY = Path("/srv/cripta/source_checkout/research/microstructure/experiments/trade_minute_factors_v2_exact_timestamp_floor.py")
PINNED_BLOB = "71e4851faebd388fd2a6db3d93dca2ca2cb28c07"
FIELDS = ("fill_count", "buy_notional_usd", "sell_notional_usd", "delta_usd",
          "cvd_from_day_start_usd", "trade_rate_accel_per_min", "delta_accel_usd", "rvol_60m")

def compute(symbol, day):
    if symbol not in SYMS or day not in DATES:
        raise ValueError("Not an agreed control symbol or date")
    source = PRIMARY.read_bytes()
    blob = hashlib.sha1(b"blob " + str(len(source)).encode() + b"\0" + source).hexdigest()
    if blob != PINNED_BLOB:
        raise ValueError("Primary Git blob drift: " + blob)
    spec = importlib.util.spec_from_file_location("frozen_minute_primary", PRIMARY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    primary = module.calculate(symbol, day)
    assert len(primary["rows"]) == 1440
    path = ROOT / symbol / "public_trades" / (symbol + day + ".csv.gz")
    start = Decimal(int(dt.datetime.strptime(day, "%Y-%m-%d").replace(
        tzinfo=dt.timezone.utc).timestamp()))
    buckets = [dict(count=0, buy=Decimal(0), sell=Decimal(0), fills=[])
               for _ in range(1440)]
    notional = []
    gaps = []
    streaks = []
    seen_ids = set()
    prev_ts = prev_key = prev_side = None
    group_size = 0
    streak = group_count = multi_group = dup = bad_value = bad_home = outside = 0
    with gzip.open(path, "rt", newline="") as stream:
        for row in csv.DictReader(stream):
            t = Decimal(row["timestamp"])
            price = Decimal(row["price"])
            qty = Decimal(row["size"])
            amount = Decimal(row["foreignNotional"])
            side = row["side"]
            assert row["symbol"] == symbol and side in ("Buy", "Sell")
            assert price > 0 and qty > 0 and amount >= 0
            assert prev_ts is None or t >= prev_ts
            i = int((t - start) // 60)
            if not 0 <= i < 1440:
                outside += 1
                continue
            b = buckets[i]
            b["count"] += 1
            b["buy" if side == "Buy" else "sell"] += amount
            b["fills"].append(amount)
            notional.append(amount)
            trade_id = row["trdMatchID"]
            dup += int(trade_id in seen_ids)
            seen_ids.add(trade_id)
            bad_value += int(abs(price * qty - amount) > Decimal("0.00000001"))
            bad_home += int(Decimal(row["homeNotional"]) != qty)
            key = (t, side)
            if key != prev_key:
                if group_size:
                    group_count += 1
                    multi_group += int(group_size > 1)
                if prev_side is not None and side != prev_side:
                    streaks.append(streak)
                    streak = 0
                streak += 1
                group_size = 1
                prev_side = side
                prev_key = key
            else:
                group_size += 1
            if prev_ts is not None and t > prev_ts:
                gaps.append((t - prev_ts) * 1000)
            prev_ts = t
    if group_size:
        group_count += 1
        multi_group += int(group_size > 1)
    if streak:
        streaks.append(streak)
    mismatches = {key: 0 for key in FIELDS}
    cvd = Decimal(0)
    prev_count = prev_delta = None
    eligible_rvol = 0
    for i, b in enumerate(buckets):
        delta = b["buy"] - b["sell"]
        cvd += delta
        hist = sum((buckets[j]["buy"] + buckets[j]["sell"]
                    for j in range(max(0, i - 60), i)), Decimal(0))
        expected = {
            "fill_count": b["count"],
            "buy_notional_usd": b["buy"],
            "sell_notional_usd": b["sell"],
            "delta_usd": delta,
            "cvd_from_day_start_usd": cvd,
            "trade_rate_accel_per_min": b["count"] - prev_count if prev_count is not None else None,
            "delta_accel_usd": delta - prev_delta if prev_delta is not None else None,
            "rvol_60m": (b["buy"] + b["sell"]) / (hist / 60) if i >= 60 and hist > 0 else None
        }
        eligible_rvol += int(expected["rvol_60m"] is not None)
        for key, value in expected.items():
            actual = primary["rows"][i][key]
            good = ((value is None and actual is None)
                    if value is None or actual is None
                    else math.isclose(float(value), float(actual),
                                      rel_tol=1e-10, abs_tol=1e-6))
            mismatches[key] += int(not good)
        prev_count = b["count"]
        prev_delta = delta
    # Large Trade Share: current minute scored against p99 from strictly prior 60 minutes.
    ratios = []
    for i in range(60, 1440):
        if not buckets[i]["fills"]:
            continue
        history = sorted(v for j in range(i - 60, i) for v in buckets[j]["fills"])
        if len(history) < 100:
            continue
        threshold = history[int(.99 * (len(history) - 1))]
        denom = sum(buckets[i]["fills"], Decimal(0))
        if denom > 0:
            ratios.append(sum((v for v in buckets[i]["fills"] if v >= threshold),
                              Decimal(0)) / denom)
    values = sorted(notional)
    return {
        "symbol": symbol, "day": day, "primary_source_blob": blob,
        "raw_gz_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source_rows": len(values), "nonempty_minutes": sum(b["count"] > 0 for b in buckets),
        "rv_minutes": eligible_rvol, "full_1440_8fields_pass": all(x == 0 for x in mismatches.values()),
        "per_field_mismatch_counts": mismatches,
        "duplicate_trade_ids": dup, "notional_mismatch": bad_value,
        "home_notional_mismatch": bad_home, "outside_day_rows": outside,
        "timestamp_side_groups": group_count, "groups_with_multiple_fills": multi_group,
        "streak_median_groups": float(statistics.median(streaks)),
        "streak_max_groups": max(streaks),
        "positive_intertimestamp_gap_median_ms": float(statistics.median(gaps)) if gaps else None,
        "expost_p95_fill_usdt": float(values[int(.95 * (len(values) - 1))]),
        "expost_p99_fill_usdt": float(values[int(.99 * (len(values) - 1))]),
        "causal_large_share_median": float(statistics.median(ratios)) if ratios else None,
        "eligible_minutes_large_share": len(ratios),
        "sum_buy_usdt": str(sum((b["buy"] for b in buckets), Decimal(0))),
        "sum_sell_usdt": str(sum((b["sell"] for b in buckets), Decimal(0))),
        "availability": "UTC minute-end is a derived floor only; actual receiver clock unavailable",
        "scope": "Single UTC control day in bounded 30-day sample; not owner-selected full period"
    }

def period(symbol, start, days):
    import time
    if symbol not in SYMS or start not in DATES or not 1 <= days <= 30:
        raise ValueError("unsupported instrument/start/count")
    start_date = dt.date.fromisoformat(start)
    requested = [(start_date+dt.timedelta(days=i)).isoformat() for i in range(days)]
    if any(d not in DATES for d in requested):
        raise ValueError("outside evidenced coverage")
    t0 = time.monotonic()
    entries = []
    for day in requested:
        res = compute(symbol, day)
        entries.append(res)
    return dict(symbol=symbol,requested_days=days,processed_days=len(entries),
                elapsed_seconds=round(time.monotonic()-t0,2),
                total_fills=sum(x["source_rows"] for x in entries),
                minute_field_comparisons=len(entries)*1440*len(FIELDS),
                total_mismatches=sum(sum(x["per_field_mismatch_counts"].values()) for x in entries),
                daily=entries, source="Bybit public_trades",
                source_identity=PINNED_BLOB,
                status="CONTROL_PERIOD_30D_MAX_NOT_RESEARCH_READY")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", choices=SYMS, required=True)
    parser.add_argument("--start", default="2026-05-18")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    print(json.dumps(period(args.symbol,args.start,args.days),ensure_ascii=False,separators=(",", ":")))

