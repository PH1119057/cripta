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

PolicyFamily = Literal["core_only", "hold", "mfe"]
RunnerFloorMode = Literal["be", "funded"]
ExitReason = Literal[
    "initial_stop",
    "early_be",
    "core_take",
    "runner_stop",
    "horizon",
    "data_end",
]

DEFAULT_CORE_FRACTIONS = (1.00, 0.80, 0.75, 0.50)
DEFAULT_MFE_GIVEBACK_PCT = (1.50, 2.00, 2.50, 3.00, 4.00, 5.00)
DEFAULT_TARGET_LEVELS_PCT = (1.5, 2.0, 3.0, 5.0, 10.0)


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
            f"[P47C] stage={stage} processed={processed}/{total} "
            f"elapsed={_format_duration(elapsed)} ETA={eta_text}{suffix}",
            flush=True,
        )
        self.last_emit = now


@dataclass(frozen=True, slots=True)
class SplitConfig:
    initial_stop_pct: float = 1.0
    early_activation_pct: float = 0.10
    early_floor_pct: float = 0.0
    split_activation_pct: float = 1.10
    core_exit_pct: float = 1.00
    horizon_hours: int = 72
    core_fractions: tuple[float, ...] = DEFAULT_CORE_FRACTIONS
    mfe_giveback_pct: tuple[float, ...] = DEFAULT_MFE_GIVEBACK_PCT
    target_levels_pct: tuple[float, ...] = DEFAULT_TARGET_LEVELS_PCT
    day_cache_size: int = 6
    progress_interval_seconds: float = 25.0

    def __post_init__(self) -> None:
        if self.initial_stop_pct <= 0:
            raise ValueError("initial_stop_pct must be positive")
        if not 0 <= self.early_floor_pct < self.early_activation_pct:
            raise ValueError("early floor must be >= 0 and below early activation")
        if self.split_activation_pct <= self.early_activation_pct:
            raise ValueError("split activation must be above early activation")
        if not self.early_floor_pct < self.core_exit_pct <= self.split_activation_pct:
            raise ValueError("core exit must be above early floor and <= split activation")
        if self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be positive")
        if not self.core_fractions:
            raise ValueError("at least one core fraction is required")
        for fraction in self.core_fractions:
            if not 0.5 <= fraction <= 1.0:
                raise ValueError("core fractions must be between 0.50 and 1.00")
        if 1.0 not in self.core_fractions:
            raise ValueError("core fractions must include 1.00 control")
        if any(value <= 0 for value in self.mfe_giveback_pct):
            raise ValueError("MFE giveback values must be positive")
        if any(value <= self.split_activation_pct for value in self.target_levels_pct):
            raise ValueError("target levels must be above split activation")
        if self.day_cache_size <= 0:
            raise ValueError("day_cache_size must be positive")
        if self.progress_interval_seconds <= 0:
            raise ValueError("progress_interval_seconds must be positive")


