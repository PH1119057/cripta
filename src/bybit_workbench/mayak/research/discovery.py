from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from bybit_workbench.mayak.research.event_truth import PrimaryLabel

CONTINUATION = PrimaryLabel.CONTINUATION_1_10.value
INITIAL_STOP = PrimaryLabel.INITIAL_OR_PRE05_STOP.value
PLUS05_STOP = PrimaryLabel.PLUS_050_TO_MINUS_050.value
NON_FEATURES = {
    "event_id",
    "symbol",
    "side",
    "anchor_time",
    "feature_cutoff",
    "window_offset_minutes",
    "primary_label",
    "isolated_or_clustered",
    "portfolio_hour_severity",
    "portfolio_day_severity",
    "feature_spec_fingerprint",
}


@dataclass(frozen=True, slots=True)
class Comparison:
    feature: str
    window_offset_minutes: int
    good_label: str
    bad_label: str
    good_n: int
    bad_n: int
    good_median: float
    bad_median: float
    good_q25: float
    good_q75: float
    bad_q25: float
    bad_q75: float
    standardized_effect: float
    median_difference: float
    bootstrap_ci_low: float
    bootstrap_ci_high: float
    asset_direction_consistency: float
    largest_asset_share: float
    bad_separation: float
    good_false_alarm_rate: float


def analyze_discovery(
    rows: list[dict[str, Any]], *, bootstrap_samples: int = 300
) -> dict[str, Any]:
    if not rows:
        raise ValueError("discovery rows cannot be empty")
    feature_names = tuple(key for key in rows[0] if key not in NON_FEATURES)
    comparisons: list[Comparison] = []
    for offset in sorted({int(row["window_offset_minutes"]) for row in rows}):
        window = [row for row in rows if int(row["window_offset_minutes"]) == offset]
        for feature in feature_names:
            for bad_label in (INITIAL_STOP, PLUS05_STOP):
                comparisons.append(
                    _compare(window, feature, offset, CONTINUATION, bad_label, bootstrap_samples)
                )
    diagnostic_comparisons: list[Comparison] = []
    for offset in sorted({int(row["window_offset_minutes"]) for row in rows}):
        window = [row for row in rows if int(row["window_offset_minutes"]) == offset]
        continuation_rows = [row for row in window if row["primary_label"] == CONTINUATION]
        clustered_rows = [
            {**row, "primary_label": "FUTURE_CLUSTERED_FAILURE"}
            for row in window
            if row["isolated_or_clustered"] == "clustered"
        ]
        isolated_rows = [
            {**row, "primary_label": "FUTURE_ISOLATED_FAILURE"}
            for row in window
            if row["isolated_or_clustered"] == "isolated"
            and row["primary_label"] in {INITIAL_STOP, PLUS05_STOP}
        ]
        for feature in feature_names:
            diagnostic_comparisons.append(
                _compare(
                    continuation_rows + clustered_rows,
                    feature,
                    offset,
                    CONTINUATION,
                    "FUTURE_CLUSTERED_FAILURE",
                    bootstrap_samples,
                )
            )
            diagnostic_comparisons.append(
                _compare(
                    isolated_rows + clustered_rows,
                    feature,
                    offset,
                    "FUTURE_ISOLATED_FAILURE",
                    "FUTURE_CLUSTERED_FAILURE",
                    bootstrap_samples,
                )
            )
    by_candidate: dict[tuple[str, int], list[Comparison]] = defaultdict(list)
    for result in comparisons:
        by_candidate[(result.feature, result.window_offset_minutes)].append(result)
    selected: list[dict[str, Any]] = []
    for (feature, offset), items in by_candidate.items():
        if len(items) != 2:
            continue
        directions = {math.copysign(1.0, item.median_difference) for item in items}
        ci_excludes_zero = all(
            item.bootstrap_ci_low > 0 or item.bootstrap_ci_high < 0 for item in items
        )
        if (
            len(directions) == 1
            and ci_excludes_zero
            and min(abs(item.standardized_effect) for item in items) >= 0.20
            and min(item.asset_direction_consistency for item in items) >= 0.60
            and max(item.largest_asset_share for item in items) <= 0.35
        ):
            selected.append(
                {
                    "feature": feature,
                    "window_offset_minutes": offset,
                    "bad_condition": "LOW" if items[0].median_difference > 0 else "HIGH",
                    "threshold": statistics.fmean(
                        [
                            item.good_median
                            for item in items
                        ]
                        + [item.bad_median for item in items]
                    ),
                    "minimum_absolute_effect": min(
                        abs(item.standardized_effect) for item in items
                    ),
                }
            )
    selected.sort(key=lambda item: (-float(item["minimum_absolute_effect"]), str(item["feature"])))
    selected = selected[:5]
    return {
        "analysis_version": "mayak-discovery.1",
        "candidate_selection_protocol": {
            "comparisons": [
                "CONTINUATION_1_10 vs INITIAL_OR_PRE05_STOP",
                "CONTINUATION_1_10 vs PLUS_050_TO_MINUS_050",
            ],
            "bootstrap_samples": bootstrap_samples,
            "requirements": {
                "same_effect_direction": True,
                "bootstrap_ci_excludes_zero_both_comparisons": True,
                "minimum_absolute_standardized_effect": 0.20,
                "minimum_asset_direction_consistency": 0.60,
                "maximum_largest_asset_share": 0.35,
                "maximum_selected_candidates": 5,
            },
        },
        "selected_candidates": selected,
        "comparisons": [asdict(item) for item in comparisons],
        "cluster_diagnostic_comparisons": [asdict(item) for item in diagnostic_comparisons],
    }


