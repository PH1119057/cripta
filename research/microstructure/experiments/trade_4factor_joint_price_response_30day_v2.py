#!/usr/bin/env python3
"""Research-only descriptive joint price-response study; NOT trading/OOS proof.

Uses physically measured event-time trade features (not proven receiver-known).
Training 20 complete UTC days calibrates factor cutoffs without future labels.
Validation next 3 UTC days, late diagnostic final 7 UTC days; each response
horizon must remain entirely within the same split. Dependent, overlapping
minute outcomes are NOT independent events/trades. No fees or execution model.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import pwd
import shutil
import statistics
from pathlib import Path

SRC = Path("/data/cripta/research/runs/microstructure_joint_4f_20261023")
SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
FEATURES = {
    "rate": "factor_2_trade_rate",
    "large": "factor_4_large_trade_share",
    "streak": "factor_8_max_timestamp_side_group_streak",
    "fast": "factor_27_median_positive_gap_ms",
    "size": "factor_28_minute_fill_notional_p90_usdt",
}
CONDITIONS = {
    "baseline": (),
    "high_rate": ("rate",),
    "high_large": ("large",),
    "high_streak": ("streak",),
    "short_gap": ("fast",),
    "high_size": ("size",),
    "delta_sell": ("sell",),
    "delta_buy": ("buy",),
    "rate_sell": ("rate", "sell"),
    "rate_buy": ("rate", "buy"),
    "large_sell": ("large", "sell"),
    "large_buy": ("large", "buy"),
    "streak_fast": ("streak", "fast"),
    "large_size": ("large", "size"),
    "rate_size": ("rate", "size"),
    "fast_sell": ("fast", "sell"),
}
SPLITS = {"TRAIN20": (0, 20), "VALID3": (20, 23), "LATE7": (23, 30)}
HORIZONS = (5, 15, 60)


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def quantile(vals, fraction):
    ordered = sorted(vals)
    return ordered[int(fraction * (len(ordered) - 1))] if ordered else None


def percentile(values, fraction):
    return quantile(values, fraction) if values else None


def select(row, parts, cutoffs, disabled):
    for part in parts:
        if part in disabled:
            return False
        if part == "sell":
            if row["factor_5_delta_usdt"] is None or row["factor_5_delta_usdt"] >= 0:
                return False
        elif part == "buy":
            if row["factor_5_delta_usdt"] is None or row["factor_5_delta_usdt"] <= 0:
                return False
        else:
            actual = row[FEATURES[part]]
            threshold = cutoffs[part]
            if actual is None or threshold is None:
                return False
            if part == "fast":
                if actual > threshold:
                    return False
            elif actual < threshold:
                return False
    return True


def measure(returns):
    if not returns:
        return {"n": 0, "up_pct": None, "down_pct": None}
    n = len(returns)
    magnitudes = [abs(x) for x in returns]
    return {
        "n": n,
        "up_pct": round(100 * sum(x > 0 for x in returns) / n, 4),
        "down_pct": round(100 * sum(x < 0 for x in returns) / n, 4),
        "zero_pct": round(100 * sum(x == 0 for x in returns) / n, 4),
        "mean_return_bps": round(sum(returns) / n, 5),
        "median_abs_bps": round(statistics.median(magnitudes), 5),
        "p90_abs_bps": round(percentile(magnitudes, 0.9), 5),
        "abs_gte_20bps_pct": round(100 * sum(x >= 20 for x in magnitudes) / n, 4),
        "abs_gte_50bps_pct": round(100 * sum(x >= 50 for x in magnitudes) / n, 4),
        "abs_gte_100bps_pct": round(100 * sum(x >= 100 for x in magnitudes) / n, 4),
    }


def run(symbol):
    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("ACTOR_NOT_CRIPTA")
    inp = SRC / "continuous_30d_v2" / (symbol + "_30d_v2")
    prior_result = json.loads((inp / "result.json").read_text())
    if prior_result["status"] != (
        "PHYSICAL_4FACTOR_COMPLETED_NOT_INDEPENDENT_QC_NOT_RESEARCH_READY"
    ):
        raise ValueError("NOT_CORRECT_PHYSICAL_SOURCE_STATUS")
    source = inp / "features.jsonl.gz"
    if sha256(source) != prior_result["features_sha256"]:
        raise ValueError("PHYSICAL_INPUT_SHA_MISMATCH")
    with gzip.open(source, "rt") as stream:
        rows = [json.loads(line) for line in stream]
    if len(rows) != 30 * 1440:
        raise ValueError("EXPECTED_30_DAYS_MINUTES")
    cutoffs = {}
    disabled = {}
    for key, col in FEATURES.items():
        train = [
            row[col] for row in rows[:20 * 1440] if row[col] is not None
        ]
        cutoff = quantile(train, 0.25 if key == "fast" else 0.75)
        cutoffs[key] = cutoff
        # An inclusive cutoff selecting ~all training rows is not informative.
        selected = sum(
            (x <= cutoff if key == "fast" else x >= cutoff) for x in train
        ) / len(train) if train else 0
        if not 0.03 < selected < 0.90:
            disabled[key] = {
                "reason": "DEGENERATE_TRAIN_QUANTILE_SELECTION",
                "train_selected_fraction": selected,
            }
    result = {}
    for split, (d0, d1) in SPLITS.items():
        result[split] = {}
        lo, hi = d0 * 1440, d1 * 1440
        for horizon in HORIZONS:
            conditions = {}
            valid = []
            for i in range(lo, hi - horizon):
                today = rows[i]["price_close"]
                future = rows[i + horizon]["price_close"]
                if today is None or future is None:
                    continue
                if any(rows[k]["price_close"] is None for k in range(i + 1, i + horizon + 1)):
                    continue
                delta = 10000 * math.log(future / today)
                valid.append((i, delta))
            for name, parts in CONDITIONS.items():
                if any(x in disabled for x in parts):
                    conditions[name] = {
                        "status": "DISABLED_DEGENERATE_TRAIN_THRESHOLD",
                        "n": 0,
                    }
                    continue
                relevant = [
                    (i, r) for i, r in valid
                    if select(rows[i], parts, cutoffs, disabled)
                ]
                overall = measure([r for _, r in relevant])
                daily = []
                for day in range(d0, d1):
                    subset = [r for i, r in relevant if i // 1440 == day]
                    if subset:
                        daily.append({
                            "day_utc": (
                                dt.date(2026, 5, 18) + dt.timedelta(days=day)
                            ).isoformat(),
                            "n": len(subset),
                            "up_pct": round(
                                100 * sum(x > 0 for x in subset) / len(subset), 3
                            ),
                            "median_abs_bps": round(
                                statistics.median(abs(x) for x in subset), 3
                            ),
                        })
                conditions[name] = {
                    "status": "EXPLORATORY_DEPENDENT_MINUTE_OUTCOMES",
                    **overall,
                    "distinct_utc_days_with_observations": len(daily),
                    "daily_summary": daily,
                }
            result[split][str(horizon)] = conditions
    path = SRC / "joint_price_response_30d_v2"
    path.mkdir(mode=0o2770, exist_ok=True)
    run_dir = path / symbol
    run_dir.mkdir(mode=0o2770, exist_ok=False)
    script = Path(__file__).resolve()
    shutil.copyfile(script, run_dir / "source_snapshot.py")
    payload = {
        "status": "INTERNAL_TIME_SPLIT_EXPLORATORY_NOT_OOS_NOT_RESEARCH_READY",
        "symbol": symbol,
        "source_file": str(source),
        "source_sha256": prior_result["features_sha256"],
        "program_sha256": sha256(script),
        "period_utc": "2026-05-18..2026-06-16",
        "fit": "20 UTC days; train-only q25 gaps and q75 other factors",
        "split": SPLITS,
        "horizons_minutes": HORIZONS,
        "thresholds": cutoffs, "disabled": disabled, "results": result,
        "limitations": [
            "15m overlapping minute targets are dependent, not trades",
            "A later-seven-day internal split was investigated in old research",
            "Train calibrated thresholds do not imply independent OOS",
            "Receiver-available feature times remain unproven",
            "Future labels use last execution price endpoints, not fillable prices",
            "No fees, slippage, Entry/Exit contract or money PnL",
            "Multiple conditions investigated; selection bias is uncorrected",
            "Conditional correlation is not causal market impact",
        ],
    }
    p = run_dir / "joint_response.json"
    with p.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({
        "status": payload["status"],
        "symbol": symbol,
        "path": str(p),
        "output_sha256": sha256(p),
        "disabled": disabled,
        "valid3_15m_baseline": result["VALID3"]["15"]["baseline"],
        "late7_15m_baseline": result["LATE7"]["15"]["baseline"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", choices=SYMS, required=True)
    args = p.parse_args()
    run(args.symbol)
