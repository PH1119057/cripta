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
from typing import Any

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

DEFAULT_RUNNER_TARGETS_R = (2.0, 3.0, 5.0, 10.0)
DEFAULT_RECOVERY_LEVELS_R = (0.0, 0.25, 0.50, 1.00, 2.00, 3.00, 5.00, 10.00)
DEFAULT_ADVERSE_LEVELS_R = (0.25, 0.50, 1.00)


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
            f"[P47A] stage={stage} processed={processed}/{total} "
            f"elapsed={_format_duration(elapsed)} ETA={eta_text}{suffix}",
            flush=True,
        )
        self.last_emit = now


@dataclass(frozen=True, slots=True)
class RetestConfig:
    initial_stop_pct: float = 1.0
    activation_r: float = 1.0
    be_buffer_bps: float = 0.0
    horizon_hours: int = 72
    runner_targets_r: tuple[float, ...] = DEFAULT_RUNNER_TARGETS_R
    recovery_levels_r: tuple[float, ...] = DEFAULT_RECOVERY_LEVELS_R
    adverse_levels_r: tuple[float, ...] = DEFAULT_ADVERSE_LEVELS_R
    day_cache_size: int = 6
    progress_interval_seconds: float = 25.0

    def __post_init__(self) -> None:
        if self.initial_stop_pct <= 0:
            raise ValueError("initial_stop_pct must be positive")
        if self.activation_r <= 0:
            raise ValueError("activation_r must be positive")
        if self.be_buffer_bps < 0:
            raise ValueError("be_buffer_bps cannot be negative")
        if self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be positive")
        if any(value <= self.activation_r for value in self.runner_targets_r):
            raise ValueError("runner targets must be above activation_r")
        if any(value < 0 for value in self.recovery_levels_r):
            raise ValueError("recovery levels cannot be negative")
        if any(value <= 0 for value in self.adverse_levels_r):
            raise ValueError("adverse levels must be positive")
        if self.day_cache_size <= 0:
            raise ValueError("day_cache_size must be positive")
        if self.progress_interval_seconds <= 0:
            raise ValueError("progress_interval_seconds must be positive")
        if self.be_floor_pct >= self.activation_pct:
            raise ValueError("break-even floor must be below activation threshold")

    @property
    def activation_pct(self) -> float:
        return self.initial_stop_pct * self.activation_r

    @property
    def be_floor_pct(self) -> float:
        return self.be_buffer_bps / 100.0


@dataclass(frozen=True, slots=True)
class RunnerRetestRow:
    symbol: str
    direction: str
    touch_at: datetime
    entry_price: float
    target_r: float
    target_pct: float
    activation_at: datetime
    target_at: datetime
    activation_to_target_seconds: float
    retest_floor_pct: float
    retest_floor_r: float
    giveback_from_activation_pct: float
    crossed_entry_before_target: bool
    crossed_be_floor_before_target: bool
    be_exit_before_target: bool
    complete_horizon: bool


@dataclass(frozen=True, slots=True)
class BeEventRow:
    symbol: str
    direction: str
    touch_at: datetime
    entry_price: float
    complete_horizon: bool
    activation_at: datetime
    activation_seconds: float
    be_exit_at: datetime
    activation_to_be_seconds: float
    pre_be_peak_pct: float
    pre_be_peak_r: float
    post_be_max_before_invalidation_pct: float | None
    post_be_min_before_invalidation_pct: float | None
    post_be_max_to_horizon_pct: float | None
    post_be_min_to_horizon_pct: float | None
    invalidation_at: datetime | None
    be_to_invalidation_seconds: float | None
    prior_peak_reclaim_at: datetime | None
    be_to_prior_peak_reclaim_seconds: float | None
    recovery_hit_seconds_json: str
    adverse_hit_seconds_json: str
    missed_runner_targets_r: str


@dataclass(slots=True)
class PathAnatomy:
    symbol: str
    direction: str
    touch_at: datetime
    entry_price: float
    complete_horizon: bool
    activation_index: int | None
    activation_at: datetime | None
    initial_stop_index: int | None
    initial_stop_at: datetime | None
    be_exit_index: int | None
    be_exit_at: datetime | None
    pre_be_peak_pct: float | None
    target_indices: dict[float, int]
    runner_retest_floor_pct: dict[float, float]
    recovery_indices: dict[float, int]
    adverse_indices: dict[float, int]
    prior_peak_reclaim_index: int | None
    post_be_max_before_invalidation_pct: float | None
    post_be_min_before_invalidation_pct: float | None
    post_be_max_to_horizon_pct: float | None
    post_be_min_to_horizon_pct: float | None


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _percent(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100.0, 2)


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


