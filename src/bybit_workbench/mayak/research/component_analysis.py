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
BASIS_FEATURES = {
    "basis_funding_rate",
    "basis_funding_rate_change_from_previous",
    "basis_mark_index_premium_pct",
}
LIQUIDITY_COPY_FIELDS = {
    "liquidity_imbalance",
    "liquidity_bid_change_pct",
    "liquidity_ask_change_pct",
    "liquidity_bid_change_1m_pct",
    "liquidity_ask_change_1m_pct",
    "liquidity_imbalance_change_1m",
    "liquidity_bid_change_5m_pct",
    "liquidity_ask_change_5m_pct",
    "liquidity_imbalance_change_5m",
    "liquidity_bid_change_15m_pct",
    "liquidity_ask_change_15m_pct",
    "liquidity_imbalance_change_15m",
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
        name in EXPLICIT_FEATURES
        or name.startswith("derivatives_")
        or name.startswith("relative_")
        or name.startswith("positioning_")
        or name.startswith("spot_")
        or name.startswith("liquidity_")
        or name in BASIS_FEATURES
    )


def _signed_feature(name: str) -> bool:
    if name == "market_median_return_5m_pct":
        return True
    if name.startswith("relative_"):
        return True
    if name in BASIS_FEATURES:
        return True
    if name == "positioning_long_short_imbalance":
        return True
    if name.startswith("liquidity_"):
        return (
            name == "liquidity_imbalance"
            or name.startswith("liquidity_depth_")
            and name.endswith("_imbalance")
            or name.startswith("liquidity_imbalance_change_")
        )
    return (name.startswith("derivatives_") or name.startswith("spot_")) and name.endswith(
        SIGNED_SUFFIXES
    )


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


def merge_positioning(
    rows: Sequence[dict[str, str]], positioning_csv: Path | None
) -> list[dict[str, str]]:
    merged = [dict(row) for row in rows]
    if positioning_csv is None:
        return merged
    with positioning_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        if "signal_key" not in (reader.fieldnames or []):
            raise ValueError("positioning file missing signal_key")
        pos: dict[str, dict[str, str]] = {}
        for row in reader:
            key = row["signal_key"]
            if key in pos:
                raise ValueError(f"duplicate positioning signal_key: {key}")
            forbidden = {name for name in row if "outcome" in name.lower() or "pnl" in name.lower()}
            if forbidden:
                raise ValueError(
                    f"positioning file contains forbidden outcome fields: {sorted(forbidden)}"
                )
            pos[key] = row
    keys = {row["signal_key"] for row in merged}
    if keys != set(pos):
        raise ValueError(
            f"positioning key mismatch base={len(keys)} positioning={len(pos)} "
            f"missing={len(keys.difference(pos))} extra={len(set(pos).difference(keys))}"
        )
    for row in merged:
        extra = pos[row["signal_key"]]
        for name, value in extra.items():
            if name.startswith("positioning_"):
                row[name] = value
    return merged


def merge_basis(rows: Sequence[dict[str, str]], basis_csv: Path | None) -> list[dict[str, str]]:
    merged = [dict(row) for row in rows]
    if basis_csv is None:
        return merged
    with basis_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        if "signal_key" not in (reader.fieldnames or []):
            raise ValueError("basis file missing signal_key")
        basis: dict[str, dict[str, str]] = {}
        for row in reader:
            key = row["signal_key"]
            if key in basis:
                raise ValueError(f"duplicate basis signal_key: {key}")
            forbidden = {name for name in row if "outcome" in name.lower() or "pnl" in name.lower()}
            if forbidden:
                raise ValueError(
                    f"basis file contains forbidden outcome fields: {sorted(forbidden)}"
                )
            basis[key] = row
    keys = {row["signal_key"] for row in merged}
    if keys != set(basis):
        raise ValueError(
            f"basis key mismatch base={len(keys)} basis={len(basis)} "
            f"missing={len(keys.difference(basis))} extra={len(set(basis).difference(keys))}"
        )
    for row in merged:
        extra = basis[row["signal_key"]]
        for name in BASIS_FEATURES:
            if name in extra:
                row[name] = extra[name]
    return merged


def _load_external_rows(path: Path, label: str) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        if "signal_key" not in (reader.fieldnames or []):
            raise ValueError(f"{label} file missing signal_key")
        result: dict[str, dict[str, str]] = {}
        for row in reader:
            forbidden = {name for name in row if "outcome" in name.lower() or "pnl" in name.lower()}
            if forbidden:
                raise ValueError(
                    f"{label} file contains forbidden outcome fields: {sorted(forbidden)}"
                )
            key = row["signal_key"]
            if key in result:
                raise ValueError(f"duplicate {label} signal_key: {key}")
            result[key] = row
    return result


def _require_same_keys(
    rows: Sequence[dict[str, str]], extra: dict[str, dict[str, str]], label: str
) -> None:
    keys = {row["signal_key"] for row in rows}
    if keys != set(extra):
        raise ValueError(
            f"{label} key mismatch base={len(keys)} external={len(extra)} "
            f"missing={len(keys.difference(extra))} extra={len(set(extra).difference(keys))}"
        )


