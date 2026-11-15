from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from bybit_workbench.research.exit_break_even_v13 import CoreSignal, PathSeries, TradeDayCache
from bybit_workbench.research.first_retest_stop_anatomy_p49 import (
    ALL_SYMBOLS,
    EXPECTED_SIGNALS,
    PERIOD_TAG,
    _build_compact_path_series,
    _first_at_or_above,
    _first_at_or_below,
    _parse_csv_floats,
    _pct_key,
    _sha256,
    discover_sources,
    load_all_signals,
)
from bybit_workbench.research.flow_reversal_v1 import _archive_map

P493_VERSION = "P49.3_FULL_FIRST_RETEST_BASIN_3H_V1"
CHECKPOINT_VERSION = "p49.3-full-first-retest-basin-3h-v1"
EXPECTED_PLUS_0P10_COHORT = 995
CHECKPOINT_INTERVAL_SIGNALS = 25
DEFAULT_ACTIVATION_PCT = 0.10
DEFAULT_RETEST_START_DRAWDOWN_PCT = 0.05
DEFAULT_STOP_CANDIDATES_PCT = (-0.75, -0.60, -0.50, -0.35, -0.25, 0.10)
DEFAULT_CONTINUATION_TARGETS_PCT = (0.50, 1.00, 2.00, 3.00)
DEFAULT_THREE_HOUR_DEPTHS_PCT = (-0.25, -0.35, -0.50, -0.60, -0.75, -1.00)
DEFAULT_THREE_HOUR_HOURS = 3.0

BasinStatus = Literal[
    "no_retest",
    "reclaimed_peak1",
    "initial_stop_before_reclaim",
    "unresolved",
]


@dataclass(frozen=True, slots=True)
class P493Config:
    activation_pct: float = DEFAULT_ACTIVATION_PCT
    initial_stop_pct: float = 1.0
    retest_start_drawdown_pct: float = DEFAULT_RETEST_START_DRAWDOWN_PCT
    stop_candidates_pct: tuple[float, ...] = DEFAULT_STOP_CANDIDATES_PCT
    continuation_targets_pct: tuple[float, ...] = DEFAULT_CONTINUATION_TARGETS_PCT
    three_hour_depths_pct: tuple[float, ...] = DEFAULT_THREE_HOUR_DEPTHS_PCT
    three_hour_hours: float = DEFAULT_THREE_HOUR_HOURS
    horizon_hours: int = 72
    day_cache_size: int = 4
    progress_interval_seconds: float = 25.0
    expected_signals: int = EXPECTED_SIGNALS
    expected_cohort: int = EXPECTED_PLUS_0P10_COHORT

    def __post_init__(self) -> None:
        if self.activation_pct <= 0:
            raise ValueError("activation_pct must be positive")
        if self.initial_stop_pct <= 0:
            raise ValueError("initial_stop_pct must be positive")
        if self.retest_start_drawdown_pct <= 0:
            raise ValueError("retest_start_drawdown_pct must be positive")
        if not self.stop_candidates_pct:
            raise ValueError("stop_candidates_pct cannot be empty")
        if any(value <= -self.initial_stop_pct for value in self.stop_candidates_pct):
            raise ValueError("tightened stops must be above the initial stop")
        if self.continuation_targets_pct != DEFAULT_CONTINUATION_TARGETS_PCT:
            raise ValueError("continuation targets are frozen to 0.50,1.00,2.00,3.00")
        if self.three_hour_depths_pct != DEFAULT_THREE_HOUR_DEPTHS_PCT:
            raise ValueError("3h depth thresholds are frozen for P49.3 V1")
        if self.three_hour_hours <= 0:
            raise ValueError("three_hour_hours must be positive")
        if self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be positive")
        if self.day_cache_size <= 0:
            raise ValueError("day_cache_size must be positive")
        if self.progress_interval_seconds <= 0:
            raise ValueError("progress_interval_seconds must be positive")
        if self.expected_signals < 0 or self.expected_cohort < 0:
            raise ValueError("expected counts cannot be negative")


@dataclass(frozen=True, slots=True)
class FullRetestBasinEvent:
    symbol: str
    direction: str
    touch_at: str
    entry_price: float
    activation_pct: float
    activation_at: str
    activation_seconds: float
    peak1_pct: float
    peak1_at: str
    retest_started_at: str | None
    retest_start_move_pct: float | None
    status: BasinStatus
    basin_low_pct: float | None
    basin_low_at: str | None
    basin_depth_from_peak_pct: float | None
    crossed_entry_in_basin: bool | None
    hit_minus_0p25_in_basin: bool | None
    hit_minus_0p35_in_basin: bool | None
    hit_minus_0p50_in_basin: bool | None
    hit_minus_0p60_in_basin: bool | None
    hit_minus_0p75_in_basin: bool | None
    hit_minus_1p00_in_basin: bool | None
    peak1_reclaimed_at: str | None
    activation_to_reclaim_seconds: float | None
    retest_start_to_reclaim_seconds: float | None
    initial_stop_at: str | None
    recovered_entry_after_retest_start_before_minus_1: bool | None
    recovered_plus_0p10_after_retest_start_before_minus_1: bool | None
    recovered_plus_0p50_after_retest_start_before_minus_1: bool | None
    recovered_plus_1p00_after_retest_start_before_minus_1: bool | None
    recovered_plus_2p00_after_retest_start_before_minus_1: bool | None
    recovered_plus_3p00_after_retest_start_before_minus_1: bool | None
    post_reclaim_plus_0p50_before_minus_1: bool | None
    post_reclaim_plus_1p00_before_minus_1: bool | None
    post_reclaim_plus_2p00_before_minus_1: bool | None
    post_reclaim_plus_3p00_before_minus_1: bool | None
    complete_horizon: bool
    missing_archive_days: str


@dataclass(slots=True)
class TradeoffCounts:
    eligible: int = 0
    baseline_runners: int = 0
    baseline_initial_stop_losers: int = 0
    baseline_horizon_nonrunners: int = 0
    baseline_censored: int = 0
    preserved_runners: int = 0
    lost_runners: int = 0
    saved_losers: int = 0
    candidate_stop_exits: int = 0
    immediate_exits: int = 0