def _distribution(values: list[float]) -> dict[str, float | None]:
    return {
        "p10": _quantile(values, 0.10),
        "p25": _quantile(values, 0.25),
        "median": _median(values),
        "p75": _quantile(values, 0.75),
        "p90": _quantile(values, 0.90),
    }


def _event_at(path: PathSeries, index: int) -> datetime:
    return datetime.fromtimestamp(path.timestamps[index], UTC)


def _seconds_between(path: PathSeries, start_index: int, end_index: int) -> float:
    return path.timestamps[end_index] - path.timestamps[start_index]


def analyze_path(path: PathSeries, config: RetestConfig) -> PathAnatomy:
    horizon_at = path.signal.touch_at + timedelta(hours=config.horizon_hours)
    horizon_ts = horizon_at.timestamp()
    activation_index: int | None = None
    initial_stop_index: int | None = None
    be_exit_index: int | None = None
    pre_be_peak_pct: float | None = None
    target_indices: dict[float, int] = {}
    runner_retest_floor_pct: dict[float, float] = {}
    recovery_indices: dict[float, int] = {}
    adverse_indices: dict[float, int] = {}
    prior_peak_reclaim_index: int | None = None
    running_retest_floor: float | None = None
    post_be_max_before_invalidation_pct: float | None = None
    post_be_min_before_invalidation_pct: float | None = None
    post_be_max_to_horizon_pct: float | None = None
    post_be_min_to_horizon_pct: float | None = None

    for index, (timestamp, move) in enumerate(
        zip(path.timestamps, path.moves_pct, strict=True)
    ):
        if timestamp > horizon_ts:
            break

        if initial_stop_index is None and move <= -config.initial_stop_pct:
            initial_stop_index = index

        if (
            activation_index is None
            and initial_stop_index is None
            and move >= config.activation_pct
        ):
            activation_index = index
            pre_be_peak_pct = move
            running_retest_floor = move

        if activation_index is not None and index >= activation_index:
            if initial_stop_index is None:
                if running_retest_floor is None:
                    running_retest_floor = move
                else:
                    running_retest_floor = min(running_retest_floor, move)
                for target_r in config.runner_targets_r:
                    if target_r in target_indices:
                        continue
                    target_pct = target_r * config.initial_stop_pct
                    if move >= target_pct:
                        target_indices[target_r] = index
                        runner_retest_floor_pct[target_r] = running_retest_floor

            if be_exit_index is None:
                pre_be_peak_pct = (
                    move if pre_be_peak_pct is None else max(pre_be_peak_pct, move)
                )
                if index > activation_index and move <= config.be_floor_pct:
                    be_exit_index = index
                    continue

        if be_exit_index is None or index <= be_exit_index:
            continue

        if post_be_max_to_horizon_pct is None:
            post_be_max_to_horizon_pct = move
            post_be_min_to_horizon_pct = move
        else:
            post_be_max_to_horizon_pct = max(post_be_max_to_horizon_pct, move)
            post_be_min_to_horizon_pct = (
                move
                if post_be_min_to_horizon_pct is None
                else min(post_be_min_to_horizon_pct, move)
            )

        invalidated = initial_stop_index is not None and index >= initial_stop_index
        if not invalidated:
            if post_be_max_before_invalidation_pct is None:
                post_be_max_before_invalidation_pct = move
                post_be_min_before_invalidation_pct = move
            else:
                post_be_max_before_invalidation_pct = max(
                    post_be_max_before_invalidation_pct,
                    move,
                )
                post_be_min_before_invalidation_pct = (
                    move
                    if post_be_min_before_invalidation_pct is None
                    else min(post_be_min_before_invalidation_pct, move)
                )

        for level_r in config.recovery_levels_r:
            if level_r in recovery_indices:
                continue
            if move >= level_r * config.initial_stop_pct:
                recovery_indices[level_r] = index

        for level_r in config.adverse_levels_r:
            if level_r in adverse_indices:
                continue
            if move <= -level_r * config.initial_stop_pct:
                adverse_indices[level_r] = index

        if (
            not invalidated
            and prior_peak_reclaim_index is None
            and pre_be_peak_pct is not None
            and move >= pre_be_peak_pct
        ):
            prior_peak_reclaim_index = index

    return PathAnatomy(
        symbol=path.signal.symbol,
        direction=path.signal.direction,
        touch_at=path.signal.touch_at,
        entry_price=path.signal.entry_price,
        complete_horizon=path.complete_through >= horizon_at,
        activation_index=activation_index,
        activation_at=(
            _event_at(path, activation_index) if activation_index is not None else None
        ),
        initial_stop_index=initial_stop_index,
        initial_stop_at=(
            _event_at(path, initial_stop_index)
            if initial_stop_index is not None
            else None
        ),
        be_exit_index=be_exit_index,
        be_exit_at=_event_at(path, be_exit_index) if be_exit_index is not None else None,
        pre_be_peak_pct=pre_be_peak_pct,
        target_indices=target_indices,
        runner_retest_floor_pct=runner_retest_floor_pct,
        recovery_indices=recovery_indices,
        adverse_indices=adverse_indices,
        prior_peak_reclaim_index=prior_peak_reclaim_index,
        post_be_max_before_invalidation_pct=post_be_max_before_invalidation_pct,
        post_be_min_before_invalidation_pct=post_be_min_before_invalidation_pct,
        post_be_max_to_horizon_pct=post_be_max_to_horizon_pct,
        post_be_min_to_horizon_pct=post_be_min_to_horizon_pct,
    )