def merge_spot(rows: Sequence[dict[str, str]], spot_csv: Path | None) -> list[dict[str, str]]:
    merged = [dict(row) for row in rows]
    if spot_csv is None:
        return merged
    extra = _load_external_rows(spot_csv, "spot")
    _require_same_keys(merged, extra, "spot")
    for row in merged:
        for name, value in extra[row["signal_key"]].items():
            if name.startswith("spot_"):
                row[name] = value
    return merged


def _ratio_imbalance(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid + ask <= 0:
        return None
    return (bid - ask) / (bid + ask)


def merge_liquidity(
    rows: Sequence[dict[str, str]], liquidity_csv: Path | None
) -> list[dict[str, str]]:
    merged = [dict(row) for row in rows]
    if liquidity_csv is None:
        return merged
    extra = _load_external_rows(liquidity_csv, "liquidity")
    _require_same_keys(merged, extra, "liquidity")
    for row in merged:
        source = extra[row["signal_key"]]
        for name in LIQUIDITY_COPY_FIELDS:
            if name in source:
                row[name] = source[name]
        bid_all = _float(source.get("liquidity_bid_usd"))
        ask_all = _float(source.get("liquidity_ask_usd"))
        total_all = None if bid_all is None or ask_all is None else bid_all + ask_all
        for bps in (5, 10, 25, 50):
            bid = _float(source.get(f"liquidity_bid_depth_{bps}bps_usd"))
            ask = _float(source.get(f"liquidity_ask_depth_{bps}bps_usd"))
            imbalance = _ratio_imbalance(bid, ask)
            share = None if bid is None or ask is None or not total_all else (bid + ask) / total_all
            row[f"liquidity_depth_{bps}bps_imbalance"] = "" if imbalance is None else str(imbalance)
            row[f"liquidity_depth_{bps}bps_share"] = "" if share is None else str(share)
        best_bid = _float(source.get("liquidity_best_bid"))
        best_ask = _float(source.get("liquidity_best_ask"))
        mid = _float(source.get("liquidity_mid_price"))
        spread = (
            None
            if best_bid is None or best_ask is None or not mid
            else (best_ask - best_bid) / mid * 10000.0
        )
        row["liquidity_spread_bps"] = "" if spread is None else str(spread)
        long_side = _direction_factor(row["direction"]) > 0
        for suffix in ("", "_1m", "_5m", "_15m"):
            bid_name = f"liquidity_bid_change{suffix}_pct" if suffix else "liquidity_bid_change_pct"
            ask_name = f"liquidity_ask_change{suffix}_pct" if suffix else "liquidity_ask_change_pct"
            support = source.get(bid_name if long_side else ask_name, "")
            opposition = source.get(ask_name if long_side else bid_name, "")
            tag = suffix or "_immediate"
            row[f"liquidity_entry_support_change{tag}_pct"] = support
            row[f"liquidity_entry_opposition_change{tag}_pct"] = opposition
    return merged


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


def run(
    input_csv: Path,
    output_dir: Path,
    source_commit: str,
    positioning_csv: Path | None = None,
    basis_csv: Path | None = None,
    spot_csv: Path | None = None,
    liquidity_csv: Path | None = None,
    account_ratio_csv: Path | None = None,
) -> dict[str, Any]:
    valid_sha = len(source_commit) == 40 and all(
        ch in "0123456789abcdef" for ch in source_commit.lower()
    )
    if not valid_sha:
        raise ValueError("source_commit must be a 40-character Git SHA")
    rows = merge_positioning(load_rows(input_csv), positioning_csv)
    rows = merge_basis(rows, basis_csv)
    rows = merge_spot(rows, spot_csv)
    rows = merge_liquidity(rows, liquidity_csv)
    rows = merge_positioning(rows, account_ratio_csv)
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
        "positioning_csv": str(positioning_csv) if positioning_csv else None,
        "positioning_sha256": _sha256(positioning_csv) if positioning_csv else None,
        "basis_csv": str(basis_csv) if basis_csv else None,
        "basis_sha256": _sha256(basis_csv) if basis_csv else None,
        "spot_csv": str(spot_csv) if spot_csv else None,
        "spot_sha256": _sha256(spot_csv) if spot_csv else None,
        "liquidity_csv": str(liquidity_csv) if liquidity_csv else None,
        "liquidity_sha256": _sha256(liquidity_csv) if liquidity_csv else None,
        "account_ratio_csv": str(account_ratio_csv) if account_ratio_csv else None,
        "account_ratio_sha256": _sha256(account_ratio_csv) if account_ratio_csv else None,
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
    parser.add_argument("--positioning-csv", type=Path)
    parser.add_argument("--basis-csv", type=Path)
    parser.add_argument("--spot-csv", type=Path)
    parser.add_argument("--liquidity-csv", type=Path)
    parser.add_argument("--account-ratio-csv", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = run(
        args.input_csv,
        args.output_dir,
        args.source_commit,
        positioning_csv=args.positioning_csv,
        basis_csv=args.basis_csv,
        spot_csv=args.spot_csv,
        liquidity_csv=args.liquidity_csv,
        account_ratio_csv=args.account_ratio_csv,
    )
    print(
        "MAYAK_COMPONENT_RESEARCH=PASS "
        f"signals={manifest['signals']} features={manifest['feature_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
