from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from bybit_workbench.research.exit_break_even_v13 import (
    PathSeries,
    SignalSource,
    TradeDayCache,
    _resolve_latest_uni_p40,
    _resolve_link_p40,
    build_path_series,
    discover_source,
    load_core_signals,
)
from bybit_workbench.research.flow_reversal_v1 import _archive_map

PolicyFamily = Literal["control", "step", "mfe", "structural"]
ExitReason = Literal[
    "initial_stop",
    "early_be",
    "runner_stop",
    "horizon",
    "data_end",
]

DEFAULT_TARGET_LEVELS_PCT = (1.5, 2.0, 3.0, 5.0, 10.0)
DEFAULT_STEP_GIVEBACK_PCT = (0.25, 0.50, 0.75, 1.00)
DEFAULT_MFE_GIVEBACK_PCT = (0.25, 0.50, 0.75, 1.00, 1.50)
DEFAULT_STRUCTURAL_PRESETS = (
    (0.25, 0.25, 0.05),
    (0.50, 0.25, 0.05),
    (0.50, 0.50, 0.05),
    (0.75, 0.25, 0.10),
    (0.75, 0.50, 0.10),
)


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
            f"[P47B] stage={stage} processed={processed}/{total} "
            f"elapsed={_format_duration(elapsed)} ETA={eta_text}{suffix}",
            flush=True,
        )
        self.last_emit = now


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    initial_stop_pct: float = 1.0
    early_activation_pct: float = 0.10
    early_floor_pct: float = 0.0
    runner_activation_pct: float = 1.10
    runner_floor_pct: float = 1.00
    horizon_hours: int = 72
    target_levels_pct: tuple[float, ...] = DEFAULT_TARGET_LEVELS_PCT
    step_giveback_pct: tuple[float, ...] = DEFAULT_STEP_GIVEBACK_PCT
    mfe_giveback_pct: tuple[float, ...] = DEFAULT_MFE_GIVEBACK_PCT
    structural_presets: tuple[tuple[float, float, float], ...] = (
        DEFAULT_STRUCTURAL_PRESETS
    )
    day_cache_size: int = 6
    progress_interval_seconds: float = 25.0

    def __post_init__(self) -> None:
        if self.initial_stop_pct <= 0:
            raise ValueError("initial_stop_pct must be positive")
        if not 0 <= self.early_floor_pct < self.early_activation_pct:
            raise ValueError("early floor must be >= 0 and below early activation")
        if self.runner_activation_pct <= self.early_activation_pct:
            raise ValueError("runner activation must be above early activation")
        if not self.early_floor_pct <= self.runner_floor_pct < self.runner_activation_pct:
            raise ValueError("runner floor must be between early floor and runner activation")
        if self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be positive")
        if any(value <= self.runner_activation_pct for value in self.target_levels_pct):
            raise ValueError("target levels must be above runner activation")
        if any(value <= 0 for value in self.step_giveback_pct):
            raise ValueError("step giveback values must be positive")
        if any(value <= 0 for value in self.mfe_giveback_pct):
            raise ValueError("MFE giveback values must be positive")
        for pullback, rebound, buffer_pct in self.structural_presets:
            if pullback <= 0 or rebound <= 0 or buffer_pct < 0:
                raise ValueError("structural preset values must be positive/non-negative")
        if self.day_cache_size <= 0:
            raise ValueError("day_cache_size must be positive")
        if self.progress_interval_seconds <= 0:
            raise ValueError("progress_interval_seconds must be positive")


@dataclass(frozen=True, slots=True)
class PolicySpec:
    policy_id: str
    family: PolicyFamily
    giveback_pct: float = 0.0
    pullback_pct: float = 0.0
    rebound_pct: float = 0.0
    buffer_pct: float = 0.0

    @property
    def parameters_json(self) -> str:
        payload = {
            "giveback_pct": self.giveback_pct,
            "pullback_pct": self.pullback_pct,
            "rebound_pct": self.rebound_pct,
            "buffer_pct": self.buffer_pct,
        }
        return json.dumps(payload, sort_keys=True)


@dataclass(frozen=True, slots=True)
class RunnerPolicyResult:
    symbol: str
    touch_at: datetime
    policy_id: str
    family: PolicyFamily
    exit_reason: ExitReason
    exit_at: datetime
    exit_move_pct: float
    completed_horizon: bool
    early_activated: bool
    runner_activated: bool
    runner_activation_at: datetime | None
    max_favorable_pct: float
    max_locked_floor_pct: float
    target_hits_pct: tuple[float, ...]


