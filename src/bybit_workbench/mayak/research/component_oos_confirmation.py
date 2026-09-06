from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from bybit_workbench.mayak.research.component_analysis import (
    NumericSample,
    auc_higher_is_good,
    load_rows,
    merge_basis,
    merge_liquidity,
    merge_positioning,
    merge_spot,
    value_for,
)

VERSION = "mayak-component-cross-asset-oos-v1"
RESEARCH_LABEL = "FRESH_CROSS_ASSET_OOS_NEW15_V1"
FROZEN_SEEN_SUMMARY_SHA256 = "da6a913ae403939b7136d180a7e3966a5bec0d3139f5d27c00180bc150a9c6ff"
FROZEN_SEEN_QUARTILES_SHA256 = "238f91d477fda5b04326c0a6dbc42bcda5b24457361054bcdb984cca6cdce9ad"
TEST_SYMBOLS = (
    "AAVEUSDT",
    "APTUSDT",
    "ARBUSDT",
    "AVAXUSDT",
    "BCHUSDT",
    "BNBUSDT",
    "DOTUSDT",
    "HBARUSDT",
    "INJUSDT",
    "LTCUSDT",
    "NEARUSDT",
    "OPUSDT",
    "SUIUSDT",
    "TRXUSDT",
    "XLMUSDT",
)
REFERENCE_SYMBOLS = ("BTCUSDT", "ETHUSDT")
REPLAY_PANEL = TEST_SYMBOLS + REFERENCE_SYMBOLS
EXPECTED_SIGNALS = 14024

MIN_NUMERIC_COVERAGE = 0.90
MIN_RESOLVED_TOTAL = 500
MIN_ASSET_GOOD = 20
MIN_ASSET_BAD = 20
MIN_ELIGIBLE_ASSETS = 8
MIN_SAME_DIRECTION_ASSET_RATE = 0.60
MIN_DIRECTIONAL_AUC = 0.52
MAX_REJECT_DIRECTIONAL_AUC = 0.48
MIN_TRANSFER_Q_ENDPOINT_N = 50

ExpectedDirection = Literal["HIGHER_IS_GOOD", "LOWER_IS_GOOD"]
OutcomeName = Literal["PLUS_010_VS_MINUS_100", "PLUS_110_VS_MINUS_100"]