class ProgressReporter:
    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = interval_seconds
        self.started = time.monotonic()
        self.last_emit = 0.0

    def emit(
        self,
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
        eta: float | None = None
        if processed > 0 and processed < total:
            eta = elapsed / processed * (total - processed)
        pct = 0.0 if total <= 0 else processed / total * 100.0
        eta_text = "n/a" if eta is None else _format_duration(eta)
        suffix = f" | {detail}" if detail else ""
        print(
            f"[P49.3] processed={processed}/{total} ({pct:.1f}%) "
            f"elapsed={_format_duration(elapsed)} ETA={eta_text}{suffix}",
            flush=True,
        )
        self.last_emit = now


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _event_at(path: PathSeries, index: int | None) -> str | None:
    if index is None:
        return None
    return datetime.fromtimestamp(path.timestamps[index], UTC).isoformat()


def _seconds(path: PathSeries, start: int, end: int) -> float:
    return max(0.0, path.timestamps[end] - path.timestamps[start])


def _seconds_from_touch(path: PathSeries, index: int) -> float:
    return max(0.0, path.timestamps[index] - path.signal.touch_at.timestamp())


def _signed_key(value: float) -> str:
    prefix = "m" if value < 0 else "p"
    return prefix + f"{abs(value):.2f}".replace(".", "p")


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100.0, 2)


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


def _median(values: list[float]) -> float | None:
    return None if not values else round(float(statistics.median(values)), 6)


def _first_at_or_below_limited(
    path: PathSeries,
    threshold: float,
    *,
    start: int,
    end_exclusive: int,
) -> int | None:
    for index in range(start, min(end_exclusive, len(path.moves_pct))):
        if path.moves_pct[index] <= threshold:
            return index
    return None


def _first_at_or_above_limited(
    path: PathSeries,
    threshold: float,
    *,
    start: int,
    end_exclusive: int,
) -> int | None:
    for index in range(start, min(end_exclusive, len(path.moves_pct))):
        if path.moves_pct[index] >= threshold:
            return index
    return None


def _target_before_stop(
    path: PathSeries,
    target_pct: float,
    stop_pct: float,
    *,
    start: int,
    horizon_hours: int = 72,
) -> bool | None:
    target = _first_at_or_above(path, target_pct, start=start)
    stop = _first_at_or_below(path, stop_pct, start=start)
    if target is not None and (stop is None or target <= stop):
        return True
    if stop is not None and (target is None or stop < target):
        return False
    if path.complete_through >= path.signal.touch_at + timedelta(hours=horizon_hours):
        return False
    return None


def _find_activation(path: PathSeries, config: P493Config) -> int:
    activation = _first_at_or_above(path, config.activation_pct)
    stop = _first_at_or_below(path, -config.initial_stop_pct)
    if activation is None or (stop is not None and stop < activation):
        raise ValueError(
            "P49.3 cohort inconsistency: signal no longer reaches +0.10 before -1: "
            f"{path.signal.symbol} {path.signal.touch_at.isoformat()}"
        )
    return activation


def analyze_full_retest_basin(
    path: PathSeries,
    *,
    config: P493Config,
) -> tuple[FullRetestBasinEvent, int | None, int | None]:
    activation = _find_activation(path, config)
    running_peak = path.moves_pct[activation]
    peak_index = activation
    retest_start: int | None = None

    for index in range(activation + 1, len(path.moves_pct)):
        move = path.moves_pct[index]
        if move > running_peak:
            running_peak = move
            peak_index = index
            continue
        if running_peak - move >= config.retest_start_drawdown_pct:
            retest_start = index
            break

    complete_horizon = path.complete_through >= path.signal.touch_at + timedelta(
        hours=config.horizon_hours
    )
    missing = ";".join(path.missing_archive_days)

    if retest_start is None:
        event = FullRetestBasinEvent(
            symbol=path.signal.symbol,
            direction=str(path.signal.direction),
            touch_at=path.signal.touch_at.isoformat(),
            entry_price=path.signal.entry_price,
            activation_pct=config.activation_pct,
            activation_at=_event_at(path, activation) or "",
            activation_seconds=_seconds_from_touch(path, activation),
            peak1_pct=running_peak,
            peak1_at=_event_at(path, peak_index) or "",
            retest_started_at=None,
            retest_start_move_pct=None,
            status="no_retest",
            basin_low_pct=None,
            basin_low_at=None,
            basin_depth_from_peak_pct=None,
            crossed_entry_in_basin=None,
            hit_minus_0p25_in_basin=None,
            hit_minus_0p35_in_basin=None,
            hit_minus_0p50_in_basin=None,
            hit_minus_0p60_in_basin=None,
            hit_minus_0p75_in_basin=None,
            hit_minus_1p00_in_basin=None,
            peak1_reclaimed_at=None,
            activation_to_reclaim_seconds=None,
            retest_start_to_reclaim_seconds=None,
            initial_stop_at=None,
            recovered_entry_after_retest_start_before_minus_1=None,
            recovered_plus_0p10_after_retest_start_before_minus_1=None,
            recovered_plus_0p50_after_retest_start_before_minus_1=None,
            recovered_plus_1p00_after_retest_start_before_minus_1=None,
            recovered_plus_2p00_after_retest_start_before_minus_1=None,
            recovered_plus_3p00_after_retest_start_before_minus_1=None,
            post_reclaim_plus_0p50_before_minus_1=None,
            post_reclaim_plus_1p00_before_minus_1=None,
            post_reclaim_plus_2p00_before_minus_1=None,
            post_reclaim_plus_3p00_before_minus_1=None,
            complete_horizon=complete_horizon,
            missing_archive_days=missing,
        )
        return event, None, None

    low = path.moves_pct[retest_start]
    low_index = retest_start
    reclaim: int | None = None
    initial_stop: int | None = None
    for index in range(retest_start, len(path.moves_pct)):
        move = path.moves_pct[index]
        if move < low:
            low = move
            low_index = index
        if move <= -config.initial_stop_pct:
            initial_stop = index
            break
        if index > retest_start and move >= running_peak:
            reclaim = index
            break

    if reclaim is not None:
        status: BasinStatus = "reclaimed_peak1"
    elif initial_stop is not None:
        status = "initial_stop_before_reclaim"
    else:
        status = "unresolved"

    recovery_levels = (0.0, 0.10, 0.50, 1.00, 2.00, 3.00)
    recovery: dict[float, bool | None] = {
        level: _target_before_stop(
            path,
            level,
            -config.initial_stop_pct,
            start=retest_start,
            horizon_hours=config.horizon_hours,
        )
        for level in recovery_levels
    }
    post_reclaim: dict[float, bool | None] = {}
    for target in config.continuation_targets_pct:
        post_reclaim[target] = (
            None
            if reclaim is None
            else _target_before_stop(
                path,
                target,
                -config.initial_stop_pct,
                start=reclaim,
                horizon_hours=config.horizon_hours,
            )
        )

    event = FullRetestBasinEvent(
        symbol=path.signal.symbol,
        direction=str(path.signal.direction),
        touch_at=path.signal.touch_at.isoformat(),
        entry_price=path.signal.entry_price,
        activation_pct=config.activation_pct,
        activation_at=_event_at(path, activation) or "",
        activation_seconds=_seconds_from_touch(path, activation),
        peak1_pct=running_peak,
        peak1_at=_event_at(path, peak_index) or "",
        retest_started_at=_event_at(path, retest_start),
        retest_start_move_pct=path.moves_pct[retest_start],
        status=status,
        basin_low_pct=low,
        basin_low_at=_event_at(path, low_index),
        basin_depth_from_peak_pct=running_peak - low,
        crossed_entry_in_basin=low <= 0.0,
        hit_minus_0p25_in_basin=low <= -0.25,
        hit_minus_0p35_in_basin=low <= -0.35,
        hit_minus_0p50_in_basin=low <= -0.50,
        hit_minus_0p60_in_basin=low <= -0.60,
        hit_minus_0p75_in_basin=low <= -0.75,
        hit_minus_1p00_in_basin=low <= -1.00,
        peak1_reclaimed_at=_event_at(path, reclaim),
        activation_to_reclaim_seconds=(
            None if reclaim is None else _seconds(path, activation, reclaim)
        ),
        retest_start_to_reclaim_seconds=(
            None if reclaim is None else _seconds(path, retest_start, reclaim)
        ),
        initial_stop_at=_event_at(path, initial_stop),
        recovered_entry_after_retest_start_before_minus_1=recovery[0.0],
        recovered_plus_0p10_after_retest_start_before_minus_1=recovery[0.10],
        recovered_plus_0p50_after_retest_start_before_minus_1=recovery[0.50],
        recovered_plus_1p00_after_retest_start_before_minus_1=recovery[1.00],
        recovered_plus_2p00_after_retest_start_before_minus_1=recovery[2.00],
        recovered_plus_3p00_after_retest_start_before_minus_1=recovery[3.00],
        post_reclaim_plus_0p50_before_minus_1=post_reclaim[0.50],
        post_reclaim_plus_1p00_before_minus_1=post_reclaim[1.00],
        post_reclaim_plus_2p00_before_minus_1=post_reclaim[2.00],
        post_reclaim_plus_3p00_before_minus_1=post_reclaim[3.00],
        complete_horizon=complete_horizon,
        missing_archive_days=missing,
    )
    return event, retest_start, reclaim