@dataclass(slots=True)
class StructuralState:
    peak_pct: float
    in_pullback: bool = False
    pullback_low_pct: float | None = None


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


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


def _event_at(path: PathSeries, index: int) -> datetime:
    return datetime.fromtimestamp(path.timestamps[index], UTC)


def build_policy_specs(config: RunnerConfig) -> tuple[PolicySpec, ...]:
    specs: list[PolicySpec] = [
        PolicySpec(policy_id="CONTROL_FLOOR_1P00", family="control")
    ]
    specs.extend(
        PolicySpec(
            policy_id=f"STEP_GIVEBACK_{value:.2f}",
            family="step",
            giveback_pct=value,
        )
        for value in config.step_giveback_pct
    )
    specs.extend(
        PolicySpec(
            policy_id=f"MFE_GIVEBACK_{value:.2f}",
            family="mfe",
            giveback_pct=value,
        )
        for value in config.mfe_giveback_pct
    )
    specs.extend(
        PolicySpec(
            policy_id=(
                f"STRUCT_PB{pullback:.2f}_RB{rebound:.2f}_BUF{buffer_pct:.2f}"
            ),
            family="structural",
            pullback_pct=pullback,
            rebound_pct=rebound,
            buffer_pct=buffer_pct,
        )
        for pullback, rebound, buffer_pct in config.structural_presets
    )
    return tuple(specs)


def _step_floor(move: float, spec: PolicySpec, config: RunnerConfig) -> float:
    floor = config.runner_floor_pct
    for milestone in config.target_levels_pct:
        if move < milestone:
            break
        floor = max(floor, milestone - spec.giveback_pct)
    return floor


def _update_structural_floor(
    move: float,
    current_floor: float,
    state: StructuralState,
    spec: PolicySpec,
) -> float:
    if not state.in_pullback:
        state.peak_pct = max(state.peak_pct, move)
        if move <= state.peak_pct - spec.pullback_pct:
            state.in_pullback = True
            state.pullback_low_pct = move
        return current_floor

    low = state.pullback_low_pct
    if low is None or move < low:
        state.pullback_low_pct = move
        low = move
    if move >= low + spec.rebound_pct:
        confirmed_floor = low - spec.buffer_pct
        current_floor = max(current_floor, confirmed_floor)
        state.in_pullback = False
        state.pullback_low_pct = None
        state.peak_pct = move
    return current_floor


