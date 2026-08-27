from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from bybit_workbench.research.core_runner_split_v16 import (
    SplitConfig,
    SplitPolicyResult,
    SplitPolicySpec,
    simulate_split_policy,
)
from bybit_workbench.research.exit_break_even_v13 import (
    PathSeries,
    SignalSource,
    TradeDayCache,
    build_path_series,
    discover_source,
    load_core_signals,
)
from bybit_workbench.research.flow_reversal_v1 import _archive_map
from bybit_workbench.research.runner_management_v15 import (
    PolicySpec,
    RunnerConfig,
    RunnerPolicyResult,
    simulate_runner_policy,
)

PolicyId = Literal[
    "A_SIMPLE_TAKE_1P00",
    "B_FULL_RUNNER_MFE_GB1P50",
    "C_SPLIT50_RUN50_BE_MFE_GB4P00",
]

HOLDOUT_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "XRPUSDT",
    "1000PEPEUSDT",
    "SOLUSDT",
    "DOGEUSDT",
    "ADAUSDT",
)
PERIOD_TAG = "20260518_20260816"
EXPECTED_SIGNAL_COUNTS = {
    "BTCUSDT": 119,
    "ETHUSDT": 130,
    "XRPUSDT": 125,
    "1000PEPEUSDT": 117,
    "SOLUSDT": 91,
    "DOGEUSDT": 143,
    "ADAUSDT": 111,
}
EXPECTED_POOLED_SIGNALS = 836


class ProgressReporter:
    def __init__(self, interval_seconds: float = 25.0) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.interval_seconds = interval_seconds
        self.started = time.monotonic()
        self.last_emit = 0.0

    def emit(
        self,
        stage: str,
        *,
        processed: int,
        total: int,
        force: bool = False,
        detail: str = "",
    ) -> None:
        now = time.monotonic()
        if not force and now - self.last_emit < self.interval_seconds:
            return
        elapsed = max(0.0, now - self.started)
        eta = None
        if processed > 0 and total > processed:
            eta = elapsed / processed * (total - processed)
        eta_text = "n/a" if eta is None else _format_duration(eta)
        suffix = f" | {detail}" if detail else ""
        print(
            f"[P47F] stage={stage} processed={processed}/{total} "
            f"elapsed={_format_duration(elapsed)} ETA={eta_text}{suffix}",
            flush=True,
        )
        self.last_emit = now


@dataclass(frozen=True, slots=True)
class ArchitectureConfig:
    initial_stop_pct: float = 1.0
    early_activation_pct: float = 0.10
    early_floor_pct: float = 0.0
    activation_1p10_pct: float = 1.10
    simple_take_pct: float = 1.00
    full_runner_floor_pct: float = 1.00
    full_runner_mfe_giveback_pct: float = 1.50
    split_core_fraction: float = 0.50
    split_core_exit_pct: float = 1.00
    split_runner_mfe_giveback_pct: float = 4.00
    horizon_hours: int = 72
    day_cache_size: int = 6
    progress_interval_seconds: float = 25.0

    def __post_init__(self) -> None:
        if self.initial_stop_pct <= 0:
            raise ValueError("initial_stop_pct must be positive")
        if not 0 <= self.early_floor_pct < self.early_activation_pct:
            raise ValueError("early floor must be >= 0 and below early activation")
        if self.activation_1p10_pct <= self.early_activation_pct:
            raise ValueError("1.10 activation must be above early activation")
        if not self.early_floor_pct < self.simple_take_pct <= self.activation_1p10_pct:
            raise ValueError("simple take must be above early floor and <= activation")
        if not self.early_floor_pct <= self.full_runner_floor_pct < self.activation_1p10_pct:
            raise ValueError("full-runner floor must be below activation")
        if self.full_runner_mfe_giveback_pct <= 0:
            raise ValueError("full-runner MFE giveback must be positive")
        if not 0 < self.split_core_fraction < 1:
            raise ValueError("split core fraction must be between 0 and 1")
        if self.split_core_exit_pct <= self.early_floor_pct:
            raise ValueError("split core exit must be above early floor")
        if self.split_runner_mfe_giveback_pct <= 0:
            raise ValueError("split runner MFE giveback must be positive")
        if self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be positive")
        if self.day_cache_size <= 0:
            raise ValueError("day_cache_size must be positive")
        if self.progress_interval_seconds <= 0:
            raise ValueError("progress_interval_seconds must be positive")