def write_discovery(output: Path, result: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "MAYAK_P1_DISCOVERY_STATISTICS.json"
    json_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    comparisons = result["comparisons"]
    if isinstance(comparisons, list) and comparisons:
        with (output / "MAYAK_P1_DISCOVERY_STATISTICS.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=list(comparisons[0]))
            writer.writeheader()
            writer.writerows(comparisons)
    diagnostics = result["cluster_diagnostic_comparisons"]
    if isinstance(diagnostics, list) and diagnostics:
        with (output / "MAYAK_P1_CLUSTER_DIAGNOSTICS.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=list(diagnostics[0]))
            writer.writeheader()
            writer.writerows(diagnostics)


def freeze_discovery(
    output: Path,
    *,
    discovery: dict[str, Any],
    feature_spec: dict[str, Any],
    dataset_fingerprint: str,
    entry_fingerprint: str,
) -> str:
    manifest = {
        "manifest_version": "MAYAK_P1_DISCOVERY_FROZEN.1",
        "frozen_at": "2026-08-20T00:00:00+00:00",
        "discovery_universe": "ALL9",
        "confirmation_universe_locked": "NEW5",
        "feature_spec": feature_spec,
        "candidate_selection_protocol": discovery["candidate_selection_protocol"],
        "selected_candidates": discovery["selected_candidates"],
        "decision_criteria": {
            "replication": "same direction and confirmation bootstrap CI excludes zero",
            "partial_replication": "same direction without excluding zero",
            "failure": "opposite direction or no selected discovery candidates",
        },
        "exclusions": ["9 ALL9 data_end_no_activation events"],
        "software_version": "0.8.5+mayak-p1.0.0",
        "dataset_fingerprint": dataset_fingerprint,
        "entry_fingerprint": entry_fingerprint,
        "downloads": "DISABLED",
    }
    path = output / "MAYAK_P1_DISCOVERY_FROZEN_MANIFEST.json"
    content = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    path.write_text(content, encoding="utf-8")
    # Hash the authoritative on-disk bytes. Windows text newline translation may
    # otherwise make a pre-write string digest differ from the frozen artifact.
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (output / "MAYAK_P1_DISCOVERY_FROZEN_MANIFEST.sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii"
    )
    return digest


def _compare(
    rows: list[dict[str, Any]],
    feature: str,
    offset: int,
    good_label: str,
    bad_label: str,
    bootstrap_samples: int,
) -> Comparison:
    good_rows = [row for row in rows if row["primary_label"] == good_label]
    bad_rows = [row for row in rows if row["primary_label"] == bad_label]
    good = [float(row[feature]) for row in good_rows]
    bad = [float(row[feature]) for row in bad_rows]
    good_median = statistics.median(good)
    bad_median = statistics.median(bad)
    difference = good_median - bad_median
    pooled = math.sqrt((statistics.pvariance(good) + statistics.pvariance(bad)) / 2)
    effect = difference / pooled if pooled else 0.0
    rng = random.Random(f"{feature}:{offset}:{bad_label}:mayak")
    bootstrap = sorted(
        statistics.median(rng.choices(good, k=len(good)))
        - statistics.median(rng.choices(bad, k=len(bad)))
        for _ in range(bootstrap_samples)
    )
    low = bootstrap[int(0.025 * bootstrap_samples)]
    high = bootstrap[min(bootstrap_samples - 1, int(0.975 * bootstrap_samples))]
    symbol_differences: list[float] = []
    symbol_counts: list[int] = []
    for symbol in sorted({str(row["symbol"]) for row in rows}):
        symbol_good = [float(row[feature]) for row in good_rows if row["symbol"] == symbol]
        symbol_bad = [float(row[feature]) for row in bad_rows if row["symbol"] == symbol]
        if symbol_good and symbol_bad:
            symbol_differences.append(
                statistics.median(symbol_good) - statistics.median(symbol_bad)
            )
            symbol_counts.append(len(symbol_good) + len(symbol_bad))
    expected_sign = math.copysign(1.0, difference) if difference else 0.0
    consistency = (
        sum(math.copysign(1.0, value) == expected_sign for value in symbol_differences)
        / len(symbol_differences)
        if symbol_differences
        else 0.0
    )
    threshold = statistics.fmean((good_median, bad_median))
    if difference > 0:
        bad_separation = sum(value < threshold for value in bad) / len(bad)
        false_alarm = sum(value < threshold for value in good) / len(good)
    else:
        bad_separation = sum(value > threshold for value in bad) / len(bad)
        false_alarm = sum(value > threshold for value in good) / len(good)
    return Comparison(
        feature=feature,
        window_offset_minutes=offset,
        good_label=good_label,
        bad_label=bad_label,
        good_n=len(good),
        bad_n=len(bad),
        good_median=good_median,
        bad_median=bad_median,
        good_q25=_quantile(good, 0.25),
        good_q75=_quantile(good, 0.75),
        bad_q25=_quantile(bad, 0.25),
        bad_q75=_quantile(bad, 0.75),
        standardized_effect=effect,
        median_difference=difference,
        bootstrap_ci_low=low,
        bootstrap_ci_high=high,
        asset_direction_consistency=consistency,
        largest_asset_share=max(symbol_counts) / sum(symbol_counts),
        bad_separation=bad_separation,
        good_false_alarm_rate=false_alarm,
    )


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)