def simulate_runner_policy(
    path: PathSeries,
    spec: PolicySpec,
    config: RunnerConfig,
) -> RunnerPolicyResult:
    horizon_at = path.signal.touch_at + timedelta(hours=config.horizon_hours)
    horizon_ts = horizon_at.timestamp()
    early_activated = False
    runner_activated = False
    runner_activation_at: datetime | None = None
    stop_floor = -config.initial_stop_pct
    max_locked_floor = stop_floor
    max_favorable = 0.0
    target_hits: set[float] = set()
    structural_state: StructuralState | None = None

    last_index = -1
    for index, (timestamp, move) in enumerate(
        zip(path.timestamps, path.moves_pct, strict=True)
    ):
        if timestamp > horizon_ts:
            break
        last_index = index
        max_favorable = max(max_favorable, move)
        event_at = datetime.fromtimestamp(timestamp, UTC)

        if not early_activated:
            if move <= -config.initial_stop_pct:
                return RunnerPolicyResult(
                    symbol=path.signal.symbol,
                    touch_at=path.signal.touch_at,
                    policy_id=spec.policy_id,
                    family=spec.family,
                    exit_reason="initial_stop",
                    exit_at=event_at,
                    exit_move_pct=-config.initial_stop_pct,
                    completed_horizon=True,
                    early_activated=False,
                    runner_activated=False,
                    runner_activation_at=None,
                    max_favorable_pct=max_favorable,
                    max_locked_floor_pct=-config.initial_stop_pct,
                    target_hits_pct=(),
                )
            if move >= config.early_activation_pct:
                early_activated = True
                stop_floor = config.early_floor_pct
                max_locked_floor = max(max_locked_floor, stop_floor)
            continue

        if not runner_activated:
            if move <= stop_floor:
                return RunnerPolicyResult(
                    symbol=path.signal.symbol,
                    touch_at=path.signal.touch_at,
                    policy_id=spec.policy_id,
                    family=spec.family,
                    exit_reason="early_be",
                    exit_at=event_at,
                    exit_move_pct=stop_floor,
                    completed_horizon=True,
                    early_activated=True,
                    runner_activated=False,
                    runner_activation_at=None,
                    max_favorable_pct=max_favorable,
                    max_locked_floor_pct=max_locked_floor,
                    target_hits_pct=(),
                )
            if move >= config.runner_activation_pct:
                runner_activated = True
                runner_activation_at = event_at
                stop_floor = max(stop_floor, config.runner_floor_pct)
                max_locked_floor = max(max_locked_floor, stop_floor)
                if spec.family == "structural":
                    structural_state = StructuralState(peak_pct=move)
            continue

        if move <= stop_floor:
            return RunnerPolicyResult(
                symbol=path.signal.symbol,
                touch_at=path.signal.touch_at,
                policy_id=spec.policy_id,
                family=spec.family,
                exit_reason="runner_stop",
                exit_at=event_at,
                exit_move_pct=stop_floor,
                completed_horizon=True,
                early_activated=True,
                runner_activated=True,
                runner_activation_at=runner_activation_at,
                max_favorable_pct=max_favorable,
                max_locked_floor_pct=max_locked_floor,
                target_hits_pct=tuple(sorted(target_hits)),
            )

        for target in config.target_levels_pct:
            if move >= target:
                target_hits.add(target)

        if spec.family == "step":
            stop_floor = max(stop_floor, _step_floor(move, spec, config))
        elif spec.family == "mfe":
            stop_floor = max(
                stop_floor,
                max_favorable - spec.giveback_pct,
            )
        elif spec.family == "structural":
            if structural_state is None:
                raise RuntimeError("structural state was not initialized")
            stop_floor = _update_structural_floor(
                move,
                stop_floor,
                structural_state,
                spec,
            )
        max_locked_floor = max(max_locked_floor, stop_floor)

    if path.complete_through >= horizon_at:
        terminal_move = path.moves_pct[last_index] if last_index >= 0 else 0.0
        return RunnerPolicyResult(
            symbol=path.signal.symbol,
            touch_at=path.signal.touch_at,
            policy_id=spec.policy_id,
            family=spec.family,
            exit_reason="horizon",
            exit_at=horizon_at,
            exit_move_pct=terminal_move,
            completed_horizon=True,
            early_activated=early_activated,
            runner_activated=runner_activated,
            runner_activation_at=runner_activation_at,
            max_favorable_pct=max_favorable,
            max_locked_floor_pct=max_locked_floor,
            target_hits_pct=tuple(sorted(target_hits)),
        )

    terminal_move = path.moves_pct[last_index] if last_index >= 0 else 0.0
    return RunnerPolicyResult(
        symbol=path.signal.symbol,
        touch_at=path.signal.touch_at,
        policy_id=spec.policy_id,
        family=spec.family,
        exit_reason="data_end",
        exit_at=path.available_until,
        exit_move_pct=terminal_move,
        completed_horizon=False,
        early_activated=early_activated,
        runner_activated=runner_activated,
        runner_activation_at=runner_activation_at,
        max_favorable_pct=max_favorable,
        max_locked_floor_pct=max_locked_floor,
        target_hits_pct=tuple(sorted(target_hits)),
    )


def _profit_factor(values: list[float]) -> float | None:
    positive = sum(value for value in values if value > 0)
    negative = -sum(value for value in values if value < 0)
    if negative <= 0:
        return None
    return round(positive / negative, 6)


def _scope_results(
    results: tuple[RunnerPolicyResult, ...],
    scope: str,
) -> tuple[RunnerPolicyResult, ...]:
    if scope == "POOLED_UNI_LINK":
        return results
    return tuple(item for item in results if item.symbol == scope)