def _runner_rows(
    path: PathSeries,
    anatomy: PathAnatomy,
    config: RetestConfig,
) -> list[RunnerRetestRow]:
    if anatomy.activation_index is None or anatomy.activation_at is None:
        return []
    rows: list[RunnerRetestRow] = []
    for target_r, target_index in sorted(anatomy.target_indices.items()):
        floor = anatomy.runner_retest_floor_pct[target_r]
        target_at = _event_at(path, target_index)
        be_exit_before_target = (
            anatomy.be_exit_index is not None and anatomy.be_exit_index < target_index
        )
        rows.append(
            RunnerRetestRow(
                symbol=anatomy.symbol,
                direction=anatomy.direction,
                touch_at=anatomy.touch_at,
                entry_price=anatomy.entry_price,
                target_r=target_r,
                target_pct=target_r * config.initial_stop_pct,
                activation_at=anatomy.activation_at,
                target_at=target_at,
                activation_to_target_seconds=_seconds_between(
                    path,
                    anatomy.activation_index,
                    target_index,
                ),
                retest_floor_pct=floor,
                retest_floor_r=floor / config.initial_stop_pct,
                giveback_from_activation_pct=config.activation_pct - floor,
                crossed_entry_before_target=floor <= 0.0,
                crossed_be_floor_before_target=floor <= config.be_floor_pct,
                be_exit_before_target=be_exit_before_target,
                complete_horizon=anatomy.complete_horizon,
            )
        )
    return rows


def _hit_seconds_json(
    path: PathSeries,
    be_exit_index: int,
    indices: dict[float, int],
) -> str:
    payload = {
        f"{level_r:g}R": round(_seconds_between(path, be_exit_index, index), 6)
        for level_r, index in sorted(indices.items())
    }
    return json.dumps(payload, sort_keys=True)


def _be_event_row(
    path: PathSeries,
    anatomy: PathAnatomy,
    config: RetestConfig,
) -> BeEventRow | None:
    if (
        anatomy.activation_index is None
        or anatomy.activation_at is None
        or anatomy.be_exit_index is None
        or anatomy.be_exit_at is None
        or anatomy.pre_be_peak_pct is None
    ):
        return None
    invalidation_after_be = (
        anatomy.initial_stop_index
        if anatomy.initial_stop_index is not None
        and anatomy.initial_stop_index > anatomy.be_exit_index
        else None
    )
    missed_targets = [
        target_r
        for target_r, target_index in anatomy.target_indices.items()
        if target_index > anatomy.be_exit_index
    ]
    return BeEventRow(
        symbol=anatomy.symbol,
        direction=anatomy.direction,
        touch_at=anatomy.touch_at,
        entry_price=anatomy.entry_price,
        complete_horizon=anatomy.complete_horizon,
        activation_at=anatomy.activation_at,
        activation_seconds=(
            path.timestamps[anatomy.activation_index] - path.signal.touch_at.timestamp()
        ),
        be_exit_at=anatomy.be_exit_at,
        activation_to_be_seconds=_seconds_between(
            path,
            anatomy.activation_index,
            anatomy.be_exit_index,
        ),
        pre_be_peak_pct=anatomy.pre_be_peak_pct,
        pre_be_peak_r=anatomy.pre_be_peak_pct / config.initial_stop_pct,
        post_be_max_before_invalidation_pct=(
            anatomy.post_be_max_before_invalidation_pct
        ),
        post_be_min_before_invalidation_pct=(
            anatomy.post_be_min_before_invalidation_pct
        ),
        post_be_max_to_horizon_pct=anatomy.post_be_max_to_horizon_pct,
        post_be_min_to_horizon_pct=anatomy.post_be_min_to_horizon_pct,
        invalidation_at=(
            _event_at(path, invalidation_after_be)
            if invalidation_after_be is not None
            else None
        ),
        be_to_invalidation_seconds=(
            _seconds_between(path, anatomy.be_exit_index, invalidation_after_be)
            if invalidation_after_be is not None
            else None
        ),
        prior_peak_reclaim_at=(
            _event_at(path, anatomy.prior_peak_reclaim_index)
            if anatomy.prior_peak_reclaim_index is not None
            else None
        ),
        be_to_prior_peak_reclaim_seconds=(
            _seconds_between(
                path,
                anatomy.be_exit_index,
                anatomy.prior_peak_reclaim_index,
            )
            if anatomy.prior_peak_reclaim_index is not None
            else None
        ),
        recovery_hit_seconds_json=_hit_seconds_json(
            path,
            anatomy.be_exit_index,
            anatomy.recovery_indices,
        ),
        adverse_hit_seconds_json=_hit_seconds_json(
            path,
            anatomy.be_exit_index,
            anatomy.adverse_indices,
        ),
        missed_runner_targets_r=";".join(f"{value:g}" for value in missed_targets),
    )