def _three_hour_row(path: PathSeries, config: P493Config) -> dict[str, Any]:
    activation = _find_activation(path, config)
    end_time = path.signal.touch_at + timedelta(hours=config.three_hour_hours)
    end_exclusive = bisect.bisect_right(path.timestamps, end_time.timestamp())
    complete_3h = path.complete_through >= end_time
    if end_exclusive <= 0:
        raise ValueError(
            f"P49.3 has no raw trades in first 3h: {path.signal.symbol} {path.signal.touch_at}"
        )

    min_index = min(range(end_exclusive), key=lambda index: path.moves_pct[index])
    max_index = max(range(end_exclusive), key=lambda index: path.moves_pct[index])
    minus1 = _first_at_or_below_limited(
        path,
        -config.initial_stop_pct,
        start=activation,
        end_exclusive=end_exclusive,
    )
    before_stop_end = end_exclusive if minus1 is None else minus1 + 1
    max_before_stop_index = max(
        range(before_stop_end),
        key=lambda index: path.moves_pct[index],
    )

    row: dict[str, Any] = {
        "symbol": path.signal.symbol,
        "direction": str(path.signal.direction),
        "touch_at": path.signal.touch_at.isoformat(),
        "entry_price": path.signal.entry_price,
        "activation_at": _event_at(path, activation),
        "activation_seconds": _seconds_from_touch(path, activation),
        "complete_3h": complete_3h,
        "observed_3h_until": (
            datetime.fromtimestamp(path.timestamps[end_exclusive - 1], UTC).isoformat()
        ),
        "min_3h_pct": path.moves_pct[min_index],
        "min_3h_at": _event_at(path, min_index),
        "max_3h_pct": path.moves_pct[max_index],
        "max_3h_at": _event_at(path, max_index),
        "first_minus_1_within_3h_at": _event_at(path, minus1),
        "max_before_first_minus_1_3h_pct": path.moves_pct[max_before_stop_index],
        "max_before_first_minus_1_3h_at": _event_at(path, max_before_stop_index),
        "min_after_first_minus_1_3h_pct": None,
        "min_after_first_minus_1_3h_at": None,
        "raw_max_after_first_minus_1_3h_pct": None,
        "raw_max_after_first_minus_1_3h_at": None,
    }
    if minus1 is not None:
        after_range = range(minus1, end_exclusive)
        min_after = min(after_range, key=lambda index: path.moves_pct[index])
        max_after = max(after_range, key=lambda index: path.moves_pct[index])
        row["min_after_first_minus_1_3h_pct"] = path.moves_pct[min_after]
        row["min_after_first_minus_1_3h_at"] = _event_at(path, min_after)
        row["raw_max_after_first_minus_1_3h_pct"] = path.moves_pct[max_after]
        row["raw_max_after_first_minus_1_3h_at"] = _event_at(path, max_after)

    for depth in config.three_hour_depths_pct:
        key = _signed_key(depth)
        hit = _first_at_or_below_limited(
            path,
            depth,
            start=activation,
            end_exclusive=end_exclusive,
        )
        row[f"first_{key}_after_activation_within_3h_at"] = _event_at(path, hit)
        row[f"hit_{key}_after_activation_within_3h"] = hit is not None
        if hit is None:
            for target in (0.0, 0.10, 0.50, 1.00, 2.00, 3.00):
                row[f"after_{key}_recover_{_signed_key(target)}_before_minus_1"] = None
            continue
        if depth <= -config.initial_stop_pct:
            for target in (0.0, 0.10, 0.50, 1.00, 2.00, 3.00):
                row[f"after_{key}_recover_{_signed_key(target)}_before_minus_1"] = None
            continue
        for target in (0.0, 0.10, 0.50, 1.00, 2.00, 3.00):
            row[f"after_{key}_recover_{_signed_key(target)}_before_minus_1"] = (
                _target_before_stop(
                    path,
                    target,
                    -config.initial_stop_pct,
                    start=hit,
                    horizon_hours=config.horizon_hours,
                )
            )
    return row


