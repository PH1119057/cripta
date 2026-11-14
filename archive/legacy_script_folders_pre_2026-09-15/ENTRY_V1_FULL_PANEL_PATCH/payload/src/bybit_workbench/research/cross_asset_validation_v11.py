from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

UNI_BENCHMARK: dict[str, Any] = {
    "symbol": "UNIUSDT",
    "evaluation_start": "2026-05-18T00:00:00+00:00",
    "evaluation_end": "2026-08-16T00:00:00+00:00",
    "p30": {
        "candidates": 973,
        "signals_per_day": 10.811,
        "hit_plus_0_5_pct_rate": 79.65,
        "hit_plus_1_pct_rate": 60.74,
        "first_0_5_vs_1_0_favorable_percent": 62.80,
    },
    "p31_pressure_then_reversal": {
        "signals": 221,
        "first_0_5_vs_1_0_favorable_percent": 66.97,
    },
    "p33_pause_60m": {
        "accepted_candidates": 657,
        "first_0_5_vs_1_0_favorable_percent": 69.10,
    },
    "core": {
        "signals": 113,
        "first_0_5_vs_1_0_favorable_percent": 82.30088495575221,
        "first_1_0_vs_1_0_favorable_percent": 60.17699115044248,
        "first_1_0_vs_1_0_decisive_favorable_percent": 63.55140186915887,
    },
    "orderbook_support_net_positive_10bps_30s": {
        "signals": 19,
        "first_0_5_vs_1_0_favorable_percent": 100.0,
        "first_1_0_vs_1_0_favorable_percent": 73.6842105263158,
        "first_1_0_vs_1_0_decisive_favorable_percent": 82.35294117647058,
    },
    "orderbook_adverse_flow_but_price_holds_30s": {
        "signals": 6,
        "first_0_5_vs_1_0_favorable_percent": 100.0,
        "first_1_0_vs_1_0_favorable_percent": 83.33333333333334,
        "first_1_0_vs_1_0_decisive_favorable_percent": 83.33333333333334,
    },
}


@dataclass(frozen=True, slots=True)
class ValidationPaths:
    p30: Path
    p31: Path
    p33: Path
    p36: Path
    p40: Path
    output: Path


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return cast(dict[str, Any], payload)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _pct(part: int, total: int) -> float:
    return 0.0 if total <= 0 else part * 100.0 / total


def _outcome_metrics(rows: list[dict[str, str]]) -> dict[str, Any]:
    total = len(rows)
    favorable_05 = sum(row["first_0_5_vs_1_0"] == "favorable_first" for row in rows)
    favorable_10 = sum(row["first_1_0_vs_1_0"] == "favorable_first" for row in rows)
    adverse_10 = sum(row["first_1_0_vs_1_0"] == "adverse_first" for row in rows)
    decisive_10 = favorable_10 + adverse_10
    return {
        "signals": total,
        "first_0_5_vs_1_0_favorable_percent": round(_pct(favorable_05, total), 4),
        "first_1_0_vs_1_0_favorable_percent": round(_pct(favorable_10, total), 4),
        "first_1_0_vs_1_0_decisive_favorable_percent": round(
            _pct(favorable_10, decisive_10), 4
        ),
    }