def _scope_filter(rows: list[Any], scope: str) -> list[Any]:
    if scope == "POOLED_UNI_LINK":
        return rows
    return [row for row in rows if row.symbol == scope]


def summarise_runner_retests(
    rows: list[RunnerRetestRow],
    *,
    scopes: tuple[str, ...],
    config: RetestConfig,
) -> list[dict[str, Any]]:
    summary_rows: list[dict[str, Any]] = []
    for scope in scopes:
        scoped = _scope_filter(rows, scope)
        for target_r in config.runner_targets_r:
            target_rows = [row for row in scoped if row.target_r == target_r]
            floors = [row.retest_floor_r for row in target_rows]
            killed = [row for row in target_rows if row.be_exit_before_target]
            summary_rows.append(
                {
                    "scope": scope,
                    "target_r": target_r,
                    "baseline_runner_count": len(target_rows),
                    "be_exit_before_target_count": len(killed),
                    "be_exit_before_target_percent": _percent(
                        len(killed),
                        len(target_rows),
                    ),
                    "runner_preservation_percent": _percent(
                        len(target_rows) - len(killed),
                        len(target_rows),
                    ),
                    "crossed_entry_before_target_count": sum(
                        row.crossed_entry_before_target for row in target_rows
                    ),
                    "crossed_entry_before_target_percent": _percent(
                        sum(row.crossed_entry_before_target for row in target_rows),
                        len(target_rows),
                    ),
                    "crossed_be_floor_before_target_count": sum(
                        row.crossed_be_floor_before_target for row in target_rows
                    ),
                    "crossed_be_floor_before_target_percent": _percent(
                        sum(row.crossed_be_floor_before_target for row in target_rows),
                        len(target_rows),
                    ),
                    "retest_floor_r_p10": _quantile(floors, 0.10),
                    "retest_floor_r_p25": _quantile(floors, 0.25),
                    "retest_floor_r_median": _median(floors),
                    "retest_floor_r_p75": _quantile(floors, 0.75),
                    "retest_floor_r_p90": _quantile(floors, 0.90),
                    "activation_to_target_seconds_median": _median(
                        [row.activation_to_target_seconds for row in target_rows]
                    ),
                }
            )
    return summary_rows


