from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VERSION = "mayak-component-research-v1"
RESEARCH_LABEL = "DISCOVERY_SEEN_FROZEN_ALL9"
DEV_SYMBOLS = {"UNIUSDT", "LINKUSDT"}
OUTCOMES = {
    "PLUS_010_VS_MINUS_100": (
        "plus_010_vs_minus_100",
        "reached_plus_0p10_before_minus_1p00",
        "hit_minus_1p00_before_plus_0p10",
    ),
    "PLUS_110_VS_MINUS_100": (
        "plus_110_vs_minus_100",
        "reached_plus_1p10",
        "hit_minus_1p00",
    ),
}
EXPLICIT_FEATURES = {
    "mayak_confidence",
    "market_median_return_5m_pct",
    "market_up_share",
    "market_down_share",
    "market_synchronization",
}
SIGNED_SUFFIXES = (
    "_net_usd",
    "_net_share",
    "_speed_usd_per_min",
    "_acceleration_usd_per_min2",
)


@dataclass(frozen=True, slots=True)
class NumericSample:
    value: float
    good: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _direction_factor(direction: str) -> float:
    normalized = direction.strip().lower()
    if normalized.startswith("long"):
        return 1.0
    if normalized.startswith("short"):
        return -1.0
    raise ValueError(f"unknown direction: {direction}")


def _scope(row: dict[str, str]) -> str:
    return "DEV2" if row["symbol"] in DEV_SYMBOLS else "HOLDOUT7_DIAGNOSTIC_REUSE"


def _candidate_feature(name: str) -> bool:
    return (
        name in EXPLICIT_FEATURES or name.startswith("derivatives_") or name.startswith("relative_")
    )