@dataclass(frozen=True, slots=True)
class ArchitectureResult:
    symbol: str
    touch_at: datetime
    policy_id: PolicyId
    exit_reason: str
    exit_at: datetime
    exit_move_pct: float
    completed_horizon: bool
    early_activated: bool
    activation_1p10_reached: bool
    max_favorable_pct: float


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def validation_p40(root: Path, symbol: str, *, period_tag: str = PERIOD_TAG) -> Path:
    return root / "reports" / "cross_asset_validation" / f"{symbol}_{period_tag}" / "p40"


def discover_holdout_sources(
    root: Path,
    symbols: tuple[str, ...] = HOLDOUT_SYMBOLS,
    *,
    period_tag: str = PERIOD_TAG,
) -> tuple[SignalSource, ...]:
    sources: list[SignalSource] = []
    for symbol in symbols:
        source = discover_source(validation_p40(root, symbol, period_tag=period_tag))
        if source.symbol != symbol:
            raise ValueError(
                f"P40 symbol mismatch for {symbol}: source reports {source.symbol}"
            )
        sources.append(source)
    return tuple(sources)


def _split_config(config: ArchitectureConfig) -> SplitConfig:
    return SplitConfig(
        initial_stop_pct=config.initial_stop_pct,
        early_activation_pct=config.early_activation_pct,
        early_floor_pct=config.early_floor_pct,
        split_activation_pct=config.activation_1p10_pct,
        core_exit_pct=config.split_core_exit_pct,
        horizon_hours=config.horizon_hours,
        core_fractions=(1.0, config.split_core_fraction),
        mfe_giveback_pct=(config.split_runner_mfe_giveback_pct,),
        target_levels_pct=(1.5, 2.0, 3.0, 5.0, 10.0),
        day_cache_size=config.day_cache_size,
        progress_interval_seconds=config.progress_interval_seconds,
    )


def _simple_policy() -> SplitPolicySpec:
    return SplitPolicySpec(
        policy_id="A_SIMPLE_TAKE_1P00",
        family="core_only",
        core_fraction=1.0,
    )


def _split_policy(config: ArchitectureConfig) -> SplitPolicySpec:
    return SplitPolicySpec(
        policy_id="C_SPLIT50_RUN50_BE_MFE_GB4P00",
        family="mfe",
        core_fraction=config.split_core_fraction,
        floor_mode="be",
        giveback_pct=config.split_runner_mfe_giveback_pct,
    )


def _runner_config(config: ArchitectureConfig) -> RunnerConfig:
    return RunnerConfig(
        initial_stop_pct=config.initial_stop_pct,
        early_activation_pct=config.early_activation_pct,
        early_floor_pct=config.early_floor_pct,
        runner_activation_pct=config.activation_1p10_pct,
        runner_floor_pct=config.full_runner_floor_pct,
        horizon_hours=config.horizon_hours,
        target_levels_pct=(1.5, 2.0, 3.0, 5.0, 10.0),
        step_giveback_pct=(0.25,),
        mfe_giveback_pct=(config.full_runner_mfe_giveback_pct,),
        structural_presets=((0.50, 0.25, 0.05),),
        day_cache_size=config.day_cache_size,
        progress_interval_seconds=config.progress_interval_seconds,
    )


def _full_runner_policy(config: ArchitectureConfig) -> PolicySpec:
    return PolicySpec(
        policy_id="B_FULL_RUNNER_MFE_GB1P50",
        family="mfe",
        giveback_pct=config.full_runner_mfe_giveback_pct,
    )