def summarise_be_events(
    event_rows: list[BeEventRow],
    anatomies: list[PathAnatomy],
    *,
    scopes: tuple[str, ...],
    config: RetestConfig,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scope in scopes:
        scoped_events = _scope_filter(event_rows, scope)
        scoped_anatomies = _scope_filter(anatomies, scope)
        activated = [item for item in scoped_anatomies if item.activation_index is not None]
        initial_stop_before_activation = [
            item
            for item in scoped_anatomies
            if item.initial_stop_index is not None and item.activation_index is None
        ]
        row: dict[str, Any] = {
            "scope": scope,
            "signals": len(scoped_anatomies),
            "complete_horizon": sum(item.complete_horizon for item in scoped_anatomies),
            "activated": len(activated),
            "activated_percent": _percent(len(activated), len(scoped_anatomies)),
            "initial_stop_before_activation": len(initial_stop_before_activation),
            "be_exits": len(scoped_events),
            "be_exit_percent_of_activated": _percent(len(scoped_events), len(activated)),
            "activation_to_be_seconds_median": _median(
                [item.activation_to_be_seconds for item in scoped_events]
            ),
            "pre_be_peak_r_median": _median(
                [item.pre_be_peak_r for item in scoped_events]
            ),
            "post_be_invalidation_count": sum(
                item.invalidation_at is not None for item in scoped_events
            ),
            "post_be_invalidation_percent": _percent(
                sum(item.invalidation_at is not None for item in scoped_events),
                len(scoped_events),
            ),
            "prior_peak_reclaim_count": sum(
                item.prior_peak_reclaim_at is not None for item in scoped_events
            ),
            "prior_peak_reclaim_percent": _percent(
                sum(item.prior_peak_reclaim_at is not None for item in scoped_events),
                len(scoped_events),
            ),
            "be_to_prior_peak_reclaim_seconds_median": _median(
                [
                    value
                    for item in scoped_events
                    if (value := item.be_to_prior_peak_reclaim_seconds) is not None
                ]
            ),
        }
        rows.append(row)
    return rows


def _parse_hit_json(raw: str) -> dict[str, float]:
    payload = json.loads(raw)
    return {str(key): float(value) for key, value in payload.items()}


def summarise_recovery_levels(
    event_rows: list[BeEventRow],
    *,
    scopes: tuple[str, ...],
    config: RetestConfig,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scope in scopes:
        scoped = _scope_filter(event_rows, scope)
        for level_r in config.recovery_levels_r:
            key = f"{level_r:g}R"
            hit_seconds: list[float] = []
            hit_before_invalidation = 0
            for item in scoped:
                recovery = _parse_hit_json(item.recovery_hit_seconds_json)
                seconds = recovery.get(key)
                if seconds is None:
                    continue
                hit_seconds.append(seconds)
                if (
                    item.be_to_invalidation_seconds is None
                    or seconds < item.be_to_invalidation_seconds
                ):
                    hit_before_invalidation += 1
            rows.append(
                {
                    "scope": scope,
                    "recovery_level_r": level_r,
                    "be_events": len(scoped),
                    "hit_count": len(hit_seconds),
                    "hit_percent": _percent(len(hit_seconds), len(scoped)),
                    "hit_before_invalidation_count": hit_before_invalidation,
                    "hit_before_invalidation_percent": _percent(
                        hit_before_invalidation,
                        len(scoped),
                    ),
                    "time_from_be_seconds_p25": _quantile(hit_seconds, 0.25),
                    "time_from_be_seconds_median": _median(hit_seconds),
                    "time_from_be_seconds_p75": _quantile(hit_seconds, 0.75),
                    "time_from_be_seconds_p90": _quantile(hit_seconds, 0.90),
                }
            )
    return rows


def summarise_adverse_levels(
    event_rows: list[BeEventRow],
    *,
    scopes: tuple[str, ...],
    config: RetestConfig,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scope in scopes:
        scoped = _scope_filter(event_rows, scope)
        for level_r in config.adverse_levels_r:
            key = f"{level_r:g}R"
            hit_seconds = [
                seconds
                for item in scoped
                if (seconds := _parse_hit_json(item.adverse_hit_seconds_json).get(key))
                is not None
            ]
            rows.append(
                {
                    "scope": scope,
                    "adverse_level_r": -level_r,
                    "be_events": len(scoped),
                    "hit_count": len(hit_seconds),
                    "hit_percent": _percent(len(hit_seconds), len(scoped)),
                    "time_from_be_seconds_p25": _quantile(hit_seconds, 0.25),
                    "time_from_be_seconds_median": _median(hit_seconds),
                    "time_from_be_seconds_p75": _quantile(hit_seconds, 0.75),
                    "time_from_be_seconds_p90": _quantile(hit_seconds, 0.90),
                }
            )
    return rows


def summarise_resolution_matrix(
    event_rows: list[BeEventRow],
    *,
    scopes: tuple[str, ...],
    config: RetestConfig,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    positive_levels = tuple(
        level for level in config.recovery_levels_r if level in {0.25, 0.5, 1.0}
    )
    for scope in scopes:
        scoped = _scope_filter(event_rows, scope)
        for up_r in positive_levels:
            up_key = f"{up_r:g}R"
            for down_r in config.adverse_levels_r:
                down_key = f"{down_r:g}R"
                up_first = 0
                down_first = 0
                neither = 0
                for item in scoped:
                    up = _parse_hit_json(item.recovery_hit_seconds_json).get(up_key)
                    down = _parse_hit_json(item.adverse_hit_seconds_json).get(down_key)
                    if up is None and down is None:
                        neither += 1
                    elif down is None or (up is not None and up <= down):
                        up_first += 1
                    else:
                        down_first += 1
                decisive = up_first + down_first
                rows.append(
                    {
                        "scope": scope,
                        "up_level_r": up_r,
                        "down_level_r": -down_r,
                        "be_events": len(scoped),
                        "up_first": up_first,
                        "down_first": down_first,
                        "neither": neither,
                        "up_first_percent_decisive": _percent(up_first, decisive),
                        "down_first_percent_decisive": _percent(down_first, decisive),
                    }
                )
    return rows



def _p46_reference_check(
    runner_summary: list[dict[str, Any]],
    config: RetestConfig,
) -> dict[str, Any]:
    applicable = (
        config.initial_stop_pct == 1.0
        and config.activation_r == 1.0
        and config.be_buffer_bps == 0.0
        and config.horizon_hours == 72
    )
    if not applicable:
        return {
            "reference": "P46 corrected UNI/LINK replay, +1R -> BE, 0 bps",
            "applicable": False,
            "all_match": None,
            "reason": "P47A parameters differ from the frozen P46 reference scenario.",
        }
    pooled = {
        float(row["target_r"]): row
        for row in runner_summary
        if row["scope"] == "POOLED_UNI_LINK"
    }
    expected_baseline = {2.0: 91, 3.0: 68, 5.0: 33, 10.0: 12}
    expected_killed = {5.0: 7, 10.0: 1}
    baseline_checks = {
        f"target_{target:g}R": {
            "expected": expected,
            "observed": (
                int(pooled[target]["baseline_runner_count"])
                if target in pooled
                else None
            ),
            "matches": (
                target in pooled
                and int(pooled[target]["baseline_runner_count"]) == expected
            ),
        }
        for target, expected in expected_baseline.items()
    }
    killed_checks = {
        f"target_{target:g}R": {
            "expected": expected,
            "observed": (
                int(pooled[target]["be_exit_before_target_count"])
                if target in pooled
                else None
            ),
            "matches": (
                target in pooled
                and int(pooled[target]["be_exit_before_target_count"]) == expected
            ),
        }
        for target, expected in expected_killed.items()
    }
    all_checks = [
        item["matches"]
        for group in (baseline_checks, killed_checks)
        for item in group.values()
    ]
    return {
        "reference": "P46 corrected UNI/LINK replay, +1R -> BE, 0 bps",
        "applicable": True,
        "baseline_runner_counts": baseline_checks,
        "be_killed_runner_counts": killed_checks,
        "all_match": all(all_checks),
    }

def _level_key(value: float) -> str:
    return str(value).replace(".", "_")


def _be_event_csv_row(row: BeEventRow) -> dict[str, Any]:
    payload = asdict(row)
    recovery = _parse_hit_json(row.recovery_hit_seconds_json)
    adverse = _parse_hit_json(row.adverse_hit_seconds_json)
    for key, seconds in recovery.items():
        payload[f"recovery_{_level_key(float(key[:-1]))}R_seconds"] = seconds
    for key, seconds in adverse.items():
        payload[f"adverse_minus_{_level_key(float(key[:-1]))}R_seconds"] = seconds
    return payload


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0])
    known = set(fieldnames)
    for row in rows[1:]:
        for key in row:
            if key not in known:
                fieldnames.append(key)
                known.add(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def _write_summary_md(
    path: Path,
    *,
    config: RetestConfig,
    be_summary: list[dict[str, Any]],
    runner_summary: list[dict[str, Any]],
    recovery_summary: list[dict[str, Any]],
    resolution_summary: list[dict[str, Any]],
    p46_reference_check: dict[str, Any],
) -> None:
    lines = [
        "# P47A Retest Anatomy",
        "",
        "Research-only. Entry V1 is frozen. No re-entry policy is executed or tuned here.",
        "",
        f"Initial stop: **-{config.initial_stop_pct:.3f}% price = -1R**.",
        f"Reference protection: activate at **+{config.activation_r:.2f}R**, then stop at "
        f"**{config.be_floor_pct:.3f}%** relative to Entry.",
        f"Path horizon: **{config.horizon_hours}h**.",
        "",
        "## P46 cross-check",
        "",
        (
            "Reference check: **"
            + (
                "PASS"
                if p46_reference_check.get("all_match") is True
                else "FAIL"
                if p46_reference_check.get("all_match") is False
                else "NOT APPLICABLE"
            )
            + "**."
        ),
        "",
        "## BE-event anatomy",
        "",
        (
            "| Scope | Signals | Activated | BE exits | BE exits / activated | "
            "Later -1R invalidation | Prior peak reclaimed |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in be_summary:
        lines.append(
            "| {scope} | {signals} | {activated} | {be_exits} | {be_pct} | {inv} | {peak} |".format(
                scope=row["scope"],
                signals=row["signals"],
                activated=row["activated"],
                be_exits=row["be_exits"],
                be_pct=row["be_exit_percent_of_activated"],
                inv=row["post_be_invalidation_percent"],
                peak=row["prior_peak_reclaim_percent"],
            )
        )
    lines.extend(
        [
            "",
            "## Future-runner retest floor after first +1R",
            "",
            "The retest floor is measured from the first +1R touch until the first target touch, "
            "conditional on the original -1R stop not invalidating the trade first.",
            "",
            (
                "| Scope | Target | Baseline runners | BE killed before target | "
                "Preservation | Crossed Entry | Median retest floor |"
            ),
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in runner_summary:
        lines.append(
            (
                "| {scope} | +{target:g}R | {count} | {killed} | {pres}% | "
                "{cross}% | {floor}R |"
            ).format(
                scope=row["scope"],
                target=float(row["target_r"]),
                count=row["baseline_runner_count"],
                killed=row["be_exit_before_target_count"],
                pres=row["runner_preservation_percent"],
                cross=row["crossed_entry_before_target_percent"],
                floor=row["retest_floor_r_median"],
            )
        )
    pooled_recovery = [row for row in recovery_summary if row["scope"] == "POOLED_UNI_LINK"]
    lines.extend(
        [
            "",
            "## Recovery after BE exit — pooled",
            "",
            (
                "| Recovery level | Hit before original -1R invalidation | "
                "Median time from BE | P90 time |"
            ),
            "|---|---:|---:|---:|",
        ]
    )
    for row in pooled_recovery:
        lines.append(
            "| +{level:g}R | {pct}% | {median} sec | {p90} sec |".format(
                level=float(row["recovery_level_r"]),
                pct=row["hit_before_invalidation_percent"],
                median=row["time_from_be_seconds_median"],
                p90=row["time_from_be_seconds_p90"],
            )
        )
    pooled_resolution = [
        row for row in resolution_summary if row["scope"] == "POOLED_UNI_LINK"
    ]
    lines.extend(
        [
            "",
            "## First-resolution matrix after BE exit — pooled",
            "",
            "| Recovery | Adverse | Recovery first | Adverse first | Neither |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in pooled_resolution:
        lines.append(
            "| +{up:g}R | {down:g}R | {up_pct}% | {down_pct}% | {neither} |".format(
                up=float(row["up_level_r"]),
                down=float(row["down_level_r"]),
                up_pct=row["up_first_percent_decisive"],
                down_pct=row["down_first_percent_decisive"],
                neither=row["neither"],
            )
        )
    lines.extend(
        [
            "",
            "## Interpretation guardrails",
            "",
            "- P47A is descriptive anatomy, not parameter optimisation.",
            (
                "- A BE exit near zero is not free in live trading; fees, slippage "
                "and funding must be applied later."
            ),
            (
                "- Re-entry is not executed in this module. The report only measures "
                "whether a continuation opportunity existed before the original -1R "
                "invalidation."
            ),
            "- Entry V1 is not re-run and is not changed.",
            (
                "- The remaining assets stay untouched as out-of-sample validation "
                "until Exit candidates are frozen."
            ),
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_research(
    sources: tuple[SignalSource, ...],
    *,
    output_dir: Path,
    config: RetestConfig,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    progress = ProgressReporter(config.progress_interval_seconds)
    signals_by_symbol = {
        source.symbol: tuple(
            sorted(load_core_signals(source), key=lambda item: item.touch_at)
        )
        for source in sources
    }
    total_signals = sum(len(items) for items in signals_by_symbol.values())
    processed = 0
    all_anatomies: list[PathAnatomy] = []
    all_runner_rows: list[RunnerRetestRow] = []
    all_be_rows: list[BeEventRow] = []
    cache_stats: dict[str, dict[str, int]] = {}

    progress.emit(
        "retest-anatomy",
        processed=0,
        total=total_signals,
        force=True,
        detail="building corrected 72h paths and scanning each path once",
    )
    for source in sources:
        signals = signals_by_symbol[source.symbol]
        archive_by_day = _archive_map(source.dataset_dir)
        cache = TradeDayCache(max_days=config.day_cache_size)
        for signal in signals:
            path = build_path_series(
                signal,
                archive_by_day,
                horizon_hours=config.horizon_hours,
                cache=cache,
            )
            anatomy = analyze_path(path, config)
            all_anatomies.append(anatomy)
            all_runner_rows.extend(_runner_rows(path, anatomy, config))
            be_row = _be_event_row(path, anatomy, config)
            if be_row is not None:
                all_be_rows.append(be_row)
            processed += 1
            progress.emit(
                "retest-anatomy",
                processed=processed,
                total=total_signals,
                detail=(
                    f"symbol={source.symbol} cache_hits={cache.hits} "
                    f"cache_misses={cache.misses} be_events={len(all_be_rows)}"
                ),
            )
        cache_stats[source.symbol] = {"hits": cache.hits, "misses": cache.misses}

    scopes = tuple(source.symbol for source in sources) + ("POOLED_UNI_LINK",)
    runner_summary = summarise_runner_retests(
        all_runner_rows,
        scopes=scopes,
        config=config,
    )
    be_summary = summarise_be_events(
        all_be_rows,
        all_anatomies,
        scopes=scopes,
        config=config,
    )
    recovery_summary = summarise_recovery_levels(
        all_be_rows,
        scopes=scopes,
        config=config,
    )
    adverse_summary = summarise_adverse_levels(
        all_be_rows,
        scopes=scopes,
        config=config,
    )
    resolution_summary = summarise_resolution_matrix(
        all_be_rows,
        scopes=scopes,
        config=config,
    )
    p46_reference_check = _p46_reference_check(runner_summary, config)

    _write_csv(
        output_dir / "be_events.csv",
        [_be_event_csv_row(row) for row in all_be_rows],
    )
    _write_csv(
        output_dir / "runner_retest_paths.csv",
        [asdict(row) for row in all_runner_rows],
    )
    _write_csv(output_dir / "be_event_summary.csv", be_summary)
    _write_csv(output_dir / "runner_retest_summary.csv", runner_summary)
    _write_csv(output_dir / "recovery_after_be_summary.csv", recovery_summary)
    _write_csv(output_dir / "adverse_after_be_summary.csv", adverse_summary)
    _write_csv(output_dir / "first_resolution_matrix.csv", resolution_summary)

    summary = {
        "architecture": "p47a_retest_anatomy_v1",
        "research_only": True,
        "entry_frozen": True,
        "reentry_executed": False,
        "config": asdict(config),
        "signals": len(all_anatomies),
        "be_events": len(all_be_rows),
        "runner_retest_rows": len(all_runner_rows),
        "cache_stats": cache_stats,
        "p46_reference_check": p46_reference_check,
        "be_event_summary": be_summary,
        "runner_retest_summary": runner_summary,
        "recovery_after_be_summary": recovery_summary,
        "adverse_after_be_summary": adverse_summary,
        "first_resolution_matrix": resolution_summary,
        "notes": [
            "P47A is descriptive anatomy and performs no parameter optimisation.",
            "The +1R -> BE reference is used only to identify retest/continuation anatomy.",
            (
                "A target is a baseline runner only when it is reached before the "
                "original -1R invalidation."
            ),
            (
                "A missed runner is a baseline target first reached after the BE exit "
                "but before original -1R invalidation."
            ),
            "All path completeness uses the corrected P46 contiguous daily-archive coverage logic.",
        ],
    }
    _write_json(output_dir / "summary.json", summary)
    _write_summary_md(
        output_dir / "summary.md",
        config=config,
        be_summary=be_summary,
        runner_summary=runner_summary,
        recovery_summary=recovery_summary,
        resolution_summary=resolution_summary,
        p46_reference_check=p46_reference_check,
    )
    progress.emit(
        "done",
        processed=1,
        total=1,
        force=True,
        detail=(
            f"output={output_dir} "
            f"p46_crosscheck={p46_reference_check.get('all_match')}"
        ),
    )
    return summary


def _parse_float_tuple(raw: str) -> tuple[float, ...]:
    values = tuple(float(item.strip()) for item in raw.split(",") if item.strip())
    if not values:
        raise ValueError("at least one numeric value is required")
    return values


def _default_output_dir(root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return root / "reports" / "retest_anatomy_v1" / f"UNI_LINK_{stamp}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P47A Retest Anatomy: +1R -> BE continuation diagnostics"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--uni-p40-dir", type=Path)
    parser.add_argument("--link-p40-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--initial-stop-pct", type=float, default=1.0)
    parser.add_argument("--activation-r", type=float, default=1.0)
    parser.add_argument("--be-buffer-bps", type=float, default=0.0)
    parser.add_argument("--horizon-hours", type=int, default=72)
    parser.add_argument("--runner-targets-r", default="2,3,5,10")
    parser.add_argument("--recovery-levels-r", default="0,0.25,0.5,1,2,3,5,10")
    parser.add_argument("--adverse-levels-r", default="0.25,0.5,1")
    parser.add_argument("--day-cache-size", type=int, default=6)
    parser.add_argument("--progress-interval-seconds", type=float, default=25.0)
    args = parser.parse_args()

    root = args.root.resolve()
    uni_dir = args.uni_p40_dir or _resolve_latest_uni_p40(root)
    link_dir = args.link_p40_dir or _resolve_link_p40(root)
    output_dir = args.output_dir or _default_output_dir(root)
    config = RetestConfig(
        initial_stop_pct=args.initial_stop_pct,
        activation_r=args.activation_r,
        be_buffer_bps=args.be_buffer_bps,
        horizon_hours=args.horizon_hours,
        runner_targets_r=_parse_float_tuple(args.runner_targets_r),
        recovery_levels_r=_parse_float_tuple(args.recovery_levels_r),
        adverse_levels_r=_parse_float_tuple(args.adverse_levels_r),
        day_cache_size=args.day_cache_size,
        progress_interval_seconds=args.progress_interval_seconds,
    )
    sources = (discover_source(uni_dir), discover_source(link_dir))
    summary = run_research(sources, output_dir=output_dir, config=config)
    print(f"P47A sources: {', '.join(source.symbol for source in sources)}")
    print(f"P47A signals: {summary['signals']}")
    print(f"P47A BE events: {summary['be_events']}")
    print(f"Report: {output_dir / 'summary.json'}")
    print(f"Readable summary: {output_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