def summarise_policy_results(
    results: tuple[RunnerPolicyResult, ...],
    *,
    spec: PolicySpec,
    scope: str,
    config: RunnerConfig,
    sample_span_days: float,
) -> dict[str, Any]:
    scoped = _scope_results(results, scope)
    decision_grade = tuple(item for item in scoped if item.completed_horizon)
    values = [item.exit_move_pct for item in decision_grade]
    closed_values = [
        item.exit_move_pct
        for item in scoped
        if item.exit_reason in {"initial_stop", "early_be", "runner_stop"}
    ]
    gross_sum = sum(values)
    monthly_equivalent = (
        gross_sum / sample_span_days * 30.0 if sample_span_days > 0 else None
    )
    row: dict[str, Any] = {
        "scope": scope,
        "policy_id": spec.policy_id,
        "family": spec.family,
        "parameters_json": spec.parameters_json,
        "signals": len(scoped),
        "decision_grade": len(decision_grade),
        "censored": len(scoped) - len(decision_grade),
        "initial_stop_exits": sum(item.exit_reason == "initial_stop" for item in scoped),
        "early_be_exits": sum(item.exit_reason == "early_be" for item in scoped),
        "runner_activated": sum(item.runner_activated for item in scoped),
        "runner_stop_exits": sum(item.exit_reason == "runner_stop" for item in scoped),
        "horizon_exits": sum(item.exit_reason == "horizon" for item in scoped),
        "data_end_exits": sum(item.exit_reason == "data_end" for item in scoped),
        "gross_signal_sum_pct": round(gross_sum, 6),
        "gross_mean_trade_pct": (
            round(gross_sum / len(values), 6) if values else None
        ),
        "gross_median_trade_pct": _median(values),
        "profit_factor": _profit_factor(values),
        "positive_trade_count": sum(value > 0 for value in values),
        "negative_trade_count": sum(value < 0 for value in values),
        "scratch_trade_count": sum(abs(value) <= 1e-12 for value in values),
        "closed_floor_sum_pct": round(sum(closed_values), 6),
        "fixed_notional_30d_equivalent_pct": (
            round(monthly_equivalent, 6) if monthly_equivalent is not None else None
        ),
        "exit_p10_pct": _quantile(values, 0.10),
        "exit_p25_pct": _quantile(values, 0.25),
        "exit_p75_pct": _quantile(values, 0.75),
        "exit_p90_pct": _quantile(values, 0.90),
    }
    for target in config.target_levels_pct:
        key = str(target).replace(".", "_")
        hit_count = sum(target in item.target_hits_pct for item in scoped)
        row[f"hit_{key}_pct_before_exit"] = hit_count
        row[f"hit_{key}_pct_before_exit_percent"] = (
            round(hit_count / len(scoped) * 100.0, 2) if scoped else None
        )
    return row


def _result_csv_row(result: RunnerPolicyResult) -> dict[str, Any]:
    row = asdict(result)
    row["target_hits_pct"] = ",".join(str(value) for value in result.target_hits_pct)
    return row


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def _prefix_reference_check(
    control_results: tuple[RunnerPolicyResult, ...],
    config: RunnerConfig,
) -> dict[str, Any]:
    applicable = (
        config.initial_stop_pct == 1.0
        and config.early_activation_pct == 0.10
        and config.early_floor_pct == 0.0
        and config.runner_activation_pct == 1.10
        and config.runner_floor_pct == 1.00
        and config.horizon_hours == 72
        and len(control_results) == 227
    )
    if not applicable:
        return {
            "applicable": False,
            "all_match": None,
            "reason": "P47B parameters differ from the frozen UNI/LINK reference.",
        }
    actual = {
        "signals": len(control_results),
        "initial_stop_exits": sum(
            item.exit_reason == "initial_stop" for item in control_results
        ),
        "early_activated": sum(item.early_activated for item in control_results),
        "runner_activated": sum(item.runner_activated for item in control_results),
        "early_be_exits": sum(item.exit_reason == "early_be" for item in control_results),
    }
    expected = {
        "signals": 227,
        "initial_stop_exits": 16,
        "early_activated": 211,
        "runner_activated": 27,
        "early_be_exits": 184,
    }
    return {
        "applicable": True,
        "all_match": actual == expected,
        "expected": expected,
        "actual": actual,
    }