@dataclass(frozen=True, slots=True)
class SplitPolicySpec:
    policy_id: str
    family: PolicyFamily
    core_fraction: float
    floor_mode: RunnerFloorMode | None = None
    giveback_pct: float = 0.0

    @property
    def runner_fraction(self) -> float:
        return 1.0 - self.core_fraction

    @property
    def parameters_json(self) -> str:
        return json.dumps(
            {
                "core_fraction": self.core_fraction,
                "runner_fraction": self.runner_fraction,
                "floor_mode": self.floor_mode,
                "giveback_pct": self.giveback_pct,
            },
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class SplitPolicyResult:
    symbol: str
    touch_at: datetime
    policy_id: str
    family: PolicyFamily
    core_fraction: float
    runner_fraction: float
    floor_mode: RunnerFloorMode | None
    exit_reason: ExitReason
    exit_at: datetime
    exit_move_pct: float
    completed_horizon: bool
    early_activated: bool
    split_activated: bool
    split_activation_at: datetime | None
    core_component_pct: float
    runner_component_pct: float
    runner_exit_move_pct: float | None
    runner_base_floor_pct: float | None
    max_favorable_pct: float
    max_episode_locked_floor_pct: float
    target_hits_pct: tuple[float, ...]


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


def _fraction_tag(value: float) -> str:
    return f"{int(round(value * 100)):03d}"


def build_policy_specs(config: SplitConfig) -> tuple[SplitPolicySpec, ...]:
    specs: list[SplitPolicySpec] = [
        SplitPolicySpec(
            policy_id="CORE100_TAKE_1P00",
            family="core_only",
            core_fraction=1.0,
        )
    ]
    for core_fraction in config.core_fractions:
        if core_fraction >= 1.0:
            continue
        core_tag = _fraction_tag(core_fraction)
        runner_tag = _fraction_tag(1.0 - core_fraction)
        for floor_mode in ("be", "funded"):
            floor_tag = "BE" if floor_mode == "be" else "FUNDED_M1"
            specs.append(
                SplitPolicySpec(
                    policy_id=(
                        f"CORE{core_tag}_RUN{runner_tag}_{floor_tag}_HOLD"
                    ),
                    family="hold",
                    core_fraction=core_fraction,
                    floor_mode=floor_mode,
                )
            )
            specs.extend(
                SplitPolicySpec(
                    policy_id=(
                        f"CORE{core_tag}_RUN{runner_tag}_{floor_tag}_"
                        f"MFE_GB{giveback:.2f}"
                    ),
                    family="mfe",
                    core_fraction=core_fraction,
                    floor_mode=floor_mode,
                    giveback_pct=giveback,
                )
                for giveback in config.mfe_giveback_pct
            )
    return tuple(specs)


def _runner_base_floor(spec: SplitPolicySpec, config: SplitConfig) -> float:
    if spec.floor_mode == "be":
        return config.early_floor_pct
    if spec.floor_mode == "funded":
        return -config.initial_stop_pct
    raise ValueError("runner floor mode is required for split policies")


def _episode_move(
    spec: SplitPolicySpec,
    config: SplitConfig,
    runner_move_pct: float,
) -> tuple[float, float, float]:
    core_component = spec.core_fraction * config.core_exit_pct
    runner_component = spec.runner_fraction * runner_move_pct
    return core_component + runner_component, core_component, runner_component


def simulate_split_policy(
    path: PathSeries,
    spec: SplitPolicySpec,
    config: SplitConfig,
) -> SplitPolicyResult:
    horizon_at = path.signal.touch_at + timedelta(hours=config.horizon_hours)
    horizon_ts = horizon_at.timestamp()
    early_activated = False
    split_activated = False
    split_activation_at: datetime | None = None
    stop_floor = -config.initial_stop_pct
    max_favorable = 0.0
    target_hits: set[float] = set()
    runner_base_floor: float | None = None
    max_episode_locked_floor = -config.initial_stop_pct

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
                return SplitPolicyResult(
                    symbol=path.signal.symbol,
                    touch_at=path.signal.touch_at,
                    policy_id=spec.policy_id,
                    family=spec.family,
                    core_fraction=spec.core_fraction,
                    runner_fraction=spec.runner_fraction,
                    floor_mode=spec.floor_mode,
                    exit_reason="initial_stop",
                    exit_at=event_at,
                    exit_move_pct=-config.initial_stop_pct,
                    completed_horizon=True,
                    early_activated=False,
                    split_activated=False,
                    split_activation_at=None,
                    core_component_pct=0.0,
                    runner_component_pct=0.0,
                    runner_exit_move_pct=None,
                    runner_base_floor_pct=None,
                    max_favorable_pct=max_favorable,
                    max_episode_locked_floor_pct=-config.initial_stop_pct,
                    target_hits_pct=(),
                )
            if move >= config.early_activation_pct:
                early_activated = True
                stop_floor = config.early_floor_pct
                max_episode_locked_floor = max(max_episode_locked_floor, stop_floor)
            continue

        if not split_activated:
            if move <= stop_floor:
                return SplitPolicyResult(
                    symbol=path.signal.symbol,
                    touch_at=path.signal.touch_at,
                    policy_id=spec.policy_id,
                    family=spec.family,
                    core_fraction=spec.core_fraction,
                    runner_fraction=spec.runner_fraction,
                    floor_mode=spec.floor_mode,
                    exit_reason="early_be",
                    exit_at=event_at,
                    exit_move_pct=stop_floor,
                    completed_horizon=True,
                    early_activated=True,
                    split_activated=False,
                    split_activation_at=None,
                    core_component_pct=0.0,
                    runner_component_pct=0.0,
                    runner_exit_move_pct=None,
                    runner_base_floor_pct=None,
                    max_favorable_pct=max_favorable,
                    max_episode_locked_floor_pct=max_episode_locked_floor,
                    target_hits_pct=(),
                )
            if move >= config.split_activation_pct:
                split_activated = True
                split_activation_at = event_at
                if spec.family == "core_only":
                    return SplitPolicyResult(
                        symbol=path.signal.symbol,
                        touch_at=path.signal.touch_at,
                        policy_id=spec.policy_id,
                        family=spec.family,
                        core_fraction=1.0,
                        runner_fraction=0.0,
                        floor_mode=None,
                        exit_reason="core_take",
                        exit_at=event_at,
                        exit_move_pct=config.core_exit_pct,
                        completed_horizon=True,
                        early_activated=True,
                        split_activated=True,
                        split_activation_at=split_activation_at,
                        core_component_pct=config.core_exit_pct,
                        runner_component_pct=0.0,
                        runner_exit_move_pct=None,
                        runner_base_floor_pct=None,
                        max_favorable_pct=max_favorable,
                        max_episode_locked_floor_pct=config.core_exit_pct,
                        target_hits_pct=(),
                    )
                runner_base_floor = _runner_base_floor(spec, config)
                stop_floor = runner_base_floor
                episode_floor, _, _ = _episode_move(spec, config, stop_floor)
                max_episode_locked_floor = max(
                    max_episode_locked_floor,
                    episode_floor,
                )
            continue

        if runner_base_floor is None:
            raise RuntimeError("runner base floor was not initialized")

        if move <= stop_floor:
            episode_move, core_component, runner_component = _episode_move(
                spec,
                config,
                stop_floor,
            )
            return SplitPolicyResult(
                symbol=path.signal.symbol,
                touch_at=path.signal.touch_at,
                policy_id=spec.policy_id,
                family=spec.family,
                core_fraction=spec.core_fraction,
                runner_fraction=spec.runner_fraction,
                floor_mode=spec.floor_mode,
                exit_reason="runner_stop",
                exit_at=event_at,
                exit_move_pct=episode_move,
                completed_horizon=True,
                early_activated=True,
                split_activated=True,
                split_activation_at=split_activation_at,
                core_component_pct=core_component,
                runner_component_pct=runner_component,
                runner_exit_move_pct=stop_floor,
                runner_base_floor_pct=runner_base_floor,
                max_favorable_pct=max_favorable,
                max_episode_locked_floor_pct=max_episode_locked_floor,
                target_hits_pct=tuple(sorted(target_hits)),
            )

        for target in config.target_levels_pct:
            if move >= target:
                target_hits.add(target)

        if spec.family == "mfe":
            stop_floor = max(
                runner_base_floor,
                max_favorable - spec.giveback_pct,
            )
        episode_floor, _, _ = _episode_move(spec, config, stop_floor)
        max_episode_locked_floor = max(max_episode_locked_floor, episode_floor)

    terminal_move = path.moves_pct[last_index] if last_index >= 0 else 0.0
    if not split_activated:
        if path.complete_through >= horizon_at:
            return SplitPolicyResult(
                symbol=path.signal.symbol,
                touch_at=path.signal.touch_at,
                policy_id=spec.policy_id,
                family=spec.family,
                core_fraction=spec.core_fraction,
                runner_fraction=spec.runner_fraction,
                floor_mode=spec.floor_mode,
                exit_reason="horizon",
                exit_at=horizon_at,
                exit_move_pct=terminal_move,
                completed_horizon=True,
                early_activated=early_activated,
                split_activated=False,
                split_activation_at=None,
                core_component_pct=0.0,
                runner_component_pct=0.0,
                runner_exit_move_pct=None,
                runner_base_floor_pct=None,
                max_favorable_pct=max_favorable,
                max_episode_locked_floor_pct=max_episode_locked_floor,
                target_hits_pct=(),
            )
        return SplitPolicyResult(
            symbol=path.signal.symbol,
            touch_at=path.signal.touch_at,
            policy_id=spec.policy_id,
            family=spec.family,
            core_fraction=spec.core_fraction,
            runner_fraction=spec.runner_fraction,
            floor_mode=spec.floor_mode,
            exit_reason="data_end",
            exit_at=path.available_until,
            exit_move_pct=terminal_move,
            completed_horizon=False,
            early_activated=early_activated,
            split_activated=False,
            split_activation_at=None,
            core_component_pct=0.0,
            runner_component_pct=0.0,
            runner_exit_move_pct=None,
            runner_base_floor_pct=None,
            max_favorable_pct=max_favorable,
            max_episode_locked_floor_pct=max_episode_locked_floor,
            target_hits_pct=(),
        )

    episode_move, core_component, runner_component = _episode_move(
        spec,
        config,
        terminal_move,
    )
    if path.complete_through >= horizon_at:
        return SplitPolicyResult(
            symbol=path.signal.symbol,
            touch_at=path.signal.touch_at,
            policy_id=spec.policy_id,
            family=spec.family,
            core_fraction=spec.core_fraction,
            runner_fraction=spec.runner_fraction,
            floor_mode=spec.floor_mode,
            exit_reason="horizon",
            exit_at=horizon_at,
            exit_move_pct=episode_move,
            completed_horizon=True,
            early_activated=True,
            split_activated=True,
            split_activation_at=split_activation_at,
            core_component_pct=core_component,
            runner_component_pct=runner_component,
            runner_exit_move_pct=terminal_move,
            runner_base_floor_pct=runner_base_floor,
            max_favorable_pct=max_favorable,
            max_episode_locked_floor_pct=max_episode_locked_floor,
            target_hits_pct=tuple(sorted(target_hits)),
        )
    return SplitPolicyResult(
        symbol=path.signal.symbol,
        touch_at=path.signal.touch_at,
        policy_id=spec.policy_id,
        family=spec.family,
        core_fraction=spec.core_fraction,
        runner_fraction=spec.runner_fraction,
        floor_mode=spec.floor_mode,
        exit_reason="data_end",
        exit_at=path.available_until,
        exit_move_pct=episode_move,
        completed_horizon=False,
        early_activated=True,
        split_activated=True,
        split_activation_at=split_activation_at,
        core_component_pct=core_component,
        runner_component_pct=runner_component,
        runner_exit_move_pct=terminal_move,
        runner_base_floor_pct=runner_base_floor,
        max_favorable_pct=max_favorable,
        max_episode_locked_floor_pct=max_episode_locked_floor,
        target_hits_pct=tuple(sorted(target_hits)),
    )


def _profit_factor(values: list[float]) -> float | None:
    positive = sum(value for value in values if value > 0)
    negative = -sum(value for value in values if value < 0)
    if negative <= 0:
        return None
    return round(positive / negative, 6)


def _scope_results(
    results: tuple[SplitPolicyResult, ...],
    scope: str,
) -> tuple[SplitPolicyResult, ...]:
    if scope == "POOLED_UNI_LINK":
        return results
    return tuple(item for item in results if item.symbol == scope)


def summarise_policy_results(
    results: tuple[SplitPolicyResult, ...],
    *,
    spec: SplitPolicySpec,
    scope: str,
    config: SplitConfig,
    sample_span_days: float,
) -> dict[str, Any]:
    scoped = _scope_results(results, scope)
    decision_grade = tuple(item for item in scoped if item.completed_horizon)
    values = [item.exit_move_pct for item in decision_grade]
    gross_sum = sum(values)
    monthly_equivalent = (
        gross_sum / sample_span_days * 30.0 if sample_span_days > 0 else None
    )
    activated = tuple(item for item in scoped if item.split_activated)
    row: dict[str, Any] = {
        "scope": scope,
        "policy_id": spec.policy_id,
        "family": spec.family,
        "parameters_json": spec.parameters_json,
        "core_fraction": spec.core_fraction,
        "runner_fraction": spec.runner_fraction,
        "floor_mode": spec.floor_mode,
        "giveback_pct": spec.giveback_pct,
        "signals": len(scoped),
        "decision_grade": len(decision_grade),
        "censored": len(scoped) - len(decision_grade),
        "initial_stop_exits": sum(item.exit_reason == "initial_stop" for item in scoped),
        "early_be_exits": sum(item.exit_reason == "early_be" for item in scoped),
        "split_activated": len(activated),
        "core_take_exits": sum(item.exit_reason == "core_take" for item in scoped),
        "runner_stop_exits": sum(item.exit_reason == "runner_stop" for item in scoped),
        "horizon_exits": sum(item.exit_reason == "horizon" for item in scoped),
        "data_end_exits": sum(item.exit_reason == "data_end" for item in scoped),
        "gross_signal_sum_pct": round(gross_sum, 6),
        "gross_mean_trade_pct": round(gross_sum / len(values), 6) if values else None,
        "gross_median_trade_pct": _median(values),
        "profit_factor": _profit_factor(values),
        "positive_trade_count": sum(value > 0 for value in values),
        "negative_trade_count": sum(value < 0 for value in values),
        "scratch_trade_count": sum(abs(value) <= 1e-12 for value in values),
        "core_component_sum_pct": round(
            sum(item.core_component_pct for item in decision_grade),
            6,
        ),
        "runner_component_sum_pct": round(
            sum(item.runner_component_pct for item in decision_grade),
            6,
        ),
        "fixed_notional_30d_equivalent_pct": (
            round(monthly_equivalent, 6) if monthly_equivalent is not None else None
        ),
        "episode_floor_at_split_pct": (
            round(
                spec.core_fraction * config.core_exit_pct
                + spec.runner_fraction
                * (
                    config.early_floor_pct
                    if spec.floor_mode == "be"
                    else -config.initial_stop_pct
                ),
                6,
            )
            if spec.family != "core_only"
            else config.core_exit_pct
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
    return row


def _result_csv_row(result: SplitPolicyResult) -> dict[str, Any]:
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
    control_results: tuple[SplitPolicyResult, ...],
    config: SplitConfig,
) -> dict[str, Any]:
    applicable = (
        config.initial_stop_pct == 1.0
        and config.early_activation_pct == 0.10
        and config.early_floor_pct == 0.0
        and config.split_activation_pct == 1.10
        and config.core_exit_pct == 1.00
        and config.horizon_hours == 72
        and len(control_results) == 227
    )
    if not applicable:
        return {
            "applicable": False,
            "all_match": None,
            "reason": "P47C parameters differ from the frozen UNI/LINK reference.",
        }
    actual = {
        "signals": len(control_results),
        "initial_stop_exits": sum(
            item.exit_reason == "initial_stop" for item in control_results
        ),
        "early_activated": sum(item.early_activated for item in control_results),
        "split_activated": sum(item.split_activated for item in control_results),
        "early_be_exits": sum(item.exit_reason == "early_be" for item in control_results),
        "core_take_exits": sum(item.exit_reason == "core_take" for item in control_results),
    }
    expected = {
        "signals": 227,
        "initial_stop_exits": 16,
        "early_activated": 211,
        "split_activated": 27,
        "early_be_exits": 184,
        "core_take_exits": 27,
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
    config: SplitConfig,
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
        "# P47C Core + Runner Split V1",
        "",
        "Entry V1 and the early protection prefix remain frozen.",
        "",
        "## Frozen prefix",
        "",
        f"- Initial stop: -{config.initial_stop_pct:.2f}%",
        f"- +{config.early_activation_pct:.2f}% -> floor +{config.early_floor_pct:.2f}%",
        (
            f"- +{config.split_activation_pct:.2f}% -> core valued at "
            f"+{config.core_exit_pct:.2f}% and optional runner split"
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
            "| policy | core | runner | floor | gb | episode floor | gross % | "
            "PF | 30d eq % | runner part % | +2 | +3 | +5 | +10 |"
        ),
        "|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in ranked:
        lines.append(
            "| {policy_id} | {core_fraction:.2f} | {runner_fraction:.2f} | "
            "{floor_mode} | {giveback_pct:.2f} | {episode_floor_at_split_pct} | "
            "{gross_signal_sum_pct} | {profit_factor} | "
            "{fixed_notional_30d_equivalent_pct} | {runner_component_sum_pct} | "
            "{hit_2_0_pct_before_exit} | {hit_3_0_pct_before_exit} | "
            "{hit_5_0_pct_before_exit} | {hit_10_0_pct_before_exit} |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Floor semantics",
            "",
            (
                "- `be`: after the core split, the runner may not loosen below raw "
                "Entry (0.00%)."
            ),
            (
                "- `funded`: after the core split, the runner may use the original "
                "-1.00% floor. Realized core profit finances that extra room."
            ),
            (
                "- With core >= 50% and core valued at +1.00%, a funded runner at "
                "-1.00% cannot make the whole split episode negative before costs."
            ),
            "",
            "## Interpretation guardrails",
            "",
            (
                "- Core is conservatively valued at +1.00% even though the split "
                "activates only after +1.10%. This reserves 0.10% execution cushion."
            ),
            (
                "- Gross percentages are directional price moves on equal notional; "
                "fees, slippage, funding, sizing and leverage are not deducted."
            ),
            (
                "- The 30d equivalent normalizes signal returns over calendar span; "
                "it is not an account-equity return promise."
            ),
            (
                "- `data_end` rows are censored and excluded from decision-grade P&L."
            ),
            (
                "- This module tests only the core/runner split after +1.10%; the "
                "Entry and early +0.10% protection are frozen."
            ),
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_research(
    sources: tuple[SignalSource, ...],
    *,
    output_dir: Path,
    config: SplitConfig,
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
    all_results: list[SplitPolicyResult] = []
    policy_rows: list[dict[str, Any]] = []
    policy_progress = ProgressReporter(config.progress_interval_seconds)
    policy_progress.emit(
        "split-grid",
        processed=0,
        total=len(specs),
        force=True,
        detail=f"paths={len(paths)} prefix frozen",
    )
    control_results: tuple[SplitPolicyResult, ...] = ()
    for index, spec in enumerate(specs, start=1):
        results = tuple(simulate_split_policy(path, spec, config) for path in paths)
        if spec.family == "core_only":
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
            "split-grid",
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
        "architecture": "p47c_core_runner_split_v1",
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
            "No re-entry, sizing, leverage or portfolio coupling is modeled.",
            "Core is conservatively valued at +1.00% after +1.10% activation.",
            "BE runner floor never loosens below Entry.",
            "FUNDED runner floor may return to -1.00% after core is realized.",
            "Censored data_end rows are excluded from decision-grade P&L.",
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
    return root / "reports" / "core_runner_split_v1" / f"UNI_LINK_{stamp}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P47C Core + Runner split after frozen +0.10 -> BE -> +1.10 gate"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--uni-p40-dir", type=Path)
    parser.add_argument("--link-p40-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--initial-stop-pct", type=float, default=1.0)
    parser.add_argument("--early-activation-pct", type=float, default=0.10)
    parser.add_argument("--early-floor-pct", type=float, default=0.0)
    parser.add_argument("--split-activation-pct", type=float, default=1.10)
    parser.add_argument("--core-exit-pct", type=float, default=1.00)
    parser.add_argument("--horizon-hours", type=int, default=72)
    parser.add_argument("--day-cache-size", type=int, default=6)
    parser.add_argument("--progress-interval-seconds", type=float, default=25.0)
    parser.add_argument(
        "--core-fractions",
        default=",".join(str(value) for value in DEFAULT_CORE_FRACTIONS),
    )
    parser.add_argument(
        "--mfe-giveback-pct",
        default=",".join(str(value) for value in DEFAULT_MFE_GIVEBACK_PCT),
    )
    parser.add_argument(
        "--target-levels-pct",
        default=",".join(str(value) for value in DEFAULT_TARGET_LEVELS_PCT),
    )
    args = parser.parse_args()

    root = args.root.resolve()
    uni_dir = args.uni_p40_dir or _resolve_latest_uni_p40(root)
    link_dir = args.link_p40_dir or _resolve_link_p40(root)
    output_dir = args.output_dir or _default_output_dir(root)
    config = SplitConfig(
        initial_stop_pct=args.initial_stop_pct,
        early_activation_pct=args.early_activation_pct,
        early_floor_pct=args.early_floor_pct,
        split_activation_pct=args.split_activation_pct,
        core_exit_pct=args.core_exit_pct,
        horizon_hours=args.horizon_hours,
        core_fractions=_parse_float_tuple(args.core_fractions),
        mfe_giveback_pct=_parse_float_tuple(args.mfe_giveback_pct),
        target_levels_pct=_parse_float_tuple(args.target_levels_pct),
        day_cache_size=args.day_cache_size,
        progress_interval_seconds=args.progress_interval_seconds,
    )
    sources = (discover_source(uni_dir), discover_source(link_dir))
    summary = run_research(sources, output_dir=output_dir, config=config)
    print(f"P47C sources: {', '.join(source.symbol for source in sources)}")
    print(f"P47C signals: {summary['signals']}")
    print(f"P47C policies: {summary['policies']}")
    print(f"Prefix cross-check: {summary['prefix_reference_check']}")
    print(f"Report: {output_dir / 'summary.json'}")
    print(f"Readable summary: {output_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
