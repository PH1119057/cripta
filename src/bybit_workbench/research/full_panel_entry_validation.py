from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

DEFAULT_SYMBOLS = (
    "UNIUSDT",
    "LINKUSDT",
    "BTCUSDT",
    "ETHUSDT",
    "XRPUSDT",
    "1000PEPEUSDT",
    "SOLUSDT",
    "DOGEUSDT",
    "ADAUSDT",
)
DISPLAY_SYMBOLS = {"1000PEPEUSDT": "PEPE"}
EXPECTED_START = "2026-05-18T00:00:00+00:00"
EXPECTED_END = "2026-08-16T00:00:00+00:00"


@dataclass(frozen=True, slots=True)
class AssetPaths:
    root: Path
    p30: Path
    p31: Path
    p33: Path
    p34: Path
    p35: Path
    p36: Path
    p39: Path
    p40: Path

    @classmethod
    def from_root(cls, root: Path) -> AssetPaths:
        return cls(
            root=root,
            p30=root / "p30",
            p31=root / "p31",
            p33=root / "p33",
            p34=root / "p34",
            p35=root / "p35",
            p36=root / "p36",
            p39=root / "p39",
            p40=root / "p40",
        )


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return cast(dict[str, Any], payload)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"true", "1", "yes"}


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pct(part: int, total: int) -> float:
    return 0.0 if total <= 0 else part * 100.0 / total


def _outcomes(
    rows: Iterable[dict[str, str]],
    *,
    field_05: str = "first_0_5_vs_1_0",
    field_10: str = "first_1_0_vs_1_0",
) -> dict[str, Any]:
    items = list(rows)
    favorable_05 = sum(row.get(field_05) == "favorable_first" for row in items)
    adverse_05 = sum(row.get(field_05) == "adverse_first" for row in items)
    neither_05 = sum(row.get(field_05) == "neither" for row in items)
    favorable_10 = sum(row.get(field_10) == "favorable_first" for row in items)
    adverse_10 = sum(row.get(field_10) == "adverse_first" for row in items)
    neither_10 = sum(row.get(field_10) == "neither" for row in items)
    decisive_10 = favorable_10 + adverse_10
    return {
        "signals": len(items),
        "favorable_05": favorable_05,
        "adverse_05": adverse_05,
        "neither_05": neither_05,
        "rate_05": round(_pct(favorable_05, len(items)), 4),
        "favorable_10": favorable_10,
        "adverse_10": adverse_10,
        "neither_10": neither_10,
        "rate_10_all": round(_pct(favorable_10, len(items)), 4),
        "rate_10_decisive": round(_pct(favorable_10, decisive_10), 4),
    }


def _group_outcomes(
    rows: list[dict[str, str]],
    *,
    feature: str,
    values: Iterable[str],
    layer: str,
    baseline_rate: float,
    field_05: str = "first_0_5_vs_1_0",
    field_10: str = "first_1_0_vs_1_0",
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for value in values:
        bucket = [row for row in rows if row.get(feature) == value]
        if not bucket:
            continue
        metrics = _outcomes(bucket, field_05=field_05, field_10=field_10)
        output.append(
            {
                "layer": layer,
                "feature": feature,
                "value": value,
                **metrics,
                "parent_rate_05": baseline_rate,
                "uplift_05_pp": round(metrics["rate_05"] - baseline_rate, 4),
            }
        )
    return output


def _spread(records: list[dict[str, Any]], feature: str) -> float | None:
    values = [
        float(record["rate_05"])
        for record in records
        if record["feature"] == feature and int(record["signals"]) > 0
    ]
    if len(values) < 2:
        return None
    return round(max(values) - min(values), 4)


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 4)
    position = (len(ordered) - 1) * q
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return round(ordered[low], 4)
    fraction = position - low
    return round(ordered[low] + (ordered[high] - ordered[low]) * fraction, 4)