OUTCOMES: dict[OutcomeName, tuple[str, str, str]] = {
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


@dataclass(frozen=True, slots=True)
class Candidate:
    outcome: OutcomeName
    feature: str
    expected_direction: ExpectedDirection


@dataclass(frozen=True, slots=True)
class FrozenQuartiles:
    q1: float
    q2: float
    q3: float


CANDIDATES = (
    Candidate("PLUS_010_VS_MINUS_100", "basis_mark_index_premium_pct", "HIGHER_IS_GOOD"),
    Candidate(
        "PLUS_010_VS_MINUS_100", "derivatives_15m_large_trade_share", "HIGHER_IS_GOOD"
    ),
    Candidate(
        "PLUS_010_VS_MINUS_100", "relative_60m_panel_median_return_pct", "HIGHER_IS_GOOD"
    ),
    Candidate("PLUS_010_VS_MINUS_100", "liquidity_ask_change_5m_pct", "HIGHER_IS_GOOD"),
    Candidate(
        "PLUS_010_VS_MINUS_100",
        "positioning_open_interest_acceleration_5m_pct_per_min2",
        "HIGHER_IS_GOOD",
    ),
    Candidate("PLUS_010_VS_MINUS_100", "spot_60m_return_pct", "HIGHER_IS_GOOD"),
    Candidate(
        "PLUS_110_VS_MINUS_100",
        "entry_aligned::market_median_return_5m_pct",
        "HIGHER_IS_GOOD",
    ),
    Candidate(
        "PLUS_110_VS_MINUS_100",
        "derivatives_15m_acceleration_usd_per_min2",
        "HIGHER_IS_GOOD",
    ),
    Candidate("PLUS_110_VS_MINUS_100", "derivatives_15m_net_usd", "HIGHER_IS_GOOD"),
    Candidate("PLUS_110_VS_MINUS_100", "spot_1m_net_usd", "HIGHER_IS_GOOD"),
    Candidate(
        "PLUS_110_VS_MINUS_100",
        "entry_aligned::positioning_long_short_imbalance",
        "LOWER_IS_GOOD",
    ),
    Candidate(
        "PLUS_110_VS_MINUS_100",
        "entry_aligned::liquidity_depth_10bps_imbalance",
        "LOWER_IS_GOOD",
    ),
)


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
        return float(value)
    except ValueError:
        return None


def _state(row: dict[str, str], candidate: Candidate) -> bool | None:
    column, good, bad = OUTCOMES[candidate.outcome]
    value = row.get(column, "")
    if value == good:
        return True
    if value == bad:
        return False
    return None


def _directional_auc(raw_auc: float | None, direction: ExpectedDirection) -> float | None:
    if raw_auc is None:
        return None
    return raw_auc if direction == "HIGHER_IS_GOOD" else 1.0 - raw_auc


def _median_matches(good: float, bad: float, direction: ExpectedDirection) -> bool:
    return good > bad if direction == "HIGHER_IS_GOOD" else good < bad


def _quartile_matches(q1_rate: float, q4_rate: float, direction: ExpectedDirection) -> bool:
    return q4_rate > q1_rate if direction == "HIGHER_IS_GOOD" else q1_rate > q4_rate


def _candidate_observations(
    rows: Sequence[dict[str, str]], candidate: Candidate
) -> tuple[list[NumericSample], int, int, int]:
    samples: list[NumericSample] = []
    resolved = 0
    unresolved = 0
    missing_numeric = 0
    for row in rows:
        good = _state(row, candidate)
        if good is None:
            unresolved += 1
            continue
        resolved += 1
        value = value_for(row, candidate.feature)
        if value is None:
            missing_numeric += 1
            continue
        samples.append(NumericSample(value=value, good=good))
    return samples, resolved, unresolved, missing_numeric


def _quartile(value: float, frozen: FrozenQuartiles) -> str:
    if value <= frozen.q1:
        return "Q1"
    if value <= frozen.q2:
        return "Q2"
    if value <= frozen.q3:
        return "Q3"
    return "Q4"


def load_frozen_quartiles(path: Path) -> dict[tuple[str, str], FrozenQuartiles]:
    found: dict[tuple[str, str], set[tuple[float, float, float]]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for raw in reader:
            if raw.get("scope") != "ALL9":
                continue
            outcome = raw.get("outcome", "")
            feature = raw.get("feature", "")
            key = (outcome, feature)
            if key not in {(item.outcome, item.feature) for item in CANDIDATES}:
                continue
            q1 = _float(raw.get("q1"))
            q2 = _float(raw.get("q2"))
            q3 = _float(raw.get("q3"))
            if q1 is None or q2 is None or q3 is None:
                raise ValueError(f"missing frozen quartile cuts for {key}")
            found.setdefault(key, set()).add((q1, q2, q3))
    result: dict[tuple[str, str], FrozenQuartiles] = {}
    for candidate in CANDIDATES:
        key = (candidate.outcome, candidate.feature)
        values = found.get(key, set())
        if len(values) != 1:
            raise ValueError(f"frozen quartile definition mismatch for {key}: {values}")
        q1, q2, q3 = next(iter(values))
        if not q1 <= q2 <= q3:
            raise ValueError(f"invalid frozen quartile ordering for {key}")
        result[key] = FrozenQuartiles(q1=q1, q2=q2, q3=q3)
    return result


def load_seen_auc(path: Path) -> dict[tuple[str, str], float]:
    result: dict[tuple[str, str], float] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for raw in reader:
            if raw.get("scope") != "ALL9":
                continue
            key = (raw.get("outcome", ""), raw.get("feature", ""))
            if key not in {(item.outcome, item.feature) for item in CANDIDATES}:
                continue
            value = _float(raw.get("auc_higher_is_good"))
            if value is None:
                raise ValueError(f"missing seen AUC for {key}")
            if key in result:
                raise ValueError(f"duplicate seen AUC row for {key}")
            result[key] = value
    for candidate in CANDIDATES:
        key = (candidate.outcome, candidate.feature)
        if key not in result:
            raise ValueError(f"candidate missing from seen summary: {key}")
        directional = _directional_auc(result[key], candidate.expected_direction)
        if directional is None or directional <= 0.5:
            raise ValueError(f"candidate frozen direction contradicts seen report: {key}")
    return result


def evaluate_candidate(
    rows: Sequence[dict[str, str]],
    candidate: Candidate,
    frozen: FrozenQuartiles,
    seen_auc: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    samples, resolved, unresolved, missing_numeric = _candidate_observations(rows, candidate)
    raw_auc = auc_higher_is_good(samples)
    directional_auc = _directional_auc(raw_auc, candidate.expected_direction)
    good_values = [sample.value for sample in samples if sample.good]
    bad_values = [sample.value for sample in samples if not sample.good]
    good_n = len(good_values)
    bad_n = len(bad_values)
    median_good = statistics.median(good_values) if good_values else None
    median_bad = statistics.median(bad_values) if bad_values else None
    coverage = len(samples) / resolved if resolved else 0.0

    asset_rows: list[dict[str, Any]] = []
    same_direction_assets = 0
    opposite_direction_assets = 0
    eligible_assets = 0
    for symbol in sorted({row["symbol"] for row in rows}):
        selected = [row for row in rows if row["symbol"] == symbol]
        asset_samples, asset_resolved, asset_unresolved, asset_missing = _candidate_observations(
            selected, candidate
        )
        asset_good = sum(sample.good for sample in asset_samples)
        asset_bad = len(asset_samples) - asset_good
        asset_auc = auc_higher_is_good(asset_samples)
        asset_directional = _directional_auc(asset_auc, candidate.expected_direction)
        eligible = asset_good >= MIN_ASSET_GOOD and asset_bad >= MIN_ASSET_BAD
        sign = "INELIGIBLE"
        if eligible and asset_directional is not None:
            eligible_assets += 1
            if asset_directional > 0.5:
                same_direction_assets += 1
                sign = "EXPECTED"
            elif asset_directional < 0.5:
                opposite_direction_assets += 1
                sign = "OPPOSITE"
            else:
                sign = "TIE"
        asset_rows.append(
            {
                "research_label": RESEARCH_LABEL,
                "outcome": candidate.outcome,
                "feature": candidate.feature,
                "expected_direction": candidate.expected_direction,
                "symbol": symbol,
                "resolved": asset_resolved,
                "unresolved": asset_unresolved,
                "numeric": len(asset_samples),
                "missing_numeric": asset_missing,
                "good_n": asset_good,
                "bad_n": asset_bad,
                "raw_auc_higher_is_good": asset_auc,
                "directional_auc": asset_directional,
                "eligible_for_sign": eligible,
                "sign_vs_frozen": sign,
            }
        )

    same_rate = same_direction_assets / eligible_assets if eligible_assets else 0.0
    opposite_rate = opposite_direction_assets / eligible_assets if eligible_assets else 0.0

    buckets: dict[str, list[bool]] = {"Q1": [], "Q2": [], "Q3": [], "Q4": []}
    for row in rows:
        good = _state(row, candidate)
        if good is None:
            continue
        value = value_for(row, candidate.feature)
        if value is None:
            continue
        buckets[_quartile(value, frozen)].append(good)
    quartile_rows: list[dict[str, Any]] = []
    rates: dict[str, float | None] = {}
    for name in ("Q1", "Q2", "Q3", "Q4"):
        values = buckets[name]
        good_count = sum(values)
        rate = good_count / len(values) if values else None
        rates[name] = rate
        quartile_rows.append(
            {
                "research_label": RESEARCH_LABEL,
                "outcome": candidate.outcome,
                "feature": candidate.feature,
                "expected_direction": candidate.expected_direction,
                "quartile": name,
                "n": len(values),
                "good_n": good_count,
                "good_rate": rate,
                "frozen_q1": frozen.q1,
                "frozen_q2": frozen.q2,
                "frozen_q3": frozen.q3,
                "cuts_source": "SEEN_ALL9_FROZEN_NOT_RECOMPUTED",
            }
        )

    q1_rate = rates["Q1"]
    q4_rate = rates["Q4"]
    endpoint_data_ok = (
        len(buckets["Q1"]) >= MIN_TRANSFER_Q_ENDPOINT_N
        and len(buckets["Q4"]) >= MIN_TRANSFER_Q_ENDPOINT_N
        and q1_rate is not None
        and q4_rate is not None
    )
    quartile_expected = (
        endpoint_data_ok
        and q1_rate is not None
        and q4_rate is not None
        and _quartile_matches(q1_rate, q4_rate, candidate.expected_direction)
    )
    quartile_opposite = (
        endpoint_data_ok
        and q1_rate is not None
        and q4_rate is not None
        and _quartile_matches(
            q1_rate,
            q4_rate,
            "LOWER_IS_GOOD"
            if candidate.expected_direction == "HIGHER_IS_GOOD"
            else "HIGHER_IS_GOOD",
        )
    )

    median_expected = (
        median_good is not None
        and median_bad is not None
        and _median_matches(median_good, median_bad, candidate.expected_direction)
    )
    opposite_direction: ExpectedDirection = (
        "LOWER_IS_GOOD" if candidate.expected_direction == "HIGHER_IS_GOOD" else "HIGHER_IS_GOOD"
    )
    median_opposite = (
        median_good is not None
        and median_bad is not None
        and _median_matches(median_good, median_bad, opposite_direction)
    )

    data_gate = (
        resolved >= MIN_RESOLVED_TOTAL
        and coverage >= MIN_NUMERIC_COVERAGE
        and eligible_assets >= MIN_ELIGIBLE_ASSETS
        and endpoint_data_ok
    )
    if not data_gate:
        verdict = "INSUFFICIENT_DATA"
    elif (
        directional_auc is not None
        and directional_auc >= MIN_DIRECTIONAL_AUC
        and median_expected
        and same_rate >= MIN_SAME_DIRECTION_ASSET_RATE
        and quartile_expected
    ):
        verdict = "CONFIRMED"
    elif (
        directional_auc is not None
        and directional_auc <= MAX_REJECT_DIRECTIONAL_AUC
        and median_opposite
        and opposite_rate >= MIN_SAME_DIRECTION_ASSET_RATE
        and quartile_opposite
    ):
        verdict = "REJECTED"
    else:
        verdict = "MIXED"

    result = {
        "research_label": RESEARCH_LABEL,
        "outcome": candidate.outcome,
        "feature": candidate.feature,
        "expected_direction": candidate.expected_direction,
        "seen_raw_auc_higher_is_good": seen_auc,
        "resolved_total": resolved,
        "unresolved": unresolved,
        "numeric_n": len(samples),
        "missing_numeric_n": missing_numeric,
        "numeric_coverage": coverage,
        "good_n": good_n,
        "bad_n": bad_n,
        "median_good": median_good,
        "median_bad": median_bad,
        "raw_auc_higher_is_good": raw_auc,
        "directional_auc": directional_auc,
        "eligible_assets": eligible_assets,
        "same_direction_assets": same_direction_assets,
        "opposite_direction_assets": opposite_direction_assets,
        "same_direction_asset_rate": same_rate,
        "opposite_direction_asset_rate": opposite_rate,
        "frozen_q1_n": len(buckets["Q1"]),
        "frozen_q4_n": len(buckets["Q4"]),
        "frozen_q1_good_rate": q1_rate,
        "frozen_q4_good_rate": q4_rate,
        "data_gate": data_gate,
        "verdict": verdict,
        "threshold_selected": False,
        "retuned": False,
        "trading_effect": "NONE",
    }
    return result, asset_rows, quartile_rows


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def _load_replay_manifest(path: Path) -> dict[str, Any]:
    payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    symbols = tuple(str(item) for item in payload.get("symbols", []))
    if symbols != REPLAY_PANEL:
        raise ValueError(f"replay panel mismatch: {symbols}")
    if int(payload.get("selected_signal_count", -1)) != EXPECTED_SIGNALS:
        raise ValueError(
            "replay signal count mismatch: "
            f"{payload.get('selected_signal_count')}"
        )
    if payload.get("outcomes_used_by_replay") is not False:
        raise ValueError("replay manifest must prove outcomes_used_by_replay=false")
    return payload


def run(
    *,
    input_csv: Path,
    replay_manifest: Path,
    seen_summary: Path,
    seen_quartiles: Path,
    output_dir: Path,
    source_commit: str,
    positioning_csv: Path | None = None,
    basis_csv: Path | None = None,
    spot_csv: Path | None = None,
    liquidity_csv: Path | None = None,
    account_ratio_csv: Path | None = None,
) -> dict[str, Any]:
    if len(source_commit) != 40:
        raise ValueError("source_commit must be a full Git SHA")
    if _sha256(seen_summary) != FROZEN_SEEN_SUMMARY_SHA256:
        raise ValueError("seen COMPONENT_SUMMARY.csv hash mismatch")
    if _sha256(seen_quartiles) != FROZEN_SEEN_QUARTILES_SHA256:
        raise ValueError("seen QUARTILE_DIAGNOSTICS.csv hash mismatch")
    replay = _load_replay_manifest(replay_manifest)

    rows = merge_positioning(load_rows(input_csv), positioning_csv)
    rows = merge_basis(rows, basis_csv)
    rows = merge_spot(rows, spot_csv)
    rows = merge_liquidity(rows, liquidity_csv)
    rows = merge_positioning(rows, account_ratio_csv)
    if len(rows) != EXPECTED_SIGNALS:
        raise ValueError(f"OOS signal count mismatch: {len(rows)}")
    symbols = tuple(sorted({row["symbol"] for row in rows}))
    if symbols != tuple(sorted(TEST_SYMBOLS)):
        raise ValueError(f"OOS test symbols mismatch: {symbols}")

    frozen = load_frozen_quartiles(seen_quartiles)
    seen_auc = load_seen_auc(seen_summary)
    candidate_rows: list[dict[str, Any]] = []
    asset_rows: list[dict[str, Any]] = []
    quartile_rows: list[dict[str, Any]] = []
    for candidate in CANDIDATES:
        key = (candidate.outcome, candidate.feature)
        result, per_asset, transferred = evaluate_candidate(
            rows, candidate, frozen[key], seen_auc[key]
        )
        candidate_rows.append(result)
        asset_rows.extend(per_asset)
        quartile_rows.extend(transferred)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "OOS_CANDIDATE_RESULTS.csv", candidate_rows)
    _write_csv(output_dir / "OOS_ASSET_RESULTS.csv", asset_rows)
    _write_csv(output_dir / "OOS_FROZEN_QUARTILE_TRANSFER.csv", quartile_rows)

    sources = {
        "input_csv": input_csv,
        "replay_manifest": replay_manifest,
        "seen_summary": seen_summary,
        "seen_quartiles": seen_quartiles,
        "positioning_csv": positioning_csv,
        "basis_csv": basis_csv,
        "spot_csv": spot_csv,
        "liquidity_csv": liquidity_csv,
        "account_ratio_csv": account_ratio_csv,
    }
    manifest = {
        "version": VERSION,
        "research_label": RESEARCH_LABEL,
        "created_at": datetime.now(UTC).isoformat(),
        "project_commit": source_commit,
        "signals": len(rows),
        "test_symbols": list(TEST_SYMBOLS),
        "reference_only_symbols": list(REFERENCE_SYMBOLS),
        "replay_panel": list(REPLAY_PANEL),
        "replay_project_commit": replay.get("project_commit"),
        "frozen_candidates": [asdict(item) for item in CANDIDATES],
        "gates": {
            "min_numeric_coverage": MIN_NUMERIC_COVERAGE,
            "min_resolved_total": MIN_RESOLVED_TOTAL,
            "min_asset_good": MIN_ASSET_GOOD,
            "min_asset_bad": MIN_ASSET_BAD,
            "min_eligible_assets": MIN_ELIGIBLE_ASSETS,
            "min_same_direction_asset_rate": MIN_SAME_DIRECTION_ASSET_RATE,
            "min_directional_auc": MIN_DIRECTIONAL_AUC,
            "max_reject_directional_auc": MAX_REJECT_DIRECTIONAL_AUC,
            "min_transfer_q_endpoint_n": MIN_TRANSFER_Q_ENDPOINT_N,
        },
        "source_files": {
            name: None if path is None else {"path": str(path), "sha256": _sha256(path)}
            for name, path in sources.items()
        },
        "verdict_counts": dict(Counter(str(row["verdict"]) for row in candidate_rows)),
        "threshold_selection": False,
        "retuning": False,
        "coin_market_rating_fitted": False,
        "strategy_policy_changed": False,
        "trading_effect": "NONE",
    }
    (output_dir / "RUN_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Frozen MAYAK NEW15 cross-asset OOS confirmation")
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--replay-manifest", type=Path, required=True)
    parser.add_argument("--seen-summary", type=Path, required=True)
    parser.add_argument("--seen-quartiles", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--positioning-csv", type=Path)
    parser.add_argument("--basis-csv", type=Path)
    parser.add_argument("--spot-csv", type=Path)
    parser.add_argument("--liquidity-csv", type=Path)
    parser.add_argument("--account-ratio-csv", type=Path)
    args = parser.parse_args(argv)
    manifest = run(
        input_csv=args.input_csv,
        replay_manifest=args.replay_manifest,
        seen_summary=args.seen_summary,
        seen_quartiles=args.seen_quartiles,
        output_dir=args.output_dir,
        source_commit=args.source_commit,
        positioning_csv=args.positioning_csv,
        basis_csv=args.basis_csv,
        spot_csv=args.spot_csv,
        liquidity_csv=args.liquidity_csv,
        account_ratio_csv=args.account_ratio_csv,
    )
    print(
        "MAYAK_COMPONENT_OOS=PASS "
        f"signals={manifest['signals']} verdicts={manifest['verdict_counts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