def _signed_feature(name: str) -> bool:
    if name == "market_median_return_5m_pct":
        return True
    if name.startswith("relative_"):
        return True
    return name.startswith("derivatives_") and name.endswith(SIGNED_SUFFIXES)


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        required = {"signal_key", "symbol", "direction", *[v[0] for v in OUTCOMES.values()]}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"correlation file missing columns: {sorted(missing)}")
        rows = [dict(row) for row in reader]
    keys = [row["signal_key"] for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate signal_key in correlation input")
    return rows


def feature_names(rows: Sequence[dict[str, str]]) -> list[str]:
    if not rows:
        return []
    raw = [name for name in rows[0] if _candidate_feature(name)]
    result: list[str] = []
    for name in raw:
        if any(_float(row.get(name)) is not None for row in rows):
            result.append(name)
            if _signed_feature(name):
                result.append(f"entry_aligned::{name}")
    if any(_float(row.get("market_up_share")) is not None for row in rows):
        result.append("entry_aligned_market_share")
    return result


def value_for(row: dict[str, str], feature: str) -> float | None:
    if feature.startswith("entry_aligned::"):
        raw_name = feature.split("::", 1)[1]
        value = _float(row.get(raw_name))
        return None if value is None else value * _direction_factor(row["direction"])
    if feature == "entry_aligned_market_share":
        field = (
            "market_up_share" if _direction_factor(row["direction"]) > 0 else "market_down_share"
        )
        return _float(row.get(field))
    return _float(row.get(feature))


def _average_ranks(samples: Sequence[NumericSample]) -> list[float]:
    indexed = sorted(enumerate(samples), key=lambda item: item[1].value)
    ranks = [0.0] * len(samples)
    i = 0
    while i < len(indexed):
        j = i + 1
        while j < len(indexed) and indexed[j][1].value == indexed[i][1].value:
            j += 1
        average_rank = ((i + 1) + j) / 2
        for k in range(i, j):
            ranks[indexed[k][0]] = average_rank
        i = j
    return ranks


def auc_higher_is_good(samples: Sequence[NumericSample]) -> float | None:
    good_n = sum(item.good for item in samples)
    bad_n = len(samples) - good_n
    if good_n == 0 or bad_n == 0:
        return None
    ranks = _average_ranks(samples)
    good_rank_sum = sum(rank for rank, item in zip(ranks, samples, strict=True) if item.good)
    u = good_rank_sum - good_n * (good_n + 1) / 2
    return u / (good_n * bad_n)


def _percentile(values: Sequence[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("empty percentile")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * p
    lo = math.floor(position)
    hi = math.ceil(position)
    if lo == hi:
        return ordered[lo]
    fraction = position - lo
    return ordered[lo] * (1 - fraction) + ordered[hi] * fraction


def _scope_match(row: dict[str, str], scope: str) -> bool:
    if scope == "ALL9":
        return True
    if scope in {"DEV2", "HOLDOUT7_DIAGNOSTIC_REUSE"}:
        return _scope(row) == scope
    if scope in {"LONG", "SHORT"}:
        return row["direction"].upper().startswith(scope)
    return row["symbol"] == scope


def analyze(rows: Sequence[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    features = feature_names(rows)
    symbols = sorted({row["symbol"] for row in rows})
    scopes = ["ALL9", "DEV2", "HOLDOUT7_DIAGNOSTIC_REUSE", "LONG", "SHORT", *symbols]
    summary: list[dict[str, Any]] = []
    quartiles: list[dict[str, Any]] = []
    for outcome_name, (outcome_col, good_label, bad_label) in OUTCOMES.items():
        for scope in scopes:
            scoped = [row for row in rows if _scope_match(row, scope)]
            unresolved = sum(row[outcome_col] not in {good_label, bad_label} for row in scoped)
            for feature in features:
                samples: list[NumericSample] = []
                missing = 0
                for row in scoped:
                    state = row[outcome_col]
                    if state not in {good_label, bad_label}:
                        continue
                    value = value_for(row, feature)
                    if value is None:
                        missing += 1
                        continue
                    samples.append(NumericSample(value=value, good=state == good_label))
                good_values = [item.value for item in samples if item.good]
                bad_values = [item.value for item in samples if not item.good]
                auc = auc_higher_is_good(samples)
                summary.append(
                    {
                        "research_label": RESEARCH_LABEL,
                        "outcome": outcome_name,
                        "scope": scope,
                        "feature": feature,
                        "resolved_with_value": len(samples),
                        "good_n": len(good_values),
                        "bad_n": len(bad_values),
                        "unresolved_n": unresolved,
                        "missing_numeric_n": missing,
                        "median_good": statistics.median(good_values) if good_values else None,
                        "median_bad": statistics.median(bad_values) if bad_values else None,
                        "median_difference": (
                            statistics.median(good_values) - statistics.median(bad_values)
                            if good_values and bad_values
                            else None
                        ),
                        "auc_higher_is_good": auc,
                        "auc_abs_from_random": abs(auc - 0.5) if auc is not None else None,
                        "threshold_selected": False,
                    }
                )
                if len(samples) >= 4:
                    vals = [item.value for item in samples]
                    q1, q2, q3 = (_percentile(vals, p) for p in (0.25, 0.5, 0.75))
                    bins: list[list[NumericSample]] = [[], [], [], []]
                    for item in samples:
                        index = (
                            0
                            if item.value <= q1
                            else 1
                            if item.value <= q2
                            else 2
                            if item.value <= q3
                            else 3
                        )
                        bins[index].append(item)
                    for index, bucket in enumerate(bins, start=1):
                        good = sum(item.good for item in bucket)
                        quartiles.append(
                            {
                                "research_label": RESEARCH_LABEL,
                                "same_sample_quartiles": "DISCOVERY_ONLY",
                                "outcome": outcome_name,
                                "scope": scope,
                                "feature": feature,
                                "quartile": f"Q{index}",
                                "n": len(bucket),
                                "good_n": good,
                                "good_rate": good / len(bucket) if bucket else None,
                                "q1": q1,
                                "q2": q2,
                                "q3": q3,
                            }
                        )
    return summary, quartiles


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def run(input_csv: Path, output_dir: Path, source_commit: str) -> dict[str, Any]:
    valid_sha = len(source_commit) == 40 and all(
        ch in "0123456789abcdef" for ch in source_commit.lower()
    )
    if not valid_sha:
        raise ValueError("source_commit must be a 40-character Git SHA")
    rows = load_rows(input_csv)
    summary, quartiles = analyze(rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "COMPONENT_SUMMARY.csv", summary)
    _write_csv(output_dir / "QUARTILE_DIAGNOSTICS.csv", quartiles)
    counts = {
        outcome: dict(Counter(row[column] for row in rows))
        for outcome, (column, _, _) in OUTCOMES.items()
    }
    manifest = {
        "version": VERSION,
        "research_label": RESEARCH_LABEL,
        "project_commit": source_commit,
        "analysis_code_sha256": _sha256(Path(__file__)),
        "created_at": datetime.now(UTC).isoformat(),
        "input_csv": str(input_csv),
        "input_sha256": _sha256(input_csv),
        "source_replay_manifest_sha256": (
            _sha256(input_csv.parent / "RUN_MANIFEST.json")
            if (input_csv.parent / "RUN_MANIFEST.json").exists()
            else None
        ),
        "mayak_engine_versions": sorted(
            {row.get("mayak_engine_version", "") for row in rows if row.get("mayak_engine_version")}
        ),
        "mayak_feature_versions": sorted(
            {
                row.get("mayak_feature_version", "")
                for row in rows
                if row.get("mayak_feature_version")
            }
        ),
        "signals": len(rows),
        "symbols": sorted({row["symbol"] for row in rows}),
        "dev_symbols": sorted(DEV_SYMBOLS),
        "holdout_status": "DIAGNOSTIC_REUSE_NOT_FRESH_OOS",
        "threshold_selection": False,
        "coin_market_rating_fitted": False,
        "outcome_counts": counts,
        "feature_count": len(feature_names(rows)),
        "component_summary_rows": len(summary),
        "quartile_rows": len(quartiles),
    }
    (output_dir / "RUN_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Frozen MAYAK component research; no threshold fitting"
    )
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = run(args.input_csv, args.output_dir, args.source_commit)
    print(
        "MAYAK_COMPONENT_RESEARCH=PASS "
        f"signals={manifest['signals']} features={manifest['feature_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