def _distribution(values: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "assets": 0,
            "median": None,
            "q1": None,
            "q3": None,
            "iqr": None,
            "min": None,
            "max": None,
            "stdev": None,
        }
    q1 = _quantile(values, 0.25)
    q3 = _quantile(values, 0.75)
    return {
        "assets": len(values),
        "median": round(statistics.median(values), 4),
        "q1": q1,
        "q3": q3,
        "iqr": None if q1 is None or q3 is None else round(q3 - q1, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "stdev": round(statistics.pstdev(values), 4) if len(values) > 1 else 0.0,
    }


def _p30_baseline(paths: AssetPaths) -> tuple[dict[str, Any], dict[str, Any]]:
    summary = _read_json(paths.p30 / "comparison.json")
    rows = _read_csv(paths.p30 / "p30_local_soft_hourly" / "signals.csv")
    metrics = _outcomes(rows, field_10="__not_available__")
    return summary, metrics


def _p33_exact(paths: AssetPaths) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, Any]]:
    summary = _read_json(paths.p33 / "summary.json")
    rows = _read_csv(paths.p33 / "signals_adverse_path.csv")
    return summary, rows, _outcomes(rows)


def _context_records(
    *,
    symbol: str,
    p34_rows: list[dict[str, str]],
    p35_rows: list[dict[str, str]],
    p36_rows: list[dict[str, str]],
    p39_rows: list[dict[str, str]],
    p40_rows: list[dict[str, str]],
    core_rate: float,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []

    accepted_oi = [row for row in p34_rows if _bool(row.get("accepted_after_failure_embargo"))]
    accepted_oi_rate = _outcomes(accepted_oi)["rate_05"]
    for feature in ("oi_60m_quartile", "oi_accel_quartile", "oi_state", "oi_price_regime_60m"):
        values = sorted({row.get(feature, "") for row in accepted_oi if row.get(feature, "")})
        output.extend(
            _group_outcomes(
                accepted_oi,
                feature=feature,
                values=values,
                layer="OI",
                baseline_rate=accepted_oi_rate,
            )
        )

    accepted_crowd = [row for row in p35_rows if _bool(row.get("accepted_after_failure_embargo"))]
    accepted_crowd_rate = _outcomes(accepted_crowd)["rate_05"]
    for feature in (
        "crowd_majority",
        "crowd_edge_quartile",
        "crowd_change_60m_quartile",
        "crowd_accel_quartile",
    ):
        values = sorted({row.get(feature, "") for row in accepted_crowd if row.get(feature, "")})
        output.extend(
            _group_outcomes(
                accepted_crowd,
                feature=feature,
                values=values,
                layer="CROWDING",
                baseline_rate=accepted_crowd_rate,
            )
        )

    accepted_basis = [row for row in p36_rows if _bool(row.get("accepted_after_failure_embargo"))]
    accepted_basis_rate = _outcomes(accepted_basis)["rate_05"]
    for feature in (
        "basis_state",
        "basis_level_quartile",
        "basis_change_60m_quartile",
        "basis_accel_quartile",
    ):
        values = sorted({row.get(feature, "") for row in accepted_basis if row.get(feature, "")})
        output.extend(
            _group_outcomes(
                accepted_basis,
                feature=feature,
                values=values,
                layer="BASIS",
                baseline_rate=accepted_basis_rate,
            )
        )

    for feature in (
        "support_wall_closer",
        "support_wall_larger",
        "both_wall_advantages",
        "near_imbalance_positive",
        "near_imbalance_improving",
        "near_imbalance_positive_or_improving",
        "near_imbalance_positive_and_improving",
    ):
        output.extend(
            _group_outcomes(
                p39_rows,
                feature=feature,
                values=("true", "false"),
                layer="P39_ORDERBOOK",
                baseline_rate=core_rate,
            )
        )

    for feature in (
        "adverse_taker_dominant_30s",
        "price_favorable_or_flat_30s",
        "adverse_flow_but_price_holds_30s",
        "support_net_positive_10bps_30s",
        "support_refill_present_10bps_30s",
        "support_refill_present_25bps_30s",
    ):
        output.extend(
            _group_outcomes(
                p40_rows,
                feature=feature,
                values=("true", "false"),
                layer="P40_ABSORPTION",
                baseline_rate=core_rate,
            )
        )

    for record in output:
        record["research_symbol"] = symbol
        record["display_symbol"] = DISPLAY_SYMBOLS.get(symbol, symbol.replace("USDT", ""))
    return output


def build_asset_summary(
    paths: AssetPaths,
    *,
    symbol: str,
    expected_start: str = EXPECTED_START,
    expected_end: str = EXPECTED_END,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    p30_summary, baseline = _p30_baseline(paths)
    p31_summary = _read_json(paths.p31 / "summary.json")
    p33_summary, exact_rows, exact = _p33_exact(paths)
    p34_rows = _read_csv(paths.p34 / "signals_open_interest.csv")
    p35_rows = _read_csv(paths.p35 / "signals_crowding.csv")
    p36_rows = _read_csv(paths.p36 / "signals_basis.csv")
    p39_rows = _read_csv(paths.p39 / "orderbook_features.csv")
    p40_rows = _read_csv(paths.p40 / "absorption_features.csv")

    evaluation_start = str(p30_summary["evaluation_start"])
    evaluation_end = str(p30_summary["evaluation_end"])
    period_ok = evaluation_start == expected_start and evaluation_end == expected_end

    pause_rows = [row for row in p34_rows if _bool(row.get("accepted_after_failure_embargo"))]
    pause = _outcomes(pause_rows)
    pressure_rows = [row for row in exact_rows if row.get("flow_state") == "pressure_then_reversal"]
    pressure = _outcomes(pressure_rows)
    pressure_pause_rows = [
        row
        for row in p34_rows
        if _bool(row.get("accepted_after_failure_embargo"))
        and row.get("flow_state") == "pressure_then_reversal"
    ]
    pressure_pause = _outcomes(pressure_pause_rows)
    core = _outcomes(p40_rows)

    by_direction = {
        direction: _outcomes([row for row in p40_rows if row.get("direction") == direction])
        for direction in ("Long", "Short")
    }
    by_segment = {
        segment: _outcomes([row for row in p40_rows if row.get("segment") == str(segment)])
        for segment in (1, 2, 3)
    }

    context = _context_records(
        symbol=symbol,
        p34_rows=p34_rows,
        p35_rows=p35_rows,
        p36_rows=p36_rows,
        p39_rows=p39_rows,
        p40_rows=p40_rows,
        core_rate=float(core["rate_05"]),
    )

    p39_dynamic = next(
        (
            record
            for record in context
            if record["feature"] == "near_imbalance_improving" and record["value"] == "true"
        ),
        None,
    )
    p40_support = next(
        (
            record
            for record in context
            if record["feature"] == "support_net_positive_10bps_30s"
            and record["value"] == "true"
        ),
        None,
    )
    p40_holds = next(
        (
            record
            for record in context
            if record["feature"] == "adverse_flow_but_price_holds_30s"
            and record["value"] == "true"
        ),
        None,
    )

    mae = p33_summary.get("mae", {}).get("mae_before_plus_0_5_for_favorable_signals_pct", {})
    p31_all = p31_summary.get("summary", {}).get("all", {})
    exact_reported = _float(p31_all.get("exact_first_0_5_vs_1_0_favorable_percent"))

    asset = {
        "research_symbol": symbol,
        "display_symbol": DISPLAY_SYMBOLS.get(symbol, symbol.replace("USDT", "")),
        "evaluation_start": evaluation_start,
        "evaluation_end": evaluation_end,
        "period_ok": period_ok,
        "p30_candidates": baseline["signals"],
        "p30_baseline_05": baseline["rate_05"],
        "exact_touch_signals": exact["signals"],
        "exact_touch_05": exact["rate_05"],
        "exact_touch_reported_05": exact_reported,
        "exact_touch_delta_vs_p30_pp": round(exact["rate_05"] - baseline["rate_05"], 4),
        "pause60_signals": pause["signals"],
        "pause60_05": pause["rate_05"],
        "pause60_uplift_vs_exact_pp": round(pause["rate_05"] - exact["rate_05"], 4),
        "pressure_reversal_signals": pressure["signals"],
        "pressure_reversal_05": pressure["rate_05"],
        "pressure_reversal_uplift_vs_exact_pp": round(
            pressure["rate_05"] - exact["rate_05"], 4
        ),
        "pressure_after_pause_signals": pressure_pause["signals"],
        "pressure_after_pause_05": pressure_pause["rate_05"],
        "pressure_after_pause_uplift_vs_pause_pp": round(
            pressure_pause["rate_05"] - pause["rate_05"], 4
        ),
        "core_signals": core["signals"],
        "core_05": core["rate_05"],
        "core_oi_uplift_vs_pressure_pause_pp": round(
            core["rate_05"] - pressure_pause["rate_05"], 4
        ),
        "core_10_all": core["rate_10_all"],
        "core_10_decisive": core["rate_10_decisive"],
        "core_10_neither": core["neither_10"],
        "long_core_signals": by_direction["Long"]["signals"],
        "long_core_05": by_direction["Long"]["rate_05"],
        "short_core_signals": by_direction["Short"]["signals"],
        "short_core_05": by_direction["Short"]["rate_05"],
        "segment1_signals": by_segment[1]["signals"],
        "segment1_core_05": by_segment[1]["rate_05"],
        "segment2_signals": by_segment[2]["signals"],
        "segment2_core_05": by_segment[2]["rate_05"],
        "segment3_signals": by_segment[3]["signals"],
        "segment3_core_05": by_segment[3]["rate_05"],
        "mae_good_median_pct": mae.get("median"),
        "mae_good_p75_pct": mae.get("p75"),
        "mae_good_p80_pct": mae.get("p80"),
        "mae_good_p90_pct": mae.get("p90"),
        "mae_good_p95_pct": mae.get("p95"),
        "oi_60m_quartile_spread_pp": _spread(context, "oi_60m_quartile"),
        "oi_accel_quartile_spread_pp": _spread(context, "oi_accel_quartile"),
        "crowd_edge_quartile_spread_pp": _spread(context, "crowd_edge_quartile"),
        "crowd_change_quartile_spread_pp": _spread(context, "crowd_change_60m_quartile"),
        "crowd_accel_quartile_spread_pp": _spread(context, "crowd_accel_quartile"),
        "basis_level_quartile_spread_pp": _spread(context, "basis_level_quartile"),
        "basis_change_quartile_spread_pp": _spread(context, "basis_change_60m_quartile"),
        "basis_accel_quartile_spread_pp": _spread(context, "basis_accel_quartile"),
        "p39_dynamic_signals": None if p39_dynamic is None else p39_dynamic["signals"],
        "p39_dynamic_05": None if p39_dynamic is None else p39_dynamic["rate_05"],
        "p39_dynamic_uplift_pp": None if p39_dynamic is None else p39_dynamic["uplift_05_pp"],
        "p40_support_net_positive_signals": (
            None if p40_support is None else p40_support["signals"]
        ),
        "p40_support_net_positive_05": None if p40_support is None else p40_support["rate_05"],
        "p40_support_net_positive_uplift_pp": (
            None if p40_support is None else p40_support["uplift_05_pp"]
        ),
        "p40_absorption_holds_signals": None if p40_holds is None else p40_holds["signals"],
        "p40_absorption_holds_05": None if p40_holds is None else p40_holds["rate_05"],
        "p40_absorption_holds_uplift_pp": (
            None if p40_holds is None else p40_holds["uplift_05_pp"]
        ),
    }

    pipeline_layers = [
        {
            "research_symbol": symbol,
            "display_symbol": asset["display_symbol"],
            "layer": "exact_touch",
            "signals": exact["signals"],
            "favorable": exact["favorable_05"],
            "rate_05": exact["rate_05"],
            "parent_rate_05": baseline["rate_05"],
            "uplift_05_pp": asset["exact_touch_delta_vs_p30_pp"],
        },
        {
            "research_symbol": symbol,
            "display_symbol": asset["display_symbol"],
            "layer": "pause_60m",
            "signals": pause["signals"],
            "favorable": pause["favorable_05"],
            "rate_05": pause["rate_05"],
            "parent_rate_05": exact["rate_05"],
            "uplift_05_pp": asset["pause60_uplift_vs_exact_pp"],
        },
        {
            "research_symbol": symbol,
            "display_symbol": asset["display_symbol"],
            "layer": "pressure_reversal",
            "signals": pressure["signals"],
            "favorable": pressure["favorable_05"],
            "rate_05": pressure["rate_05"],
            "parent_rate_05": exact["rate_05"],
            "uplift_05_pp": asset["pressure_reversal_uplift_vs_exact_pp"],
        },
        {
            "research_symbol": symbol,
            "display_symbol": asset["display_symbol"],
            "layer": "pressure_reversal_after_pause",
            "signals": pressure_pause["signals"],
            "favorable": pressure_pause["favorable_05"],
            "rate_05": pressure_pause["rate_05"],
            "parent_rate_05": pause["rate_05"],
            "uplift_05_pp": asset["pressure_after_pause_uplift_vs_pause_pp"],
        },
        {
            "research_symbol": symbol,
            "display_symbol": asset["display_symbol"],
            "layer": "oi_no_tail_core",
            "signals": core["signals"],
            "favorable": core["favorable_05"],
            "rate_05": core["rate_05"],
            "parent_rate_05": pressure_pause["rate_05"],
            "uplift_05_pp": asset["core_oi_uplift_vs_pressure_pause_pp"],
        },
    ]
    if p39_dynamic is not None:
        pipeline_layers.append(
            {
                "research_symbol": symbol,
                "display_symbol": asset["display_symbol"],
                "layer": "p39_near_imbalance_improving_true",
                "signals": p39_dynamic["signals"],
                "favorable": p39_dynamic["favorable_05"],
                "rate_05": p39_dynamic["rate_05"],
                "parent_rate_05": core["rate_05"],
                "uplift_05_pp": p39_dynamic["uplift_05_pp"],
            }
        )
    if p40_support is not None:
        pipeline_layers.append(
            {
                "research_symbol": symbol,
                "display_symbol": asset["display_symbol"],
                "layer": "p40_support_net_positive_10bps_30s_true",
                "signals": p40_support["signals"],
                "favorable": p40_support["favorable_05"],
                "rate_05": p40_support["rate_05"],
                "parent_rate_05": core["rate_05"],
                "uplift_05_pp": p40_support["uplift_05_pp"],
            }
        )
    if p40_holds is not None:
        pipeline_layers.append(
            {
                "research_symbol": symbol,
                "display_symbol": asset["display_symbol"],
                "layer": "p40_adverse_flow_price_holds_true",
                "signals": p40_holds["signals"],
                "favorable": p40_holds["favorable_05"],
                "rate_05": p40_holds["rate_05"],
                "parent_rate_05": core["rate_05"],
                "uplift_05_pp": p40_holds["uplift_05_pp"],
            }
        )

    return asset, context, pipeline_layers


def _aggregate_pipeline(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["layer"])].append(row)
    output: list[dict[str, Any]] = []
    for layer, group in grouped.items():
        rates = [float(row["rate_05"]) for row in group]
        uplifts = [float(row["uplift_05_pp"]) for row in group]
        total_signals = sum(int(row["signals"]) for row in group)
        total_favorable = sum(int(row["favorable"]) for row in group)
        rate_dist = _distribution(rates)
        uplift_dist = _distribution(uplifts)
        output.append(
            {
                "layer": layer,
                "assets": len(group),
                "pooled_signals": total_signals,
                "pooled_rate_05": round(_pct(total_favorable, total_signals), 4),
                "median_asset_rate_05": rate_dist["median"],
                "asset_rate_iqr_pp": rate_dist["iqr"],
                "asset_rate_min": rate_dist["min"],
                "asset_rate_max": rate_dist["max"],
                "median_uplift_05_pp": uplift_dist["median"],
                "uplift_iqr_pp": uplift_dist["iqr"],
                "uplift_stdev_pp": uplift_dist["stdev"],
                "improved_assets": sum(value > 0 for value in uplifts),
                "unchanged_assets": sum(value == 0 for value in uplifts),
                "worsened_assets": sum(value < 0 for value in uplifts),
            }
        )
    return sorted(output, key=lambda item: item["layer"])


def _aggregate_context(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["layer"]), str(row["feature"]), str(row["value"]))].append(row)
    output: list[dict[str, Any]] = []
    for (layer, feature, value), group in grouped.items():
        rates = [float(row["rate_05"]) for row in group]
        uplifts = [float(row["uplift_05_pp"]) for row in group]
        signals = [int(row["signals"]) for row in group]
        rate_dist = _distribution(rates)
        uplift_dist = _distribution(uplifts)
        output.append(
            {
                "layer": layer,
                "feature": feature,
                "value": value,
                "assets": len(group),
                "median_signals_per_asset": round(statistics.median(signals), 2),
                "median_asset_rate_05": rate_dist["median"],
                "asset_rate_iqr_pp": rate_dist["iqr"],
                "median_uplift_05_pp": uplift_dist["median"],
                "uplift_iqr_pp": uplift_dist["iqr"],
                "uplift_stdev_pp": uplift_dist["stdev"],
                "improved_assets": sum(item > 0 for item in uplifts),
                "unchanged_assets": sum(item == 0 for item in uplifts),
                "worsened_assets": sum(item < 0 for item in uplifts),
            }
        )
    return sorted(output, key=lambda item: (item["layer"], item["feature"], item["value"]))


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "NO"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _write_markdown(
    path: Path,
    *,
    assets: list[dict[str, Any]],
    pipeline: list[dict[str, Any]],
    expected_start: str,
    expected_end: str,
) -> None:
    lines = [
        "# ENTRY V1 — full cross-asset validation panel",
        "",
        f"Frozen interval: `{expected_start}` → `{expected_end}`",
        "",
        (
            "This report is descriptive. It does not promote any new Entry filter "
            "to a hard rule or veto."
        ),
        "",
        "## Asset matrix",
        "",
        (
            "| Asset | P30 N | Baseline +0.5/-1 | 60m pause | Pressure→reversal | "
            "Core N | Core +0.5/-1 | +1/-1 all | +1/-1 decisive | Neither +1/-1 |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in assets:
        lines.append(
            "| {display_symbol} | {p30_candidates} | {p30_baseline_05:.2f}% | "
            "{pause60_05:.2f}% | {pressure_reversal_05:.2f}% | {core_signals} | "
            "{core_05:.2f}% | {core_10_all:.2f}% | {core_10_decisive:.2f}% | "
            "{core_10_neither} |".format(**row)
        )

    lines.extend(
        [
            "",
            "## Asset-balanced layer transfer",
            "",
            (
                "Pooled rate is shown only as a secondary diagnostic. Median across assets "
                "and the number of assets improved are the primary transfer checks."
            ),
            "",
            (
                "| Layer | Assets | Pooled +0.5/-1 | Median asset rate | Median uplift | "
                "IQR uplift | Improved | Worse |"
            ),
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in pipeline:
        lines.append(
            f"| {row['layer']} | {row['assets']} | {_fmt(row['pooled_rate_05'])}% | "
            f"{_fmt(row['median_asset_rate_05'])}% | {_fmt(row['median_uplift_05_pp'])} pp | "
            f"{_fmt(row['uplift_iqr_pp'])} pp | {row['improved_assets']} | "
            f"{row['worsened_assets']} |"
        )

    lines.extend(
        [
            "",
            "## Interpretation guardrails",
            "",
            "- No post-hoc best quartile is selected as a new filter.",
            (
                "- Crowding and basis are exported as full context matrices, not collapsed "
                "into a cherry-picked state."
            ),
            "- P39/P40 states remain context/diagnostic until cross-asset transfer is reviewed.",
            (
                "- Market-regime/P44 remains outside this report and stays quarantined until "
                "this panel is interpreted."
            ),
            "- Live trading logic is not changed by this runner or aggregator.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _period_token(expected_start: str, expected_end: str) -> str:
    start = expected_start[:10].replace("-", "")
    end = expected_end[:10].replace("-", "")
    if len(start) != 8 or len(end) != 8 or not start.isdigit() or not end.isdigit():
        raise ValueError(
            "Expected ISO-like UTC boundaries beginning with YYYY-MM-DD: "
            f"{expected_start!r}, {expected_end!r}"
        )
    return f"{start}_{end}"


def _required_asset_outputs(root: Path) -> tuple[Path, ...]:
    return (
        root / "p30" / "comparison.json",
        root / "p30" / "p30_local_soft_hourly" / "signals.csv",
        root / "p31" / "summary.json",
        root / "p33" / "summary.json",
        root / "p33" / "signals_adverse_path.csv",
        root / "p34" / "summary.json",
        root / "p34" / "signals_open_interest.csv",
        root / "p35" / "summary.json",
        root / "p35" / "signals_crowding.csv",
        root / "p36" / "summary.json",
        root / "p36" / "signals_basis.csv",
        root / "p39" / "summary.json",
        root / "p39" / "orderbook_features.csv",
        root / "p40" / "summary.json",
        root / "p40" / "absorption_features.csv",
    )


def build_panel(
    validation_base: Path,
    *,
    symbols: Iterable[str] = DEFAULT_SYMBOLS,
    output_dir: Path | None = None,
    expected_start: str = EXPECTED_START,
    expected_end: str = EXPECTED_END,
) -> dict[str, Any]:
    period = _period_token(expected_start, expected_end)
    assets: list[dict[str, Any]] = []
    contexts: list[dict[str, Any]] = []
    pipeline_rows: list[dict[str, Any]] = []
    missing: list[str] = []

    for symbol in symbols:
        normalized = symbol.strip().upper()
        root = validation_base / f"{normalized}_{period}"
        missing_files = [path for path in _required_asset_outputs(root) if not path.is_file()]
        if missing_files:
            relative = ", ".join(str(path.relative_to(root)) for path in missing_files)
            missing.append(f"{normalized} [{relative}]")
            continue
        asset, context, layers = build_asset_summary(
            AssetPaths.from_root(root),
            symbol=normalized,
            expected_start=expected_start,
            expected_end=expected_end,
        )
        assets.append(asset)
        contexts.extend(context)
        pipeline_rows.extend(layers)

    if missing:
        raise FileNotFoundError(
            "Full-panel outputs are missing for: " + ", ".join(missing)
        )
    if not assets:
        raise ValueError("No assets were loaded")
    bad_periods = [row["research_symbol"] for row in assets if not row["period_ok"]]
    if bad_periods:
        raise ValueError("Frozen period mismatch for: " + ", ".join(bad_periods))

    output = output_dir or validation_base / f"ENTRY_V1_FULL_PANEL_{period}"
    output.mkdir(parents=True, exist_ok=True)
    pipeline_summary = _aggregate_pipeline(pipeline_rows)
    context_summary = _aggregate_context(contexts)

    _write_csv(output / "panel_asset_summary.csv", assets)
    _write_csv(output / "panel_pipeline_asset_layers.csv", pipeline_rows)
    _write_csv(output / "panel_pipeline_transfer.csv", pipeline_summary)
    _write_csv(output / "panel_context_asset_matrix.csv", contexts)
    _write_csv(output / "panel_context_transfer.csv", context_summary)
    payload = {
        "architecture": "entry_v1_full_cross_asset_validation",
        "evaluation_start": expected_start,
        "evaluation_end": expected_end,
        "symbols": [row["research_symbol"] for row in assets],
        "display_symbols": [row["display_symbol"] for row in assets],
        "asset_count": len(assets),
        "asset_summary": assets,
        "pipeline_transfer": pipeline_summary,
        "context_transfer": context_summary,
        "interpretation_rules": [
            "No new Entry filter is created from this aggregation.",
            (
                "Pooled rates are secondary to asset-balanced median/dispersion and "
                "improved-asset counts."
            ),
            "Crowding/basis quartiles are reported without selecting the best observed quartile.",
            "P44 market-regime results remain quarantined until the full panel is reviewed.",
            "Live trading logic is unchanged.",
        ],
    }
    _write_json(output / "panel_summary.json", payload)
    _write_markdown(
        output / "panel_summary.md",
        assets=assets,
        pipeline=pipeline_summary,
        expected_start=expected_start,
        expected_end=expected_end,
    )
    return payload


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate frozen Entry V1 full cross-asset panel")
    parser.add_argument("--validation-base", required=True)
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--expected-start", default=EXPECTED_START)
    parser.add_argument("--expected-end", default=EXPECTED_END)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = build_panel(
        Path(args.validation_base),
        symbols=args.symbols,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        expected_start=args.expected_start,
        expected_end=args.expected_end,
    )
    print(
        "Full panel complete: "
        f"assets={payload['asset_count']} "
        f"period={payload['evaluation_start']}..{payload['evaluation_end']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