def _normalise_split(
    result: SplitPolicyResult,
    policy_id: PolicyId,
) -> ArchitectureResult:
    return ArchitectureResult(
        symbol=result.symbol,
        touch_at=result.touch_at,
        policy_id=policy_id,
        exit_reason=result.exit_reason,
        exit_at=result.exit_at,
        exit_move_pct=result.exit_move_pct,
        completed_horizon=result.completed_horizon,
        early_activated=result.early_activated,
        activation_1p10_reached=result.split_activated,
        max_favorable_pct=result.max_favorable_pct,
    )


def _normalise_runner(result: RunnerPolicyResult) -> ArchitectureResult:
    return ArchitectureResult(
        symbol=result.symbol,
        touch_at=result.touch_at,
        policy_id="B_FULL_RUNNER_MFE_GB1P50",
        exit_reason=result.exit_reason,
        exit_at=result.exit_at,
        exit_move_pct=result.exit_move_pct,
        completed_horizon=result.completed_horizon,
        early_activated=result.early_activated,
        activation_1p10_reached=result.runner_activated,
        max_favorable_pct=result.max_favorable_pct,
    )


def simulate_three_architectures(
    path: PathSeries,
    config: ArchitectureConfig,
) -> tuple[ArchitectureResult, ArchitectureResult, ArchitectureResult]:
    split_config = _split_config(config)
    simple = _normalise_split(
        simulate_split_policy(path, _simple_policy(), split_config),
        "A_SIMPLE_TAKE_1P00",
    )
    full_runner = _normalise_runner(
        simulate_runner_policy(
            path,
            _full_runner_policy(config),
            _runner_config(config),
        )
    )
    split = _normalise_split(
        simulate_split_policy(path, _split_policy(config), split_config),
        "C_SPLIT50_RUN50_BE_MFE_GB4P00",
    )
    return simple, full_runner, split


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(statistics.median(values)), 6)


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * weight, 6)


def _profit_factor(values: list[float]) -> float | None:
    positive = sum(value for value in values if value > 0)
    negative = -sum(value for value in values if value < 0)
    if negative <= 0:
        return None
    return round(positive / negative, 6)


def _max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
    return round(max_drawdown, 6)


def _top_contribution(values: list[float], count: int) -> float | None:
    total = sum(values)
    if abs(total) <= 1e-12:
        return None
    positive = sorted((value for value in values if value > 0), reverse=True)
    return round(sum(positive[:count]) / total * 100.0, 6)


def summarise_results(
    results: tuple[ArchitectureResult, ...],
    *,
    policy_id: PolicyId,
    scope: str,
    sample_span_days: float,
) -> dict[str, Any]:
    scoped = [
        item
        for item in results
        if item.policy_id == policy_id and (scope == "HOLDOUT7" or item.symbol == scope)
    ]
    chronological = sorted(scoped, key=lambda item: (item.touch_at, item.symbol))
    all_values = [item.exit_move_pct for item in chronological]
    decision_grade = [item for item in chronological if item.completed_horizon]
    decision_values = [item.exit_move_pct for item in decision_grade]
    gross_all = sum(all_values)
    monthly = gross_all / sample_span_days * 30.0 if sample_span_days > 0 else None
    return {
        "scope": scope,
        "policy_id": policy_id,
        "signals": len(scoped),
        "decision_grade": len(decision_grade),
        "censored": len(scoped) - len(decision_grade),
        "initial_stop_exits": sum(item.exit_reason == "initial_stop" for item in scoped),
        "early_be_exits": sum(item.exit_reason == "early_be" for item in scoped),
        "activation_1p10_reached": sum(item.activation_1p10_reached for item in scoped),
        "runner_stop_exits": sum(item.exit_reason == "runner_stop" for item in scoped),
        "core_take_exits": sum(item.exit_reason == "core_take" for item in scoped),
        "horizon_exits": sum(item.exit_reason == "horizon" for item in scoped),
        "data_end_exits": sum(item.exit_reason == "data_end" for item in scoped),
        "gross_all_signal_sum_pct": round(gross_all, 6),
        "gross_decision_grade_sum_pct": round(sum(decision_values), 6),
        "gross_mean_trade_pct": round(gross_all / len(all_values), 6) if all_values else None,
        "gross_median_trade_pct": _median(all_values),
        "profit_factor_all": _profit_factor(all_values),
        "positive_trade_count": sum(value > 0 for value in all_values),
        "negative_trade_count": sum(value < 0 for value in all_values),
        "scratch_trade_count": sum(abs(value) <= 1e-12 for value in all_values),
        "sequential_signal_max_drawdown_pct": _max_drawdown(all_values),
        "top10_positive_contribution_pct": _top_contribution(all_values, 10),
        "top20_positive_contribution_pct": _top_contribution(all_values, 20),
        "fixed_notional_30d_equivalent_pct": round(monthly, 6) if monthly is not None else None,
        "exit_p10_pct": _quantile(all_values, 0.10),
        "exit_p25_pct": _quantile(all_values, 0.25),
        "exit_p75_pct": _quantile(all_values, 0.75),
        "exit_p90_pct": _quantile(all_values, 0.90),
    }