def _segment_metrics(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for segment in ("1", "2", "3"):
        group = [row for row in rows if row.get("segment") == segment]
        output.append({"segment": int(segment), **_outcome_metrics(group)})
    return output


def _bool_value(value: str) -> bool:
    return value.strip().lower() in {"true", "1", "yes"}


def _delta(current: dict[str, Any], benchmark: dict[str, Any], key: str) -> float | None:
    left = current.get(key)
    right = benchmark.get(key)
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return None
    return round(float(left) - float(right), 4)


def build_validation_summary(
    paths: ValidationPaths,
    *,
    symbol: str,
    expected_start: str,
    expected_end: str,
) -> dict[str, Any]:
    p30 = _read_json(paths.p30 / "comparison.json")
    p31 = _read_json(paths.p31 / "summary.json")
    p33 = _read_json(paths.p33 / "summary.json")
    p36 = _read_json(paths.p36 / "summary.json")
    p40 = _read_json(paths.p40 / "summary.json")
    p40_rows = _read_csv(paths.p40 / "absorption_features.csv")

    p30_local = p30["p30_local_soft_hourly"]
    p31_flip = p31["summary"]["flow_states"]["pressure_then_reversal"]
    pause60 = next(
        item
        for item in p33["post_failure_embargo_candidate_filter"]
        if int(item["embargo_minutes"]) == 60
    )

    core = _outcome_metrics(p40_rows)
    support_positive = _outcome_metrics(
        [
            row
            for row in p40_rows
            if _bool_value(row["support_net_positive_10bps_30s"])
        ]
    )
    holds = _outcome_metrics(
        [
            row
            for row in p40_rows
            if _bool_value(row["adverse_flow_but_price_holds_30s"])
        ]
    )

    current = {
        "symbol": symbol,
        "evaluation_start": p30["evaluation_start"],
        "evaluation_end": p30["evaluation_end"],
        "period_matches_requested": (
            p30["evaluation_start"] == expected_start and p30["evaluation_end"] == expected_end
        ),
        "p30": {
            "candidates": p30_local["signals"],
            "signals_per_day": p30_local["signals_per_day"],
            "hit_plus_0_5_pct_rate": p30_local["hit_plus_0_5_pct_rate"],
            "hit_plus_1_pct_rate": p30_local["hit_plus_1_pct_rate"],
            "first_0_5_vs_1_0_favorable_percent": p30_local["first_0_5_vs_1_0"][
                "favorable_first"
            ]["percent"],
        },
        "p31_pressure_then_reversal": {
            "signals": p31_flip["signals"],
            "first_0_5_vs_1_0_favorable_percent": p31_flip[
                "exact_first_0_5_vs_1_0_favorable_percent"
            ],
        },
        "p33_pause_60m": {
            "accepted_candidates": pause60["accepted_candidates"],
            "first_0_5_vs_1_0_favorable_percent": pause60["accepted_favorable_percent"],
        },
        "core": core,
        "core_by_30d_segment": _segment_metrics(p40_rows),
        "orderbook_support_net_positive_10bps_30s": support_positive,
        "orderbook_adverse_flow_but_price_holds_30s": holds,
        "p36_core_reported": p36.get("core_pressure_reversal_without_oi_tail"),
        "p40_feature_rows": p40.get("feature_rows"),
    }

    comparison: dict[str, Any] = {}
    for section in (
        "p30",
        "p31_pressure_then_reversal",
        "p33_pause_60m",
        "core",
        "orderbook_support_net_positive_10bps_30s",
        "orderbook_adverse_flow_but_price_holds_30s",
    ):
        now = current[section]
        bench = UNI_BENCHMARK[section]
        comparison[section] = {
            key + "_delta_vs_uni": _delta(now, bench, key)
            for key in set(now) & set(bench)
            if isinstance(now.get(key), (int, float))
        }

    return {
        "protocol": (
            f"Frozen cross-asset validation for {symbol}; no outcome from this asset "
            "is used to tune Entry V1"
        ),
        "requested_period": {"start": expected_start, "end": expected_end},
        "uni_frozen_benchmark": UNI_BENCHMARK,
        "validation": current,
        "delta_vs_uni": comparison,
        "interpretation_rules": [
            "No automatic pass/fail threshold is applied by P41.",
            (
                "The key question is whether geometry, flow reversal, 60m invalidation "
                "pause, and core outcomes transfer across the validation asset without "
                "retuning Entry V1."
            ),
            (
                "Orderbook event features are bonuses/context only; P41 does not "
                "promote them to a hard gate."
            ),
            (
                "A strong result on one validation asset increases confidence but is not proof; "
                "the full-panel asset-balanced aggregation is the decision surface."
            ),
        ],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _write_markdown(path: Path, summary: dict[str, Any]) -> None:
    current = summary["validation"]
    bench = summary["uni_frozen_benchmark"]
    rows = [
        ("P30 candidates", current["p30"]["candidates"], bench["p30"]["candidates"]),
        (
            "P30 +0.5 hit, %",
            current["p30"]["hit_plus_0_5_pct_rate"],
            bench["p30"]["hit_plus_0_5_pct_rate"],
        ),
        (
            "P31 pressure→reversal +0.5/-1, %",
            current["p31_pressure_then_reversal"]["first_0_5_vs_1_0_favorable_percent"],
            bench["p31_pressure_then_reversal"]["first_0_5_vs_1_0_favorable_percent"],
        ),
        (
            "P33 60m pause +0.5/-1, %",
            current["p33_pause_60m"]["first_0_5_vs_1_0_favorable_percent"],
            bench["p33_pause_60m"]["first_0_5_vs_1_0_favorable_percent"],
        ),
        ("Core signals", current["core"]["signals"], bench["core"]["signals"]),
        (
            "Core +0.5/-1, %",
            current["core"]["first_0_5_vs_1_0_favorable_percent"],
            bench["core"]["first_0_5_vs_1_0_favorable_percent"],
        ),
        (
            "Core +1/-1 decisive, %",
            current["core"]["first_1_0_vs_1_0_decisive_favorable_percent"],
            bench["core"]["first_1_0_vs_1_0_decisive_favorable_percent"],
        ),
        (
            "OB support-net-positive +0.5/-1, %",
            current["orderbook_support_net_positive_10bps_30s"][
                "first_0_5_vs_1_0_favorable_percent"
            ],
            bench["orderbook_support_net_positive_10bps_30s"][
                "first_0_5_vs_1_0_favorable_percent"
            ],
        ),
    ]
    lines = [
        f"# Cross-asset validation — {current['symbol']}",
        "",
        f"Period: `{current['evaluation_start']}` → `{current['evaluation_end']}`",
        f"Exact requested period: **{current['period_matches_requested']}**",
        "",
        "| Metric | Validation asset | Frozen UNI benchmark |",
        "|---|---:|---:|",
    ]
    for label, now, old in rows:
        lines.append(f"| {label} | {_fmt(now)} | {_fmt(old)} |")
    lines.extend(
        [
            "",
            "## 30-day core slices",
            "",
            "| Segment | Signals | +0.5 before -1, % | +1 before -1 decisive, % |",
            "|---:|---:|---:|---:|",
        ]
    )
    for item in current["core_by_30d_segment"]:
        lines.append(
            "| {segment} | {signals} | {p05:.2f} | {p10:.2f} |".format(
                segment=item["segment"],
                signals=item["signals"],
                p05=item["first_0_5_vs_1_0_favorable_percent"],
                p10=item["first_1_0_vs_1_0_decisive_favorable_percent"],
            )
        )
    lines.extend(
        [
            "",
            (
                "This validator deliberately does not auto-accept or auto-reject the concept. "
                "The asset result is cross-asset validation, not a tuning pass."
            ),
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize P41 frozen cross-asset validation")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--p30-dir", required=True)
    parser.add_argument("--p31-dir", required=True)
    parser.add_argument("--p33-dir", required=True)
    parser.add_argument("--p36-dir", required=True)
    parser.add_argument("--p40-dir", required=True)
    parser.add_argument("--expected-start", required=True)
    parser.add_argument("--expected-end", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output = Path(args.output_dir)
    summary = build_validation_summary(
        ValidationPaths(
            p30=Path(args.p30_dir),
            p31=Path(args.p31_dir),
            p33=Path(args.p33_dir),
            p36=Path(args.p36_dir),
            p40=Path(args.p40_dir),
            output=output,
        ),
        symbol=args.symbol.strip().upper(),
        expected_start=args.expected_start,
        expected_end=args.expected_end,
    )
    _write_json(output / "validation_summary.json", summary)
    _write_markdown(output / "validation_summary.md", summary)
    print(f"Validation summary: {output / 'validation_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
