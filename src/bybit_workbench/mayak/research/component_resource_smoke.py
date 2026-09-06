from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from bybit_workbench.mayak.research.component_analysis import (
    load_rows,
    merge_basis,
    merge_liquidity,
    merge_positioning,
    merge_spot,
    value_for,
)
from bybit_workbench.mayak.research.component_oos_confirmation import CANDIDATES

VERSION = "mayak-component-resource-smoke-v1"
RESEARCH_LABEL = "RESOURCE_SMOKE_ONLY"
DEFAULT_MIN_NUMERIC_COVERAGE = 0.90


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def _load_replay_manifest(path: Path, expected_signals: int) -> dict[str, Any]:
    payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    actual_signals = payload.get("selected_signal_count")
    if int(actual_signals if actual_signals is not None else -1) != expected_signals:
        raise ValueError(
            f"replay signal count mismatch expected={expected_signals} actual={actual_signals}"
        )
        raise ValueError("replay manifest must prove outcomes_used_by_replay=false")
    return payload


def run(
    *,
    input_csv: Path,
    replay_manifest: Path,
    positioning_csv: Path,
    basis_csv: Path,
    spot_csv: Path,
    liquidity_csv: Path,
    account_ratio_csv: Path,
    output_dir: Path,
    source_commit: str,
    expected_symbols: tuple[str, ...],
    expected_signals: int,
    min_numeric_coverage: float = DEFAULT_MIN_NUMERIC_COVERAGE,
) -> dict[str, Any]:
    if len(source_commit) != 40:
        raise ValueError("source_commit must be a full Git SHA")
    if expected_signals <= 0:
        raise ValueError("expected_signals must be positive")
    if not expected_symbols or len(expected_symbols) != len(set(expected_symbols)):
        raise ValueError("expected_symbols must be non-empty and unique")
    if not 0.0 < min_numeric_coverage <= 1.0:
        raise ValueError("min_numeric_coverage must be in (0, 1]")

    replay = _load_replay_manifest(replay_manifest, expected_signals)
    rows = merge_positioning(load_rows(input_csv), positioning_csv)
    rows = merge_basis(rows, basis_csv)
    rows = merge_spot(rows, spot_csv)
    rows = merge_liquidity(rows, liquidity_csv)
    rows = merge_positioning(rows, account_ratio_csv)
    if len(rows) != expected_signals:
        raise ValueError(
            f"resource smoke signal count mismatch expected={expected_signals} actual={len(rows)}"
        )
    symbols = tuple(sorted({row["symbol"] for row in rows}))
    if symbols != tuple(sorted(expected_symbols)):
        raise ValueError(f"resource smoke symbol mismatch: {symbols}")

    coverage_rows: list[dict[str, Any]] = []
    failed: list[str] = []
    for candidate in CANDIDATES:
        numeric = sum(value_for(row, candidate.feature) is not None for row in rows)
        coverage = numeric / len(rows)
        passed = coverage >= min_numeric_coverage
        coverage_rows.append(
            {
                "outcome": candidate.outcome,
                "feature": candidate.feature,
                "expected_direction": candidate.expected_direction,
                "numeric_n": numeric,
                "signals": len(rows),
                "numeric_coverage": coverage,
                "coverage_gate": "PASS" if passed else "FAIL",
            }
        )
        if not passed:
            failed.append(candidate.feature)

    output_dir.mkdir(parents=True, exist_ok=True)
    coverage_path = output_dir / "RESOURCE_SMOKE_FEATURE_COVERAGE.csv"
    _write_csv(coverage_path, coverage_rows)
    sources = {
        "input_csv": input_csv,
        "replay_manifest": replay_manifest,
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
        "replay_project_commit": replay.get("project_commit"),
        "signals": len(rows),
        "symbols": list(expected_symbols),
        "candidate_count": len(CANDIDATES),
        "min_numeric_coverage": min_numeric_coverage,
        "coverage_failures": failed,
        "status": "PASS" if not failed else "FAIL",
        "coverage_csv": {
            "path": str(coverage_path),
            "sha256": _sha256(coverage_path),
        },
        "source_files": {
            name: {"path": str(path), "sha256": _sha256(path)} for name, path in sources.items()
        },
        "oos_verdict_produced": False,
        "threshold_selection": False,
        "retuning": False,
        "coin_market_rating_fitted": False,
        "strategy_policy_changed": False,
        "trading_effect": "NONE",
    }
    manifest_path = output_dir / "RUN_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if failed:
        raise RuntimeError(f"resource smoke numeric coverage failed: {failed}")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MAYAK frozen-component resource smoke only")
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument("--replay-manifest", type=Path, required=True)
    parser.add_argument("--positioning-csv", type=Path, required=True)
    parser.add_argument("--basis-csv", type=Path, required=True)
    parser.add_argument("--spot-csv", type=Path, required=True)
    parser.add_argument("--liquidity-csv", type=Path, required=True)
    parser.add_argument("--account-ratio-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--expected-signals", type=int, required=True)
    parser.add_argument("--min-numeric-coverage", type=float, default=DEFAULT_MIN_NUMERIC_COVERAGE)
    args = parser.parse_args(argv)
    symbols = tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip())
    manifest = run(
        input_csv=args.input_csv,
        replay_manifest=args.replay_manifest,
        positioning_csv=args.positioning_csv,
        basis_csv=args.basis_csv,
        spot_csv=args.spot_csv,
        liquidity_csv=args.liquidity_csv,
        account_ratio_csv=args.account_ratio_csv,
        output_dir=args.output_dir,
        source_commit=args.source_commit,
        expected_symbols=symbols,
        expected_signals=args.expected_signals,
        min_numeric_coverage=args.min_numeric_coverage,
    )
    signal_count = int(manifest.get("signals", 0))
    candidate_count = int(manifest.get("candidate_count", 0))
    print(
        f"MAYAK_RESOURCE_SMOKE=PASS signals={signal_count} candidates={candidate_count}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