def _expected_source_check(signals_by_symbol: dict[str, int]) -> dict[str, Any]:
    actual_pooled = sum(signals_by_symbol.values())
    return {
        "expected_by_symbol": EXPECTED_SIGNAL_COUNTS,
        "actual_by_symbol": signals_by_symbol,
        "expected_pooled": EXPECTED_POOLED_SIGNALS,
        "actual_pooled": actual_pooled,
        "all_match": signals_by_symbol == EXPECTED_SIGNAL_COUNTS
        and actual_pooled == EXPECTED_POOLED_SIGNALS,
    }


def _latest_p47e_summary(root: Path) -> Path | None:
    report_root = root / "reports" / "hourly_trend_oos_v1"
    candidates = sorted(report_root.glob("HOLDOUT7_*/summary.json"), reverse=True)
    return candidates[0] if candidates else None


def _p47e_crosscheck(
    root: Path,
    pooled_c_row: dict[str, Any],
) -> dict[str, Any]:
    summary_path = _latest_p47e_summary(root)
    if summary_path is None:
        return {"applicable": False, "reason": "No P47E HOLDOUT7 summary found."}
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    expected_signals = int(payload["holdout_pooled"]["signals"])
    expected_gross = float(payload["holdout_pooled"]["gross_selected_policy_pct"])
    actual_signals = int(pooled_c_row["signals"])
    actual_gross = float(pooled_c_row["gross_all_signal_sum_pct"])
    return {
        "applicable": True,
        "summary_path": str(summary_path),
        "expected_policy_id": payload.get("selected_policy_id"),
        "expected_signals": expected_signals,
        "actual_signals": actual_signals,
        "expected_gross_pct": round(expected_gross, 6),
        "actual_gross_pct": round(actual_gross, 6),
        "signals_match": expected_signals == actual_signals,
        "gross_match_within_1e_6": abs(expected_gross - actual_gross) <= 1e-6,
    }