def _first_outcomes_after_index(
    path: PathSeries,
    *,
    start: int,
    stops: tuple[float, ...],
    targets: tuple[float, ...],
) -> tuple[dict[float, int | None], dict[float, int | None]]:
    first_stops: dict[float, int | None] = {stop: None for stop in stops}
    first_targets: dict[float, int | None] = {target: None for target in targets}
    unresolved_stops = len(stops)
    unresolved_targets = len(targets)
    for index in range(start, len(path.moves_pct)):
        move = path.moves_pct[index]
        for stop in stops:
            if first_stops[stop] is None and move <= stop:
                first_stops[stop] = index
                unresolved_stops -= 1
        for target in targets:
            if first_targets[target] is None and move >= target:
                first_targets[target] = index
                unresolved_targets -= 1
        if unresolved_stops == 0 and unresolved_targets == 0:
            break
    return first_stops, first_targets


def _new_tradeoff_counts(
    config: P493Config,
) -> dict[tuple[str, float, float], TradeoffCounts]:
    return {
        (timing, stop, target): TradeoffCounts()
        for timing in ("retest_start", "peak1_reclaim")
        for stop in config.stop_candidates_pct
        for target in config.continuation_targets_pct
    }


def _accumulate_tradeoff(
    counts: dict[tuple[str, float, float], TradeoffCounts],
    path: PathSeries,
    event: FullRetestBasinEvent,
    retest_start: int | None,
    reclaim: int | None,
    config: P493Config,
) -> None:
    for timing, start in (("retest_start", retest_start), ("peak1_reclaim", reclaim)):
        if start is None:
            continue
        all_stops = tuple(
            sorted(set(config.stop_candidates_pct + (-config.initial_stop_pct,)))
        )
        first_stops, first_targets = _first_outcomes_after_index(
            path,
            start=start,
            stops=all_stops,
            targets=config.continuation_targets_pct,
        )
        baseline_stop = first_stops[-config.initial_stop_pct]
        start_move = path.moves_pct[start]
        complete = event.complete_horizon
        for target in config.continuation_targets_pct:
            target_index = first_targets[target]
            if target_index is not None and (
                baseline_stop is None or target_index <= baseline_stop
            ):
                baseline_outcome = "target"
            elif baseline_stop is not None and (
                target_index is None or baseline_stop < target_index
            ):
                baseline_outcome = "initial_stop"
            elif complete:
                baseline_outcome = "horizon_nonrunner"
            else:
                baseline_outcome = "censored"

            for stop in config.stop_candidates_pct:
                item = counts[(timing, stop, target)]
                item.eligible += 1
                item.baseline_runners += int(baseline_outcome == "target")
                item.baseline_initial_stop_losers += int(
                    baseline_outcome == "initial_stop"
                )
                item.baseline_horizon_nonrunners += int(
                    baseline_outcome == "horizon_nonrunner"
                )
                item.baseline_censored += int(baseline_outcome == "censored")

                candidate_stop = first_stops[stop]
                immediate = start_move <= stop
                if immediate:
                    candidate_outcome = "stop"
                elif target_index is not None and (
                    candidate_stop is None or target_index <= candidate_stop
                ):
                    candidate_outcome = "target"
                elif candidate_stop is not None and (
                    target_index is None or candidate_stop < target_index
                ):
                    candidate_outcome = "stop"
                elif complete:
                    candidate_outcome = "horizon_nonrunner"
                else:
                    candidate_outcome = "censored"

                item.immediate_exits += int(immediate)
                item.candidate_stop_exits += int(candidate_outcome == "stop")
                if baseline_outcome == "target":
                    item.preserved_runners += int(candidate_outcome == "target")
                    item.lost_runners += int(candidate_outcome == "stop")
                elif baseline_outcome == "initial_stop":
                    item.saved_losers += int(candidate_outcome == "stop")


