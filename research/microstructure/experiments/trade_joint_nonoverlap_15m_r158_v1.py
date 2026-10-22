#!/usr/bin/env python3
"""CRIPTA research-only strict 15m non-overlap incremental factor screening.

Consumes independently QC'd first-month historical *event-time* 4-factor cubes.
NO second-month raw files, trading runtime, database, or Exchange I/O.
Primary anchor i%15==0: no overlapping 15-minute return intervals. Offset
sensitivity is excluded from confirmatory tests and can be computed separately.
Nested contrast: pair-true observations versus parent-true AND pair-false,
within each UTC day. Parent is selected using TRAIN20 only, not the late folds.
Exact day-level sign-flip permutation and fixed 45-test BH are EXPLORATORY:
late seven days were viewed previously, source receiver time unproven.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import pathlib
import shutil
import statistics
import sys
from itertools import product

SYMS = ("APTUSDT", "INJUSDT", "DOTUSDT", "LTCUSDT", "ARBUSDT")
SOURCE = pathlib.Path(
    "/data/cripta/research/runs/microstructure_joint_4f_20261023"
)
OUT = pathlib.Path(
    "/data/cripta/research/runs/microstructure_r158_strict_nonoverlap_15m_20261023"
)
REPO = pathlib.Path("/srv/cripta/source_checkout")
FEATURES = {
    "rate": "factor_2_trade_rate",
    "large": "factor_4_large_trade_share",
    "streak": "factor_8_max_timestamp_side_group_streak",
    "fast": "factor_27_median_positive_gap_ms",
    "size": "factor_28_minute_fill_notional_p90_usdt",
}
SINGLES = {
    "high_rate": ("rate",),
    "high_large": ("large",),
    "high_streak": ("streak",),
    "short_gap": ("fast",),
    "high_size": ("size",),
    "delta_sell": ("sell",),
    "delta_buy": ("buy",),
}
PAIRS = {
    "rate_sell": ("high_rate", "delta_sell"),
    "rate_buy": ("high_rate", "delta_buy"),
    "large_sell": ("high_large", "delta_sell"),
    "large_buy": ("high_large", "delta_buy"),
    "streak_fast": ("high_streak", "short_gap"),
    "large_size": ("high_large", "high_size"),
    "rate_size": ("high_rate", "high_size"),
    "fast_sell": ("short_gap", "delta_sell"),
}
SPLITS = {"TRAIN20": (0, 20), "VALID3": (20, 23), "LATE7": (23, 30)}
HORIZON = 15
PRIMARY_OFFSET = 0
MIN_DAILY_ARM = 3
TOTAL_HYPOTHESES = 45  # 5 instruments * (8 pairs + 1 high_rate)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def new_json(path, value):
    tmp = path.with_name(path.name + ".part")
    with tmp.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


def mean(xs):
    return sum(xs) / len(xs) if xs else None


def condition(row, name, thresholds, disabled):
    parts = SINGLES[name]
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
            value = row[FEATURES[part]]
            cutoff = thresholds[part]
            if value is None or cutoff is None:
                return False
            if part == "fast" and value > cutoff:
                return False
            if part != "fast" and value < cutoff:
                return False
    return True


def extract_nonoverlap(rows, fold):
    first, last = SPLITS[fold]
    lo, hi = first * 1440, last * 1440
    events = []
    for i in range(lo, hi - HORIZON, HORIZON):
        if i % HORIZON != PRIMARY_OFFSET:
            raise ValueError("INVALID_PRIMARY_ANCHOR")
        block = rows[i:i + HORIZON + 1]
        if len(block) != HORIZON + 1:
            raise ValueError("TRUNCATED_PRICE_WINDOW")
        prices = [r["price_close"] for r in block]
        if any(x is None or not math.isfinite(x) or x <= 0 for x in prices):
            continue
        if rows[i]["minute_index"] != i or rows[i + HORIZON]["minute_index"] != i + HORIZON:
            raise ValueError("NONCONTIGUOUS_SOURCE_MINUTES")
        ret = 10000 * math.log(prices[-1] / prices[0])
        events.append((i // 1440, rows[i], ret))
    return events


def daily_contrast(events, selected, control, min_arm=MIN_DAILY_ARM):
    days = {}
    for day, row, ret in events:
        test = selected(row)
        parent_only = control(row)
        if test and parent_only:
            raise ValueError("OVERLAPPING_NESTED_ARMS")
        arm = days.setdefault(day, {"test": [], "control": []})
        if test:
            arm["test"].append(ret)
        elif parent_only:
            arm["control"].append(ret)
    output = []
    for day in sorted(days):
        test, control_arm = days[day]["test"], days[day]["control"]
        if len(test) < min_arm or len(control_arm) < min_arm:
            continue
        test_abs = [abs(x) for x in test]
        ctrl_abs = [abs(x) for x in control_arm]
        output.append({
            "day_utc": (dt.date(2026, 5, 18) + dt.timedelta(days=day)).isoformat(),
            "n_test": len(test), "n_parent_only": len(control_arm),
            "mean_abs_bps_test": mean(test_abs),
            "mean_abs_bps_parent_only": mean(ctrl_abs),
            "difference_mean_abs_bps": mean(test_abs) - mean(ctrl_abs),
            "median_abs_bps_test": statistics.median(test_abs),
            "median_abs_bps_parent_only": statistics.median(ctrl_abs),
            "difference_median_abs_bps": statistics.median(test_abs) - statistics.median(ctrl_abs),
            "p_up_test": sum(x > 0 for x in test) / len(test),
            "p_up_parent_only": sum(x > 0 for x in control_arm) / len(control_arm),
            "difference_p_up_pp": 100 * (
                sum(x > 0 for x in test) / len(test)
                - sum(x > 0 for x in control_arm) / len(control_arm)
            ),
        })
    return output


def summarize(daily):
    return {
        "eligible_utc_days": len(daily),
        "test_count": sum(x["n_test"] for x in daily),
        "parent_only_count": sum(x["n_parent_only"] for x in daily),
        "day_equal_weight_mean_abs_bps_uplift": mean(
            [x["difference_mean_abs_bps"] for x in daily]
        ),
        "day_equal_weight_median_abs_bps_uplift": mean(
            [x["difference_median_abs_bps"] for x in daily]
        ),
        "day_equal_weight_up_pp": mean(
            [x["difference_p_up_pp"] for x in daily]
        ),
        "positive_amplitude_days": sum(x["difference_mean_abs_bps"] > 0 for x in daily),
        "daily": daily,
    }


def signflip_p(differences):
    n = len(differences)
    if n < 3 or n > 10:
        return None
    observed = abs(sum(differences))
    # Two-sided exhaustive paired-day sign-flip, preserving each UTC-day block.
    extreme = 0
    for signs in product((-1, 1), repeat=n):
        if abs(sum(s * x for s, x in zip(signs, differences))) >= observed - 1e-10:
            extreme += 1
    return extreme / (2 ** n)


def q_bh(pvalues):
    """BH family correction; missing/disabled comparisons count as p=1."""
    order = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    q = [1.0] * len(pvalues)
    smallest = 1.0
    for rank in range(len(order), 0, -1):
        idx = order[rank - 1]
        smallest = min(smallest, pvalues[idx] * len(pvalues) / rank)
        q[idx] = smallest
    return q


def get_inputs(symbol):
    physical = (
        SOURCE / "continuous_30d_v2" / (symbol + "_30d_v2")
    )
    source_file = physical / "features.jsonl.gz"
    phys_result = json.loads((physical / "result.json").read_text())
    receipt = json.loads((
        SOURCE / "batch_month_v3" / "results" / (symbol + "_qc_receipt.json")
    ).read_text())
    analytic_path = (
        SOURCE / "joint_price_response_30d_v2"
        / symbol / "joint_response.json"
    )
    old = json.loads(analytic_path.read_text())
    if (
        receipt["status"] != "FULL_4FACTOR_DECIMAL_INDEPENDENT_QC_PASS_NOT_RESEARCH_READY"
        or receipt["days"] != 30 or receipt["rows"] != 43200
        or receipt["symbol"] != symbol
        or sha(source_file) != receipt["feature_sha256"]
        or phys_result["features_sha256"] != receipt["feature_sha256"]
        or old["source_sha256"] != receipt["feature_sha256"]
        or old["symbol"] != symbol
    ):
        raise ValueError("FULL_QC_OR_PROVENANCE_GATE_FAILED")
    if old["period_utc"] != "2026-05-18..2026-06-16":
        raise ValueError("FROZEN_PERIOD_CHANGED")
    with gzip.open(source_file, "rt") as stream:
        rows = [json.loads(line) for line in stream]
    if len(rows) != 43200 or any(
        r["minute_index"] != idx or r["instrument_id"] != symbol
        for idx, r in enumerate(rows)
    ):
        raise ValueError("PHYSICAL_CUBE_INDEX_OR_ID_MISMATCH")
    return rows, old, {
        "cube_sha256": receipt["feature_sha256"],
        "prior_joint_sha256": sha(analytic_path),
        "source_file": str(source_file),
        "prior_joint_file": str(analytic_path),
        "qc_receipt": str(
            SOURCE / "batch_month_v3" / "results" / (symbol + "_qc_receipt.json")
        ),
    }


def choose_parent(train, pair, thresholds, disabled):
    parents = PAIRS[pair]
    records = []
    for p in parents:
        xs = [
            abs(ret) for _day, row, ret in train
            if condition(row, p, thresholds, disabled)
        ]
        records.append((mean(xs) if len(xs) >= 30 else -float("inf"), p, len(xs)))
    records.sort(reverse=True)
    if not math.isfinite(records[0][0]):
        return None
    return {"parent": records[0][1], "train_parent_mean_abs_bps": records[0][0],
            "train_parent_count": records[0][2]}


def run(symbol):
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("ACTOR_NOT_CRIPTA")
    if symbol not in SYMS:
        raise ValueError("UNDECLARED_INSTRUMENT")
    if not os.access(OUT.parent, os.W_OK | os.X_OK):
        raise PermissionError("RESEARCH_PARENT_NOT_WRITABLE")
    target = OUT / symbol
    if target.exists():
        raise FileExistsError("NO_OVERWRITE " + str(target))
    rows, old, lineage = get_inputs(symbol)
    threshold, disabled = old["thresholds"], old["disabled"]
    folds = {fold: extract_nonoverlap(rows, fold) for fold in SPLITS}
    if any(not ev for ev in folds.values()):
        raise ValueError("EMPTY_VALID_FOLD")
    results = {}
    for name in PAIRS:
        p1, p2 = PAIRS[name]
        dis = any(x in disabled for single in (p1, p2) for x in SINGLES[single])
        if dis:
            results[name] = {"status": "DISABLED_TRAIN_DEGENERATE_FACTOR",
                             "parent": None, "folds": {}}
            continue
        parent = choose_parent(folds["TRAIN20"], name, threshold, disabled)
        if parent is None:
            results[name] = {"status": "INSUFFICIENT_TRAIN_PARENT",
                             "parent": None, "folds": {}}
            continue
        chosen = parent["parent"]
        def both(row):
            return (
                condition(row, p1, threshold, disabled)
                and condition(row, p2, threshold, disabled)
            )
        def control(row):
            return condition(row, chosen, threshold, disabled) and not both(row)
        result = {
            "status": "EXPLORATORY_NESTED_NONOVERLAP",
            "parent": parent,
            "folds": {
                fold: summarize(daily_contrast(events, both, control))
                for fold, events in folds.items()
            },
        }
        later = (
            result["folds"]["VALID3"]["daily"]
            + result["folds"]["LATE7"]["daily"]
        )
        result["later10_day_signflip_p_exploratory"] = signflip_p([
            x["difference_mean_abs_bps"] for x in later
        ])
        results[name] = result
    # The family includes the high Trade Rate singleton for amplitude context.
    baseline = {}
    for fold, events in folds.items():
        def selected(row):
            return condition(row, "high_rate", threshold, disabled)
        def complement(row):
            return not selected(row)
        baseline[fold] = summarize(daily_contrast(events, selected, complement))
    later = baseline["VALID3"]["daily"] + baseline["LATE7"]["daily"]
    singleton_p = signflip_p([
        x["difference_mean_abs_bps"] for x in later
    ])
    result = {
        "status": "NONOVERLAP_NESTED_EXPLORATORY_NOT_INDEPENDENT_OOS",
        "symbol": symbol,
        "period_utc": "2026-05-18..2026-06-16",
        "minute_horizon": 15, "primary_anchor_minute_mod_15": 0,
        "split_day_ranges": SPLITS,
        "min_daily_test_and_control": MIN_DAILY_ARM,
        "num_primary_eligible_events_by_fold": {
            k: len(v) for k, v in folds.items()
        },
        "thresholds_frozen_from_previous_train20_analysis": threshold,
        "disabled": disabled, "pairs": results,
        "high_rate_vs_not_high_rate": {
            "folds": baseline,
            "later10_day_signflip_p_exploratory": singleton_p,
        },
        "input": lineage,
        "research_ready": False,
        "limitations": [
            "Last seven days were previously investigated: NOT untouched OOS",
            "Receiver availability after public-trade event-time is UNKNOWN",
            "Pair vs parent-only means compare nested non-overlapping groups",
            "UTC-day blocks may be serially dependent; small late-day sample",
            "Other anchor offsets were not pooled or cherry-picked",
            "Multiple-test correction is exploratory and assigned at cohort consolidation",
            "No execution, fees/slippage, MFE/MAE or Strategy/live policy",
        ],
    }
    os.umask(0o007)
    OUT.mkdir(mode=0o2770, exist_ok=True)
    target.mkdir(mode=0o2770, exist_ok=False)
    for folder in ("logs", "results", "source_snapshot"):
        (target / folder).mkdir(mode=0o2770, exist_ok=False)
    script = pathlib.Path(__file__).resolve()
    shutil.copyfile(script, target / "source_snapshot" / "screen.py")
    source_commit = (REPO / ".git/refs/heads/main").read_text().strip()
    (target / "source_commit.txt").write_text(source_commit + "\n")
    (target / "source_sha256.txt").write_text(sha(script) + "\n")
    (target / "command.txt").write_text(
        sys.executable + " -B " + str(script) + " --symbol " + symbol + "\n"
    )
    (target / "environment.txt").write_text(
        "actor_uid=" + str(os.geteuid()) + "\n"
        "python=" + sys.executable + "\n"
        "python_version=" + sys.version.split()[0] + "\n"
    )
    new_json(target / "input_provenance.json", lineage)
    new_json(target / "run_manifest.json", {
        "status": "RUNNING", "actor_uid": os.geteuid(),
        "source_commit": source_commit, "program_sha256": sha(script),
        "symbol": symbol, "first_month_only": True,
        "test_family_size": TOTAL_HYPOTHESES,
    })
    new_json(target / "results" / "nonoverlap15m.json", result)
    new_json(target / "results" / "index.json", {
        "status": "COMPLETED_EXPLORATORY_NOT_RESEARCH_READY",
        "output_sha256": sha(target / "results" / "nonoverlap15m.json"),
        "source_commit": source_commit,
    })
    print(json.dumps({
        "status": "COMPLETED_EXPLORATORY_NOT_RESEARCH_READY",
        "symbol": symbol, "source_commit": source_commit,
        "results": str(target / "results" / "nonoverlap15m.json"),
        "sha256": sha(target / "results" / "nonoverlap15m.json"),
        "folds_n": result["num_primary_eligible_events_by_fold"],
        "high_rate_later10_signflip_p": singleton_p,
        "pair_later10_p": {
            k: v.get("later10_day_signflip_p_exploratory")
            for k, v in results.items()
        },
    }, sort_keys=True))


def consolidate():
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise PermissionError("ACTOR_NOT_CRIPTA")
    entries = []
    summaries = []
    for sym in SYMS:
        p = OUT / sym / "results" / "nonoverlap15m.json"
        idx = json.loads((OUT / sym / "results" / "index.json").read_text())
        if idx["output_sha256"] != sha(p):
            raise ValueError("COHORT_RESULT_SHA_DRIFT " + sym)
        record = json.loads(p.read_text())
        if record["symbol"] != sym or record["minute_horizon"] != 15:
            raise ValueError("COHORT_WRONG_SCOPE")
        entries.append({"symbol": sym, "file": str(p), "sha256": sha(p)})
        summaries.append(record)
    family = []
    for record in summaries:
        symbol = record["symbol"]
        family.append({
            "symbol": symbol, "condition": "high_rate",
            "p": record["high_rate_vs_not_high_rate"][
                "later10_day_signflip_p_exploratory"
            ],
            "effect": {
                x: record["high_rate_vs_not_high_rate"]["folds"][x][
                    "day_equal_weight_mean_abs_bps_uplift"
                ] for x in ("VALID3", "LATE7")
            },
        })
        for name in PAIRS:
            p = record["pairs"][name]
            family.append({
                "symbol": symbol, "condition": name,
                "parent": p.get("parent", {}).get("parent") if p.get("parent") else None,
                "p": p.get("later10_day_signflip_p_exploratory"),
                "effect": {
                    x: p.get("folds", {}).get(x, {}).get(
                        "day_equal_weight_mean_abs_bps_uplift"
                    ) for x in ("VALID3", "LATE7")
                },
            })
    if len(family) != TOTAL_HYPOTHESES:
        raise ValueError("FAMILY_SIZE_DRIFT")
    qvalues = q_bh([x["p"] if x["p"] is not None else 1.0 for x in family])
    for row, q in zip(family, qvalues):
        row["exploratory_bh_q"] = q
    top = sorted(family, key=lambda x: x["exploratory_bh_q"])
    output = {
        "status": "COHORT_NONOVERLAP_NESTED_SCREEN_COMPLETE_NOT_RESEARCH_READY",
        "period_utc": "2026-05-18..2026-06-16",
        "horizon_minutes": 15, "only_anchor_offset": 0,
        "hypothesis_family_size": TOTAL_HYPOTHESES,
        "instrument_lineage": entries, "tests": top,
        "limitations": [
            "Not virgin OOS: LATE7 previously reviewed",
            "Day-level signflip is preliminary, not robust serial-dependence correction",
            "BH applies only to this prespecified 45-condition 15m amplitude family",
            "Non-overlapping 15-minute horizons reduce but do not eliminate day dependence",
            "Receiver-time feature availability unknown; NO LIVE implications",
            "No after-fee PnL; second-month holdout not read",
        ],
    }
    dest = OUT / "cohort_nonoverlap15m.json"
    new_json(dest, output)
    print(json.dumps({
        "status": output["status"], "file": str(dest),
        "sha256": sha(dest), "tests": len(family),
        "q_under_0_10": [
            {"symbol": x["symbol"], "condition": x["condition"],
             "q": x["exploratory_bh_q"]}
            for x in top if x["exploratory_bh_q"] < 0.10
        ],
        "top": top[:12],
    }, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--symbol", choices=SYMS)
    mode.add_argument("--consolidate", action="store_true")
    args = parser.parse_args()
    if args.symbol:
        run(args.symbol)
    else:
        consolidate()