def _result_row(result: ArchitectureResult) -> dict[str, Any]:
    return asdict(result)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def _comparison_rows(summary_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {(row["scope"], row["policy_id"]): row for row in summary_rows}
    rows: list[dict[str, Any]] = []
    for scope in (*HOLDOUT_SYMBOLS, "HOLDOUT7"):
        a = by_key[(scope, "A_SIMPLE_TAKE_1P00")]
        b = by_key[(scope, "B_FULL_RUNNER_MFE_GB1P50")]
        c = by_key[(scope, "C_SPLIT50_RUN50_BE_MFE_GB4P00")]
        a_gross = float(a["gross_all_signal_sum_pct"])
        b_gross = float(b["gross_all_signal_sum_pct"])
        c_gross = float(c["gross_all_signal_sum_pct"])
        rows.append(
            {
                "scope": scope,
                "A_simple_gross_pct": round(a_gross, 6),
                "B_full_runner_gross_pct": round(b_gross, 6),
                "C_split_gross_pct": round(c_gross, 6),
                "B_minus_A_pct_points": round(b_gross - a_gross, 6),
                "C_minus_A_pct_points": round(c_gross - a_gross, 6),
                "B_minus_C_pct_points": round(b_gross - c_gross, 6),
                "best_gross_policy": max(
                    (
                        ("A_SIMPLE_TAKE_1P00", a_gross),
                        ("B_FULL_RUNNER_MFE_GB1P50", b_gross),
                        ("C_SPLIT50_RUN50_BE_MFE_GB4P00", c_gross),
                    ),
                    key=lambda item: item[1],
                )[0],
            }
        )
    return rows


def _write_summary_md(
    path: Path,
    *,
    config: ArchitectureConfig,
    summary_rows: list[dict[str, Any]],
    comparison_rows: list[dict[str, Any]],
    source_check: dict[str, Any],
    p47e_crosscheck: dict[str, Any],
    sample_span_days: float,
) -> None:
    pooled = [row for row in summary_rows if row["scope"] == "HOLDOUT7"]
    pooled_by_policy = {row["policy_id"]: row for row in pooled}
    lines = [
        "# P47F — Frozen Exit Architecture OOS Comparison",
        "",
        "No Entry or Exit parameter is tuned in this run.",
        "The three architectures were fixed before this holdout comparison.",
        "",
        "## Frozen common prefix",
        "",
        f"- initial stop: -{config.initial_stop_pct:.2f}%",
        f"- +{config.early_activation_pct:.2f}% -> BE",
        f"- architecture decision point: +{config.activation_1p10_pct:.2f}%",
        f"- horizon: {config.horizon_hours}h",
        f"- sample calendar span: {sample_span_days:.2f} days",
        "",
        "## Architectures",
        "",
        "- A SIMPLE: at +1.10%, close 100% at modeled +1.00%.",
        "- B FULL RUNNER: keep 100%; floor +1.00%; MFE giveback 1.50%.",
        "- C SPLIT: 50% core at +1.00%; 50% runner, BE floor, MFE giveback 4.00%.",
        "",
        "## Holdout pooled",
        "",
        (
            "| policy | gross sum % | mean trade % | PF | max DD % | "
            "+1.10 reached | data-end | 30d eq % |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for policy_id in (
        "A_SIMPLE_TAKE_1P00",
        "B_FULL_RUNNER_MFE_GB1P50",
        "C_SPLIT50_RUN50_BE_MFE_GB4P00",
    ):
        row = pooled_by_policy[policy_id]
        lines.append(
            f"| {policy_id} | {row['gross_all_signal_sum_pct']} | "
            f"{row['gross_mean_trade_pct']} | {row['profit_factor_all']} | "
            f"{row['sequential_signal_max_drawdown_pct']} | "
            f"{row['activation_1p10_reached']} | {row['data_end_exits']} | "
            f"{row['fixed_notional_30d_equivalent_pct']} |"
        )
    lines.extend(
        [
            "",
            "## Per-asset gross comparison",
            "",
            "| symbol | A simple | B full runner | C split | B-A | C-A | B-C |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in comparison_rows:
        if row["scope"] == "HOLDOUT7":
            continue
        lines.append(
            f"| {row['scope']} | {row['A_simple_gross_pct']} | "
            f"{row['B_full_runner_gross_pct']} | {row['C_split_gross_pct']} | "
            f"{row['B_minus_A_pct_points']} | {row['C_minus_A_pct_points']} | "
            f"{row['B_minus_C_pct_points']} |"
        )
    pooled_compare = next(row for row in comparison_rows if row["scope"] == "HOLDOUT7")
    lines.extend(
        [
            "",
            "## Pooled deltas",
            "",
            f"- B minus A: **{pooled_compare['B_minus_A_pct_points']} pp**",
            f"- C minus A: **{pooled_compare['C_minus_A_pct_points']} pp**",
            f"- B minus C: **{pooled_compare['B_minus_C_pct_points']} pp**",
            "",
            "## Quality gates",
            "",
            f"- frozen source counts: `{json.dumps(source_check, ensure_ascii=False)}`",
            f"- P47E C-policy cross-check: `{json.dumps(p47e_crosscheck, ensure_ascii=False)}`",
            "",
            "## Guardrail",
            "",
            (
                "This holdout may compare the three pre-registered architectures, "
                "but it must not be used "
            ),
            "to retune the 1.50% or 4.00% giveback values. Any new parameter search requires a new "
            "forward/holdout sample.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_research(
    root: Path,
    *,
    output_dir: Path,
    config: ArchitectureConfig,
    symbols: tuple[str, ...] = HOLDOUT_SYMBOLS,
    period_tag: str = PERIOD_TAG,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = discover_holdout_sources(root, symbols, period_tag=period_tag)
    signals_by_symbol = {
        source.symbol: tuple(sorted(load_core_signals(source), key=lambda item: item.touch_at))
        for source in sources
    }
    source_counts = {symbol: len(signals_by_symbol[symbol]) for symbol in symbols}
    source_check = _expected_source_check(source_counts)
    if not source_check["all_match"]:
        raise RuntimeError(f"Frozen holdout source-count mismatch: {source_check}")

    total_signals = sum(source_counts.values())
    progress = ProgressReporter(config.progress_interval_seconds)
    progress.emit(
        "path+3policies",
        processed=0,
        total=total_signals,
        force=True,
        detail="build each 72h path once; simulate A/B/C immediately",
    )

    all_results: list[ArchitectureResult] = []
    cache_stats: dict[str, dict[str, int]] = {}
    touch_times: list[datetime] = []
    processed = 0

    for asset_index, source in enumerate(sources, start=1):
        archive_by_day = _archive_map(source.dataset_dir)
        cache = TradeDayCache(max_days=config.day_cache_size)
        print(
            f"[P47F] asset={asset_index}/{len(sources)} symbol={source.symbol} "
            f"signals={len(signals_by_symbol[source.symbol])} archives={len(archive_by_day)}",
            flush=True,
        )
        for signal in signals_by_symbol[source.symbol]:
            path = build_path_series(
                signal,
                archive_by_day,
                horizon_hours=config.horizon_hours,
                cache=cache,
            )
            touch_times.append(signal.touch_at)
            all_results.extend(simulate_three_architectures(path, config))
            processed += 1
            progress.emit(
                "path+3policies",
                processed=processed,
                total=total_signals,
                detail=(
                    f"symbol={source.symbol} cache_hits={cache.hits} "
                    f"cache_misses={cache.misses}"
                ),
            )
        cache_stats[source.symbol] = {"hits": cache.hits, "misses": cache.misses}

    sample_span_days = (
        (max(touch_times) - min(touch_times)).total_seconds() / 86400.0
        if len(touch_times) >= 2
        else 0.0
    )
    results = tuple(all_results)
    scopes = (*symbols, "HOLDOUT7")
    policy_ids: tuple[PolicyId, ...] = (
        "A_SIMPLE_TAKE_1P00",
        "B_FULL_RUNNER_MFE_GB1P50",
        "C_SPLIT50_RUN50_BE_MFE_GB4P00",
    )
    summary_rows = [
        summarise_results(
            results,
            policy_id=policy_id,
            scope=scope,
            sample_span_days=sample_span_days,
        )
        for scope in scopes
        for policy_id in policy_ids
    ]
    comparison_rows = _comparison_rows(summary_rows)
    pooled_c_row = next(
        row
        for row in summary_rows
        if row["scope"] == "HOLDOUT7"
        and row["policy_id"] == "C_SPLIT50_RUN50_BE_MFE_GB4P00"
    )
    p47e_crosscheck = _p47e_crosscheck(root, pooled_c_row)
    if p47e_crosscheck.get("applicable") and not (
        p47e_crosscheck.get("signals_match")
        and p47e_crosscheck.get("gross_match_within_1e_6")
    ):
        raise RuntimeError(f"P47E frozen C-policy cross-check failed: {p47e_crosscheck}")

    _write_csv(output_dir / "policy_results.csv", [_result_row(item) for item in results])
    _write_csv(output_dir / "policy_summary.csv", summary_rows)
    _write_csv(output_dir / "architecture_comparison.csv", comparison_rows)

    pooled_rows = {
        row["policy_id"]: row for row in summary_rows if row["scope"] == "HOLDOUT7"
    }
    summary = {
        "protocol": "P47F frozen A/B/C exit architecture OOS comparison",
        "research_only": True,
        "entry_frozen": True,
        "parameters_frozen": True,
        "holdout_symbols": list(symbols),
        "period_tag": period_tag,
        "config": asdict(config),
        "architectures": {
            "A_SIMPLE_TAKE_1P00": "100% take modeled +1.00% at +1.10 activation",
            "B_FULL_RUNNER_MFE_GB1P50": (
                "100% runner; +1.00 floor after +1.10; MFE giveback 1.50%"
            ),
            "C_SPLIT50_RUN50_BE_MFE_GB4P00": (
                "50% core at +1.00; 50% runner; BE floor; MFE giveback 4.00%"
            ),
        },
        "sample_span_days": round(sample_span_days, 6),
        "source_check": source_check,
        "p47e_crosscheck": p47e_crosscheck,
        "cache_stats": cache_stats,
        "pooled": pooled_rows,
        "comparison": next(row for row in comparison_rows if row["scope"] == "HOLDOUT7"),
        "guardrails": [
            "No Entry rule is changed.",
            "No giveback parameter is selected or tuned from holdout outcomes.",
            "P47E 1H trend hypothesis is not part of this architecture comparison.",
            "Fees/slippage/funding are not netted here; results are raw price-move equivalents.",
            (
                "Overlapping positions and portfolio sizing are not modeled in this "
                "signal-level replay."
            ),
        ],
    }
    _write_json(output_dir / "summary.json", summary)
    _write_summary_md(
        output_dir / "summary.md",
        config=config,
        summary_rows=summary_rows,
        comparison_rows=comparison_rows,
        source_check=source_check,
        p47e_crosscheck=p47e_crosscheck,
        sample_span_days=sample_span_days,
    )
    progress.emit(
        "done",
        processed=total_signals,
        total=total_signals,
        force=True,
        detail=f"output={output_dir}",
    )
    return summary


def _default_output_dir(root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return root / "reports" / "exit_architecture_oos_v1" / f"HOLDOUT7_{stamp}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "P47F frozen holdout comparison: A simple +1%, B full runner GB1.5, "
            "C 50/50 split GB4"
        )
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--day-cache-size", type=int, default=6)
    parser.add_argument("--progress-interval-seconds", type=float, default=25.0)
    args = parser.parse_args()

    root = args.root.resolve()
    output_dir = args.output_dir or _default_output_dir(root)
    config = ArchitectureConfig(
        day_cache_size=args.day_cache_size,
        progress_interval_seconds=args.progress_interval_seconds,
    )
    summary = run_research(root, output_dir=output_dir, config=config)
    comparison = summary["comparison"]
    print(f"P47F holdout signals: {summary['source_check']['actual_pooled']}")
    print(
        "P47F gross A/B/C: "
        f"{comparison['A_simple_gross_pct']} / "
        f"{comparison['B_full_runner_gross_pct']} / "
        f"{comparison['C_split_gross_pct']}"
    )
    print(
        "P47F deltas B-A / C-A / B-C: "
        f"{comparison['B_minus_A_pct_points']} / "
        f"{comparison['C_minus_A_pct_points']} / "
        f"{comparison['B_minus_C_pct_points']}"
    )
    print(f"Report: {output_dir / 'summary.json'}")
    print(f"Readable summary: {output_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