def _tradeoff_rows(
    counts: dict[tuple[str, float, float], TradeoffCounts],
    config: P493Config,
    timing: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stop in config.stop_candidates_pct:
        price_risk_saved_per_loser = round(config.initial_stop_pct + stop, 4)
        for target in config.continuation_targets_pct:
            item = counts[(timing, stop, target)]
            rows.append(
                {
                    "timing": timing,
                    "stop_pct": stop,
                    "continuation_target_pct": target,
                    "eligible": item.eligible,
                    "baseline_runners": item.baseline_runners,
                    "baseline_initial_stop_losers": item.baseline_initial_stop_losers,
                    "baseline_horizon_nonrunners": item.baseline_horizon_nonrunners,
                    "baseline_censored": item.baseline_censored,
                    "runner_preserved": item.preserved_runners,
                    "runner_lost": item.lost_runners,
                    "runner_preservation_pct": _pct(
                        item.preserved_runners, item.baseline_runners
                    ),
                    "runner_loss_pct": _pct(item.lost_runners, item.baseline_runners),
                    "saved_losers": item.saved_losers,
                    "saved_loser_pct": _pct(
                        item.saved_losers, item.baseline_initial_stop_losers
                    ),
                    "candidate_stop_exits": item.candidate_stop_exits,
                    "immediate_exits": item.immediate_exits,
                    "theoretical_price_risk_saved_per_saved_loser_pp": (
                        price_risk_saved_per_loser
                    ),
                    "theoretical_total_price_risk_saved_pp": round(
                        item.saved_losers * price_risk_saved_per_loser, 4
                    ),
                    "lost_runner_to_saved_loser_ratio": (
                        None
                        if item.saved_losers == 0
                        else round(item.lost_runners / item.saved_losers, 4)
                    ),
                }
            )
    return rows


def _depth_bucket(low: float | None) -> str:
    if low is None:
        return "no_retest"
    if low > 0.0:
        return ">0.00"
    if low > -0.25:
        return "0.00..-0.25"
    if low > -0.35:
        return "-0.25..-0.35"
    if low > -0.50:
        return "-0.35..-0.50"
    if low > -0.60:
        return "-0.50..-0.60"
    if low > -0.75:
        return "-0.60..-0.75"
    if low > -1.00:
        return "-0.75..-1.00"
    return "<=-1.00"


def build_depth_matrix(events: list[FullRetestBasinEvent]) -> list[dict[str, Any]]:
    order = (
        "no_retest",
        ">0.00",
        "0.00..-0.25",
        "-0.25..-0.35",
        "-0.35..-0.50",
        "-0.50..-0.60",
        "-0.60..-0.75",
        "-0.75..-1.00",
        "<=-1.00",
    )
    rows: list[dict[str, Any]] = []
    for bucket in order:
        subset = [event for event in events if _depth_bucket(event.basin_low_pct) == bucket]
        row: dict[str, Any] = {
            "activation_pct": DEFAULT_ACTIVATION_PCT,
            "full_retest_low_bucket": bucket,
            "signals": len(subset),
            "percent_of_995": _pct(len(subset), len(events)),
            "reclaimed_peak1": sum(event.status == "reclaimed_peak1" for event in subset),
            "failed_initial_stop_before_reclaim": sum(
                event.status == "initial_stop_before_reclaim" for event in subset
            ),
            "unresolved": sum(event.status == "unresolved" for event in subset),
        }
        decisive = sum(event.status != "unresolved" for event in subset)
        row["peak1_reclaim_pct_of_decisive"] = _pct(row["reclaimed_peak1"], decisive)
        for target, field in (
            (0.10, "recovered_plus_0p10_after_retest_start_before_minus_1"),
            (0.50, "recovered_plus_0p50_after_retest_start_before_minus_1"),
            (1.00, "recovered_plus_1p00_after_retest_start_before_minus_1"),
            (2.00, "recovered_plus_2p00_after_retest_start_before_minus_1"),
            (3.00, "recovered_plus_3p00_after_retest_start_before_minus_1"),
        ):
            values = [getattr(event, field) for event in subset]
            true_count = sum(value is True for value in values)
            target_decisive = sum(value is not None for value in values)
            key = _pct_key(target)
            row[f"recover_plus_{key}_before_minus_1"] = true_count
            row[f"recover_plus_{key}_pct"] = _pct(true_count, target_decisive)
        rows.append(row)
    return rows


def build_runner_depth_rows(
    events: list[FullRetestBasinEvent],
    config: P493Config,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    target_fields = {
        0.50: "recovered_plus_0p50_after_retest_start_before_minus_1",
        1.00: "recovered_plus_1p00_after_retest_start_before_minus_1",
        2.00: "recovered_plus_2p00_after_retest_start_before_minus_1",
        3.00: "recovered_plus_3p00_after_retest_start_before_minus_1",
    }
    for target, field in target_fields.items():
        runners = [
            event
            for event in events
            if getattr(event, field) is True and event.basin_low_pct is not None
        ]
        lows = [event.basin_low_pct for event in runners if event.basin_low_pct is not None]
        row: dict[str, Any] = {
            "activation_pct": config.activation_pct,
            "future_target_pct": target,
            "runner_full_retests": len(lows),
            "low_p05": _quantile(lows, 0.05),
            "low_p10": _quantile(lows, 0.10),
            "low_p25": _quantile(lows, 0.25),
            "low_median": _median(lows),
            "low_p75": _quantile(lows, 0.75),
            "low_p90": _quantile(lows, 0.90),
            "low_p95": _quantile(lows, 0.95),
            "low_min": None if not lows else round(min(lows), 6),
        }
        for stop in config.stop_candidates_pct:
            key = _signed_key(stop)
            survived = sum(low > stop for low in lows)
            row[f"basin_low_above_stop_{key}"] = survived
            row[f"basin_low_above_stop_{key}_pct"] = _pct(survived, len(lows))
        rows.append(row)
    return rows


def build_three_hour_depth_summary(
    rows: list[dict[str, Any]],
    config: P493Config,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for depth in config.three_hour_depths_pct:
        key = _signed_key(depth)
        hit_field = f"hit_{key}_after_activation_within_3h"
        hit_rows = [row for row in rows if row[hit_field] is True]
        decisive = [row for row in rows if row["complete_3h"] is True or row[hit_field] is True]
        record: dict[str, Any] = {
            "depth_threshold_pct": depth,
            "cohort": len(rows),
            "complete_3h": sum(row["complete_3h"] is True for row in rows),
            "right_censored_before_3h": sum(row["complete_3h"] is False for row in rows),
            "decisive_for_depth": len(decisive),
            "hit_after_plus_0p10_within_3h": len(hit_rows),
            "hit_pct_of_decisive": _pct(len(hit_rows), len(decisive)),
        }
        if depth <= -config.initial_stop_pct:
            minus_rows = hit_rows
            record["raw_return_entry_after_minus_1_within_3h"] = sum(
                row["raw_max_after_first_minus_1_3h_pct"] is not None
                and row["raw_max_after_first_minus_1_3h_pct"] >= 0.0
                for row in minus_rows
            )
            record["raw_return_plus_0p10_after_minus_1_within_3h"] = sum(
                row["raw_max_after_first_minus_1_3h_pct"] is not None
                and row["raw_max_after_first_minus_1_3h_pct"] >= 0.10
                for row in minus_rows
            )
            output.append(record)
            continue
        for target in (0.0, 0.10, 0.50, 1.00, 2.00, 3.00):
            field = f"after_{key}_recover_{_signed_key(target)}_before_minus_1"
            values = [row[field] for row in hit_rows]
            reached = sum(value is True for value in values)
            target_decisive = sum(value is not None for value in values)
            target_key = _signed_key(target)
            record[f"recover_{target_key}_after_depth_before_minus_1"] = reached
            record[f"recover_{target_key}_pct"] = _pct(reached, target_decisive)
        output.append(record)
    return output


def build_three_hour_minus1_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cases = [row for row in rows if row["first_minus_1_within_3h_at"] is not None]
    adverse = [
        float(row["min_3h_pct"])
        for row in cases
        if row["min_3h_pct"] is not None
    ]
    favourable_before = [
        float(row["max_before_first_minus_1_3h_pct"])
        for row in cases
        if row["max_before_first_minus_1_3h_pct"] is not None
    ]
    return {
        "cases": len(cases),
        "pct_of_cohort": _pct(len(cases), len(rows)),
        "min_3h_adverse_p05": _quantile(adverse, 0.05),
        "min_3h_adverse_p10": _quantile(adverse, 0.10),
        "min_3h_adverse_p25": _quantile(adverse, 0.25),
        "min_3h_adverse_median": _median(adverse),
        "min_3h_adverse_p75": _quantile(adverse, 0.75),
        "min_3h_adverse_p90": _quantile(adverse, 0.90),
        "min_3h_adverse_p95": _quantile(adverse, 0.95),
        "min_3h_adverse_min": None if not adverse else round(min(adverse), 6),
        "max_before_minus_1_p25": _quantile(favourable_before, 0.25),
        "max_before_minus_1_median": _median(favourable_before),
        "max_before_minus_1_p75": _quantile(favourable_before, 0.75),
        "max_before_minus_1_p90": _quantile(favourable_before, 0.90),
        "max_before_minus_1_max": (
            None if not favourable_before else round(max(favourable_before), 6)
        ),
    }


def _read_p49_v12_cohort(
    p49_dir: Path,
    config: P493Config,
) -> tuple[set[tuple[str, str, str]], dict[str, Any]]:
    summary_path = p49_dir / "summary.json"
    events_path = p49_dir / "first_retest_events.csv"
    if not summary_path.is_file() or not events_path.is_file():
        raise FileNotFoundError(
            "Completed P49.2 report is required. Expected summary.json and "
            f"first_retest_events.csv under {p49_dir}"
        )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("research_version") != "P49_FIRST_RETEST_STOP_ANATOMY_V1_2_MEMORY_BOUNDED":
        raise ValueError("P49.3 requires the completed P49.2 MEMORY BOUNDED report")
    if int(summary.get("signals", -1)) != config.expected_signals:
        raise ValueError("P49.2 signal count does not match the frozen 1063 Entry cohort")

    selected: set[tuple[str, str, str]] = set()
    with events_path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if abs(float(row["activation_pct"]) - config.activation_pct) > 1e-9:
                continue
            if row["status"] == "no_activation":
                continue
            selected.add((row["symbol"], row["direction"], row["touch_at"]))
    if len(selected) != config.expected_cohort:
        raise ValueError(
            "P49.3 +0.10-first cohort mismatch: "
            f"expected {config.expected_cohort}, got {len(selected)}"
        )
    provenance = {
        "p49_v12_dir": str(p49_dir.resolve()),
        "p49_v12_summary_sha256": _sha256(summary_path),
        "p49_v12_events_sha256": _sha256(events_path),
    }
    return selected, provenance


def _signal_key(signal: CoreSignal) -> tuple[str, str, str]:
    return signal.symbol, str(signal.direction), signal.touch_at.isoformat()


def _input_fingerprint(
    *,
    p49_provenance: dict[str, Any],
    config: P493Config,
    sources: tuple[Any, ...],
) -> str:
    payload: dict[str, Any] = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "period": PERIOD_TAG,
        "symbols": list(ALL_SYMBOLS),
        "config": asdict(config),
        "p49": p49_provenance,
        "sources": [],
    }
    for source in sources:
        payload["sources"].append(
            {
                "symbol": source.symbol,
                "features_sha256": _sha256(source.features_path),
                "summary_sha256": _sha256(source.summary_path),
                "dataset_manifest_sha256": _sha256(
                    source.dataset_dir / "dataset_manifest.json"
                ),
            }
        )
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _new_counts(config: P493Config) -> dict[tuple[str, float, float], TradeoffCounts]:
    return _new_tradeoff_counts(config)


def _serialize_counts(
    counts: dict[tuple[str, float, float], TradeoffCounts],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (timing, stop, target), item in sorted(counts.items()):
        rows.append(
            {
                "timing": timing,
                "stop": stop,
                "target": target,
                **asdict(item),
            }
        )
    return rows


def _deserialize_counts(
    rows: list[dict[str, Any]],
    config: P493Config,
) -> dict[tuple[str, float, float], TradeoffCounts]:
    counts = _new_counts(config)
    for row in rows:
        key = (str(row["timing"]), float(row["stop"]), float(row["target"]))
        if key not in counts:
            raise ValueError(f"checkpoint tradeoff key incompatible with config: {key}")
        counts[key] = TradeoffCounts(
            eligible=int(row["eligible"]),
            baseline_runners=int(row["baseline_runners"]),
            baseline_initial_stop_losers=int(row["baseline_initial_stop_losers"]),
            baseline_horizon_nonrunners=int(row["baseline_horizon_nonrunners"]),
            baseline_censored=int(row["baseline_censored"]),
            preserved_runners=int(row["preserved_runners"]),
            lost_runners=int(row["lost_runners"]),
            saved_losers=int(row["saved_losers"]),
            candidate_stop_exits=int(row["candidate_stop_exits"]),
            immediate_exits=int(row["immediate_exits"]),
        )
    return counts


def _write_checkpoint(
    path: Path,
    *,
    fingerprint: str,
    processed: int,
    total: int,
    basin_events: list[FullRetestBasinEvent],
    three_hour_rows: list[dict[str, Any]],
    tradeoff_counts: dict[tuple[str, float, float], TradeoffCounts],
) -> None:
    payload = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "input_fingerprint": fingerprint,
        "processed_signals": processed,
        "total_signals": total,
        "basin_events": [asdict(event) for event in basin_events],
        "three_hour_rows": three_hour_rows,
        "tradeoff_counts": _serialize_counts(tradeoff_counts),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_checkpoint(
    path: Path,
    *,
    fingerprint: str,
    total: int,
    config: P493Config,
) -> tuple[
    int,
    list[FullRetestBasinEvent],
    list[dict[str, Any]],
    dict[tuple[str, float, float], TradeoffCounts],
]:
    if not path.exists():
        return 0, [], [], _new_counts(config)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("checkpoint_version") != CHECKPOINT_VERSION:
        raise ValueError("P49.3 checkpoint version mismatch")
    if payload.get("input_fingerprint") != fingerprint:
        raise ValueError(
            "P49.3 checkpoint input fingerprint mismatch; use a different OutputDir"
        )
    processed = int(payload.get("processed_signals", -1))
    if int(payload.get("total_signals", -1)) != total or not 0 <= processed <= total:
        raise ValueError("P49.3 checkpoint signal counts are invalid")
    raw_events = payload.get("basin_events")
    raw_three = payload.get("three_hour_rows")
    raw_counts = payload.get("tradeoff_counts")
    if (
        not isinstance(raw_events, list)
        or not isinstance(raw_three, list)
        or not isinstance(raw_counts, list)
    ):
        raise ValueError("P49.3 checkpoint payload is incomplete")
    if len(raw_events) != processed or len(raw_three) != processed:
        raise ValueError("P49.3 checkpoint row count mismatch")
    events = [FullRetestBasinEvent(**dict(item)) for item in raw_events]
    three_rows = [dict(item) for item in raw_three]
    count_rows = [dict(item) for item in raw_counts]
    return processed, events, three_rows, _deserialize_counts(count_rows, config)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_summary_md(path: Path, summary: dict[str, Any]) -> None:
    minus1 = summary["three_hour_minus1"]
    status = summary["basin_status_counts"]
    lines = [
        "# P49.3 Full First Retest Basin + 3h Risk Anatomy",
        "",
        "Research only. Downloads: DISABLED.",
        "Entry V1, frozen P46, live Execution, Exit and Risk production logic are unchanged.",
        "",
        "## Fixed cohort",
        "",
        (
            f"Exactly **{summary['cohort']}** old Entry V1 signals that reached +0.10% "
            "before the original -1.00% stop. The 66 early failures are excluded from this cycle."
        ),
        "",
        "## Full first retest definition",
        "",
        (
            "After +0.10 activation, Peak #1 keeps extending until the first drawdown of at least "
            f"{summary['config']['retest_start_drawdown_pct']:.2f} percentage points."
        ),
        (
            "The full first-retest basin then remains open through all micro-bounces. "
            "It ends only when price causally reclaims the frozen Peak #1, hits the "
            "original -1.00% stop, or is "
            "right-censored by the dataset boundary."
        ),
        "",
        "## Coverage snapshot",
        "",
        f"- Reclaimed Peak #1: **{status.get('reclaimed_peak1', 0)}**",
        f"- Hit -1 before reclaim: **{status.get('initial_stop_before_reclaim', 0)}**",
        f"- No qualifying retest: **{status.get('no_retest', 0)}**",
        f"- Right-censored/unresolved basin: **{status.get('unresolved', 0)}**",
        (
            f"- Reached -1 within first 3h after Entry: **{minus1['cases']}** "
            f"({minus1['pct_of_cohort']}% of cohort)"
        ),
        "",
        "## Main outputs",
        "",
        "- `full_retest_basin_events.csv`: one row per one of the 995 +0.10-first signals.",
        "- `full_retest_depth_matrix.csv`: retest depth x recovery to +0.10/+0.50/+1/+2/+3.",
        "- `runner_full_retest_depth.csv`: depth distribution for future +0.5/+1/+2/+3 runners.",
        (
            "- `three_hour_paths_995.csv`: exact 3h min/max and threshold hits "
            "for every cohort signal."
        ),
        (
            "- `three_hour_minus1_cases.csv`: only +0.10-first signals that still "
            "reached -1 within 3h."
        ),
        (
            "- `three_hour_depth_recovery.csv`: after -0.25/-0.35/-0.50/-0.60/"
            "-0.75, how many recovered before -1."
        ),
        (
            "- `retest_start_stop_tradeoff.csv`: saved losers versus lost future "
            "runners if stop tightens when the retest starts."
        ),
        (
            "- `post_reclaim_stop_tradeoff.csv`: same accounting if stop tightens "
            "only after Peak #1 reclaim."
        ),
        "",
        (
            "No production rule is selected by this script. The five reserved new "
            "assets remain untouched for later OOS confirmation."
        ),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_research(
    root: Path,
    p49_v12_dir: Path,
    output_dir: Path,
    config: P493Config,
) -> dict[str, Any]:
    selected_keys, p49_provenance = _read_p49_v12_cohort(p49_v12_dir, config)
    sources = discover_sources(root)
    all_signals = load_all_signals(sources)
    if config.expected_signals and len(all_signals) != config.expected_signals:
        raise ValueError(
            "Entry V1 signal count mismatch: "
            f"expected {config.expected_signals}, got {len(all_signals)}"
        )
    selected_signals = [signal for signal in all_signals if _signal_key(signal) in selected_keys]
    if len(selected_signals) != config.expected_cohort:
        raise ValueError(
            "P49.3 signal mapping mismatch: "
            f"expected {config.expected_cohort}, got {len(selected_signals)}"
        )
    if {_signal_key(signal) for signal in selected_signals} != selected_keys:
        raise ValueError("P49.3 selected cohort does not map exactly onto current P40 signals")

    source_by_symbol = {source.symbol: source for source in sources}
    archive_by_symbol = {
        symbol: _archive_map(source_by_symbol[symbol].dataset_dir) for symbol in ALL_SYMBOLS
    }
    signals_by_symbol: dict[str, list[CoreSignal]] = {symbol: [] for symbol in ALL_SYMBOLS}
    for signal in selected_signals:
        signals_by_symbol[signal.symbol].append(signal)

    output_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = _input_fingerprint(
        p49_provenance=p49_provenance,
        config=config,
        sources=sources,
    )
    checkpoint_path = output_dir / "checkpoint.json"
    processed, basin_events, three_rows, tradeoff_counts = _load_checkpoint(
        checkpoint_path,
        fingerprint=fingerprint,
        total=len(selected_signals),
        config=config,
    )

    reporter = ProgressReporter(config.progress_interval_seconds)
    reporter.emit(
        processed=processed,
        total=len(selected_signals),
        force=True,
        detail=("resume from checkpoint" if processed else "full basin + 3h raw path anatomy"),
    )

    ordinal = 0
    for symbol in ALL_SYMBOLS:
        symbol_signals = sorted(signals_by_symbol[symbol], key=lambda item: item.touch_at)
        pending: list[CoreSignal] = []
        for signal in symbol_signals:
            ordinal += 1
            if ordinal > processed:
                pending.append(signal)
        if not pending:
            continue
        cache = TradeDayCache(max_days=config.day_cache_size)
        reporter.emit(
            processed=processed,
            total=len(selected_signals),
            force=True,
            detail=(
                f"symbol={symbol} start pending={len(pending)} "
                f"total_symbol={len(symbol_signals)}"
            ),
        )
        for signal in pending:
            path = _build_compact_path_series(
                signal,
                archive_by_symbol[symbol],
                horizon_hours=config.horizon_hours,
                cache=cache,
            )
            basin_event, retest_start, reclaim = analyze_full_retest_basin(path, config=config)
            three_row = _three_hour_row(path, config)
            basin_events.append(basin_event)
            three_rows.append(three_row)
            _accumulate_tradeoff(
                tradeoff_counts,
                path,
                basin_event,
                retest_start,
                reclaim,
                config,
            )
            processed += 1
            reporter.emit(
                processed=processed,
                total=len(selected_signals),
                detail=f"symbol={symbol} cache_hits={cache.hits} cache_misses={cache.misses}",
            )
            del path
            if processed % CHECKPOINT_INTERVAL_SIGNALS == 0:
                _write_checkpoint(
                    checkpoint_path,
                    fingerprint=fingerprint,
                    processed=processed,
                    total=len(selected_signals),
                    basin_events=basin_events,
                    three_hour_rows=three_rows,
                    tradeoff_counts=tradeoff_counts,
                )
        _write_checkpoint(
            checkpoint_path,
            fingerprint=fingerprint,
            processed=processed,
            total=len(selected_signals),
            basin_events=basin_events,
            three_hour_rows=three_rows,
            tradeoff_counts=tradeoff_counts,
        )
        reporter.emit(
            processed=processed,
            total=len(selected_signals),
            force=True,
            detail=(
                f"symbol={symbol} complete cache_hits={cache.hits} "
                f"cache_misses={cache.misses} checkpoint=saved"
            ),
        )
        del cache

    if processed != len(selected_signals):
        raise RuntimeError(
            f"P49.3 processed signal mismatch: {processed} != {len(selected_signals)}"
        )

    depth_matrix = build_depth_matrix(basin_events)
    runner_depth = build_runner_depth_rows(basin_events, config)
    three_depth = build_three_hour_depth_summary(three_rows, config)
    three_minus1 = build_three_hour_minus1_summary(three_rows)
    retest_start_rows = _tradeoff_rows(tradeoff_counts, config, "retest_start")
    reclaim_rows = _tradeoff_rows(tradeoff_counts, config, "peak1_reclaim")
    minus1_cases = [row for row in three_rows if row["first_minus_1_within_3h_at"] is not None]

    status_counts: dict[str, int] = {}
    for event in basin_events:
        status_counts[event.status] = status_counts.get(event.status, 0) + 1

    source_provenance: list[dict[str, str]] = []
    for source in sources:
        source_provenance.append(
            {
                "symbol": source.symbol,
                "p40_dir": str(source.p40_dir),
                "features_sha256": _sha256(source.features_path),
                "summary_sha256": _sha256(source.summary_path),
                "dataset_dir": str(source.dataset_dir),
                "dataset_manifest_sha256": _sha256(source.dataset_dir / "dataset_manifest.json"),
            }
        )

    summary: dict[str, Any] = {
        "research_version": P493_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "period": PERIOD_TAG,
        "symbols": list(ALL_SYMBOLS),
        "entry_signals_total": len(all_signals),
        "cohort_definition": "+0.10% before original -1.00% initial stop",
        "cohort": len(selected_signals),
        "excluded_early_failures_or_no_activation": len(all_signals) - len(selected_signals),
        "basin_status_counts": status_counts,
        "three_hour_minus1": three_minus1,
        "three_hour_complete": sum(row["complete_3h"] is True for row in three_rows),
        "three_hour_right_censored": sum(row["complete_3h"] is False for row in three_rows),
        "config": asdict(config),
        "downloads": "DISABLED",
        "memory_mode": "one_symbol_lru_plus_one_compact_72h_signal_path",
        "p49_v12_provenance": p49_provenance,
        "source_provenance": source_provenance,
        "entry_v1_changed": False,
        "p46_changed": False,
        "exit_risk_production_changed": False,
        "reserved_five_oos_assets_touched": False,
    }

    _write_csv(
        output_dir / "full_retest_basin_events.csv",
        [asdict(event) for event in basin_events],
    )
    _write_csv(output_dir / "full_retest_depth_matrix.csv", depth_matrix)
    _write_csv(output_dir / "runner_full_retest_depth.csv", runner_depth)
    _write_csv(output_dir / "three_hour_paths_995.csv", three_rows)
    _write_csv(output_dir / "three_hour_minus1_cases.csv", minus1_cases)
    _write_csv(output_dir / "three_hour_depth_recovery.csv", three_depth)
    _write_csv(output_dir / "retest_start_stop_tradeoff.csv", retest_start_rows)
    _write_csv(output_dir / "post_reclaim_stop_tradeoff.csv", reclaim_rows)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_summary_md(output_dir / "summary.md", summary)
    _write_checkpoint(
        checkpoint_path,
        fingerprint=fingerprint,
        processed=processed,
        total=len(selected_signals),
        basin_events=basin_events,
        three_hour_rows=three_rows,
        tradeoff_counts=tradeoff_counts,
    )
    reporter.emit(processed=processed, total=len(selected_signals), force=True, detail="done")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="P49.3 full first retest basin + 3h risk anatomy")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--p49-v12-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--activation-pct", type=float, default=DEFAULT_ACTIVATION_PCT)
    parser.add_argument("--initial-stop-pct", type=float, default=1.0)
    parser.add_argument(
        "--retest-start-drawdown-pct",
        type=float,
        default=DEFAULT_RETEST_START_DRAWDOWN_PCT,
    )
    parser.add_argument(
        "--stop-candidates-pct",
        default="-0.75,-0.60,-0.50,-0.35,-0.25,0.10",
    )
    parser.add_argument("--continuation-targets-pct", default="0.50,1.00,2.00,3.00")
    parser.add_argument(
        "--three-hour-depths-pct",
        default="-0.25,-0.35,-0.50,-0.60,-0.75,-1.00",
    )
    parser.add_argument("--three-hour-hours", type=float, default=DEFAULT_THREE_HOUR_HOURS)
    parser.add_argument("--horizon-hours", type=int, default=72)
    parser.add_argument("--day-cache-size", type=int, default=4)
    parser.add_argument("--progress-interval-seconds", type=float, default=25.0)
    parser.add_argument("--expected-signals", type=int, default=EXPECTED_SIGNALS)
    parser.add_argument("--expected-cohort", type=int, default=EXPECTED_PLUS_0P10_COHORT)
    args = parser.parse_args()

    root = args.root.resolve()
    p49_dir = args.p49_v12_dir
    if p49_dir is None:
        p49_dir = root / "reports" / "first_retest_stop_anatomy_p49" / "ALL9_P49_WORKING"
    output_dir = args.output_dir
    if output_dir is None:
        output_dir = root / "reports" / "full_first_retest_basin_p493" / "ALL9_P493_WORKING"

    config = P493Config(
        activation_pct=args.activation_pct,
        initial_stop_pct=args.initial_stop_pct,
        retest_start_drawdown_pct=args.retest_start_drawdown_pct,
        stop_candidates_pct=_parse_csv_floats(args.stop_candidates_pct),
        continuation_targets_pct=_parse_csv_floats(args.continuation_targets_pct),
        three_hour_depths_pct=_parse_csv_floats(args.three_hour_depths_pct),
        three_hour_hours=args.three_hour_hours,
        horizon_hours=args.horizon_hours,
        day_cache_size=args.day_cache_size,
        progress_interval_seconds=args.progress_interval_seconds,
        expected_signals=args.expected_signals,
        expected_cohort=args.expected_cohort,
    )
    summary = run_research(root, p49_dir.resolve(), output_dir.resolve(), config)
    print(f"P49.3 total Entry signals: {summary['entry_signals_total']}")
    print(f"P49.3 +0.10-first cohort: {summary['cohort']}")
    print(f"P49.3 basin status: {summary['basin_status_counts']}")
    print(f"P49.3 -1 within 3h: {summary['three_hour_minus1']}")
    print(f"Report: {output_dir.resolve() / 'summary.json'}")
    print(f"Readable summary: {output_dir.resolve() / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