def _write_summary_md(
    path: Path,
    *,
    config: RunnerConfig,
    policy_rows: list[dict[str, Any]],
    prefix_check: dict[str, Any],
    sample_span_days: float,
) -> None:
    pooled = [row for row in policy_rows if row["scope"] == "POOLED_UNI_LINK"]
    ranked = sorted(
        pooled,
        key=lambda row: float(row["gross_signal_sum_pct"]),
        reverse=True,
    )
    lines = [
        "# P47B Runner Management V1",
        "",
        "Entry V1 remains frozen. This module changes no live execution logic.",
        "",
        "## Frozen prefix",
        "",
        f"- Initial stop: -{config.initial_stop_pct:.2f}%",
        (
            f"- +{config.early_activation_pct:.2f}% -> floor "
            f"+{config.early_floor_pct:.2f}%"
        ),
        (
            f"- +{config.runner_activation_pct:.2f}% -> floor "
            f"+{config.runner_floor_pct:.2f}% -> RUNNER"
        ),
        f"- Horizon: {config.horizon_hours}h",
        f"- Sample calendar span: {sample_span_days:.2f} days",
        "",
        "## Prefix reference check",
        "",
        f"`{json.dumps(prefix_check, ensure_ascii=False, default=str)}`",
        "",
        "## Pooled policies ranked by gross fixed-notional signal sum",
        "",
        (
            "| policy | family | gross sum % | mean trade % | PF | runner activated | "
            "runner stops | 30d eq % | +2 hit | +3 hit | +5 hit | +10 hit |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in ranked:
        lines.append(
            "| {policy_id} | {family} | {gross_signal_sum_pct} | "
            "{gross_mean_trade_pct} | {profit_factor} | {runner_activated} | "
            "{runner_stop_exits} | {fixed_notional_30d_equivalent_pct} | "
            "{hit_2_0_pct_before_exit} | {hit_3_0_pct_before_exit} | "
            "{hit_5_0_pct_before_exit} | {hit_10_0_pct_before_exit} |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Interpretation guardrails",
            "",
            (
                "- Gross percentages are directional price moves on equal notional; "
                "fees, slippage, funding, sizing and leverage are not deducted here."
            ),
            (
                "- `fixed_notional_30d_equivalent_pct` is a signal-sum normalization "
                "over the sample calendar span, not an equity-return promise."
            ),
            (
                "- `horizon` exits are marked to the final price at 72h; `data_end` "
                "rows are censored and excluded from decision-grade P&L metrics."
            ),
            (
                "- Structural stops are causal: a pullback low is used only after a "
                "subsequent rebound confirms it. No future swing point is used."
            ),
            (
                "- The purpose is to compare management after +1.10%; the prefix "
                "before +1.10% is frozen and must not be re-tuned from this report."
            ),
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_research(
    sources: tuple[SignalSource, ...],
    *,
    output_dir: Path,
    config: RunnerConfig,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    progress = ProgressReporter(config.progress_interval_seconds)
    all_paths: list[PathSeries] = []
    cache_stats: dict[str, dict[str, int]] = {}

    signals_by_symbol = {
        source.symbol: tuple(
            sorted(load_core_signals(source), key=lambda item: item.touch_at)
        )
        for source in sources
    }
    total_signals = sum(len(items) for items in signals_by_symbol.values())
    processed = 0
    progress.emit(
        "path-build",
        processed=0,
        total=total_signals,
        force=True,
        detail="building corrected 72h paths",
    )
    for source in sources:
        archive_by_day = _archive_map(source.dataset_dir)
        cache = TradeDayCache(max_days=config.day_cache_size)
        for signal in signals_by_symbol[source.symbol]:
            all_paths.append(
                build_path_series(
                    signal,
                    archive_by_day,
                    horizon_hours=config.horizon_hours,
                    cache=cache,
                )
            )
            processed += 1
            progress.emit(
                "path-build",
                processed=processed,
                total=total_signals,
                detail=(
                    f"symbol={source.symbol} cache_hits={cache.hits} "
                    f"cache_misses={cache.misses}"
                ),
            )
        cache_stats[source.symbol] = {"hits": cache.hits, "misses": cache.misses}

    paths = tuple(all_paths)
    touch_times = [path.signal.touch_at for path in paths]
    sample_span_days = (
        (max(touch_times) - min(touch_times)).total_seconds() / 86400.0
        if len(touch_times) >= 2
        else 0.0
    )
    specs = build_policy_specs(config)
    scopes = tuple(source.symbol for source in sources) + ("POOLED_UNI_LINK",)
    all_results: list[RunnerPolicyResult] = []
    policy_rows: list[dict[str, Any]] = []
    policy_progress = ProgressReporter(config.progress_interval_seconds)
    policy_progress.emit(
        "runner-grid",
        processed=0,
        total=len(specs),
        force=True,
        detail=f"paths={len(paths)} prefix frozen",
    )
    control_results: tuple[RunnerPolicyResult, ...] = ()
    for index, spec in enumerate(specs, start=1):
        results = tuple(simulate_runner_policy(path, spec, config) for path in paths)
        if spec.family == "control":
            control_results = results
        all_results.extend(results)
        for scope in scopes:
            policy_rows.append(
                summarise_policy_results(
                    results,
                    spec=spec,
                    scope=scope,
                    config=config,
                    sample_span_days=sample_span_days,
                )
            )
        policy_progress.emit(
            "runner-grid",
            processed=index,
            total=len(specs),
            detail=f"policy={spec.policy_id}",
        )

    prefix_check = _prefix_reference_check(control_results, config)
    _write_csv(
        output_dir / "policy_results.csv",
        [_result_csv_row(result) for result in all_results],
    )
    _write_csv(output_dir / "policy_summary.csv", policy_rows)
    summary = {
        "architecture": "p47b_runner_management_v1",
        "research_only": True,
        "entry_frozen": True,
        "prefix_frozen": True,
        "config": asdict(config),
        "signals": len(paths),
        "policies": len(specs),
        "sample_span_days": round(sample_span_days, 6),
        "cache_stats": cache_stats,
        "prefix_reference_check": prefix_check,
        "policy_specs": [asdict(spec) for spec in specs],
        "notes": [
            "No re-entry, partial TP, sizing or leverage is modeled.",
            "Early BE is raw price BE in this research run; execution costs are separate.",
            "Runner floor begins only after +1.10% and never moves below +1.00%.",
            "Censored data_end rows are excluded from decision-grade P&L summaries.",
        ],
    }
    _write_json(output_dir / "summary.json", summary)
    _write_summary_md(
        output_dir / "summary.md",
        config=config,
        policy_rows=policy_rows,
        prefix_check=prefix_check,
        sample_span_days=sample_span_days,
    )
    policy_progress.emit(
        "done",
        processed=1,
        total=1,
        force=True,
        detail=f"output={output_dir} prefix_crosscheck={prefix_check.get('all_match')}",
    )
    return summary


def _parse_float_tuple(raw: str) -> tuple[float, ...]:
    values = tuple(float(item.strip()) for item in raw.split(",") if item.strip())
    if not values:
        raise ValueError("at least one numeric value is required")
    return values


def _default_output_dir(root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return root / "reports" / "runner_management_v1" / f"UNI_LINK_{stamp}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P47B Runner Management after frozen +0.10 -> BE -> +1.10/+1.00 prefix"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--uni-p40-dir", type=Path)
    parser.add_argument("--link-p40-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--initial-stop-pct", type=float, default=1.0)
    parser.add_argument("--early-activation-pct", type=float, default=0.10)
    parser.add_argument("--early-floor-pct", type=float, default=0.0)
    parser.add_argument("--runner-activation-pct", type=float, default=1.10)
    parser.add_argument("--runner-floor-pct", type=float, default=1.00)
    parser.add_argument("--horizon-hours", type=int, default=72)
    parser.add_argument("--day-cache-size", type=int, default=6)
    parser.add_argument("--progress-interval-seconds", type=float, default=25.0)
    parser.add_argument(
        "--target-levels-pct",
        default=",".join(str(value) for value in DEFAULT_TARGET_LEVELS_PCT),
    )
    parser.add_argument(
        "--step-giveback-pct",
        default=",".join(str(value) for value in DEFAULT_STEP_GIVEBACK_PCT),
    )
    parser.add_argument(
        "--mfe-giveback-pct",
        default=",".join(str(value) for value in DEFAULT_MFE_GIVEBACK_PCT),
    )
    args = parser.parse_args()

    root = args.root.resolve()
    uni_dir = args.uni_p40_dir or _resolve_latest_uni_p40(root)
    link_dir = args.link_p40_dir or _resolve_link_p40(root)
    output_dir = args.output_dir or _default_output_dir(root)
    config = RunnerConfig(
        initial_stop_pct=args.initial_stop_pct,
        early_activation_pct=args.early_activation_pct,
        early_floor_pct=args.early_floor_pct,
        runner_activation_pct=args.runner_activation_pct,
        runner_floor_pct=args.runner_floor_pct,
        horizon_hours=args.horizon_hours,
        target_levels_pct=_parse_float_tuple(args.target_levels_pct),
        step_giveback_pct=_parse_float_tuple(args.step_giveback_pct),
        mfe_giveback_pct=_parse_float_tuple(args.mfe_giveback_pct),
        day_cache_size=args.day_cache_size,
        progress_interval_seconds=args.progress_interval_seconds,
    )
    sources = (discover_source(uni_dir), discover_source(link_dir))
    summary = run_research(sources, output_dir=output_dir, config=config)
    print(f"P47B sources: {', '.join(source.symbol for source in sources)}")
    print(f"P47B signals: {summary['signals']}")
    print(f"P47B policies: {summary['policies']}")
    print(f"Prefix cross-check: {summary['prefix_reference_check']}")
    print(f"Report: {output_dir / 'summary.json'}")
    print(f"Readable summary: {output_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
