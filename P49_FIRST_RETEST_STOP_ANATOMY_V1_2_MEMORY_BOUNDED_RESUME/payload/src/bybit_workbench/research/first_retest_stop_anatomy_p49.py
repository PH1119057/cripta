from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import statistics
import time
from array import array
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from bybit_workbench.research.exit_break_even_v13 import (
    CoreSignal,
    PathSeries,
    SignalSource,
    TradeDayCache,
    discover_source,
    load_core_signals,
)
from bybit_workbench.research.flow_reversal_v1 import _archive_map

PERIOD_TAG = "20260518_20260816"
ALL_SYMBOLS = (
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
EXPECTED_SIGNALS = 1063
CHECKPOINT_VERSION = "p49.2-memory-bounded-v1"
CHECKPOINT_INTERVAL_SIGNALS = 25
DEFAULT_ACTIVATION_LEVELS_PCT = (0.10, 0.20, 0.25, 0.50)
DEFAULT_STOP_CANDIDATES_PCT = (-0.75, -0.50, -0.25, 0.10)
DEFAULT_CONTINUATION_TARGETS_PCT = (0.50, 1.00, 2.00, 3.00)
RetestStatus = Literal[
    "no_activation",
    "no_retest",
    "retest_confirmed",
    "initial_stop_during_retest",
    "retest_unresolved",
]


@dataclass(frozen=True, slots=True)
class P49Config:
    initial_stop_pct: float = 1.0
    activation_levels_pct: tuple[float, ...] = DEFAULT_ACTIVATION_LEVELS_PCT
    retest_start_drawdown_pct: float = 0.05
    rebound_confirm_pct: float = 0.05
    stop_candidates_pct: tuple[float, ...] = DEFAULT_STOP_CANDIDATES_PCT
    continuation_targets_pct: tuple[float, ...] = DEFAULT_CONTINUATION_TARGETS_PCT
    horizon_hours: int = 72
    day_cache_size: int = 4
    progress_interval_seconds: float = 25.0
    expected_signals: int = EXPECTED_SIGNALS

    def __post_init__(self) -> None:
        if self.initial_stop_pct <= 0:
            raise ValueError("initial_stop_pct must be positive")
        if not self.activation_levels_pct or any(
            value <= 0 for value in self.activation_levels_pct
        ):
            raise ValueError("activation_levels_pct must contain positive values")
        if self.retest_start_drawdown_pct <= 0:
            raise ValueError("retest_start_drawdown_pct must be positive")
        if self.rebound_confirm_pct <= 0:
            raise ValueError("rebound_confirm_pct must be positive")
        if not self.stop_candidates_pct:
            raise ValueError("stop_candidates_pct cannot be empty")
        if any(value <= -self.initial_stop_pct for value in self.stop_candidates_pct):
            raise ValueError("tightened stop candidates must be above the initial stop")
        if self.continuation_targets_pct != DEFAULT_CONTINUATION_TARGETS_PCT:
            raise ValueError(
                "P49 V1 continuation_targets_pct is frozen to 0.50,1.00,2.00,3.00"
            )
        if self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be positive")
        if self.day_cache_size <= 0:
            raise ValueError("day_cache_size must be positive")
        if self.progress_interval_seconds <= 0:
            raise ValueError("progress_interval_seconds must be positive")
        if self.expected_signals < 0:
            raise ValueError("expected_signals cannot be negative")


@dataclass(slots=True)
class PolicyCounts:
    eligible: int = 0
    baseline_runners: int = 0
    preserved: int = 0
    killed: int = 0
    immediate: int = 0
    tightened_stops: int = 0
    censored: int = 0


@dataclass(frozen=True, slots=True)
class FirstRetestEvent:
    symbol: str
    direction: str
    touch_at: str
    entry_price: float
    activation_pct: float
    status: RetestStatus
    activation_at: str | None
    activation_seconds: float | None
    initial_stop_before_activation: bool
    peak1_pct: float | None
    peak1_at: str | None
    retest_started_at: str | None
    retest_low_pct: float | None
    retest_low_at: str | None
    retest_depth_from_peak_pct: float | None
    crossed_entry_on_retest: bool | None
    hit_minus_0p25_on_retest: bool | None
    hit_minus_0p50_on_retest: bool | None
    hit_minus_0p75_on_retest: bool | None
    hit_minus_1p00_on_retest: bool | None
    retest_confirmed_at: str | None
    activation_to_retest_confirm_seconds: float | None
    confirmation_move_pct: float | None
    peak1_reclaimed_at: str | None
    peak1_reclaimed_before_minus_1: bool | None
    first_minus_1_after_confirm_at: str | None
    max_after_confirm_pct: float | None
    min_after_confirm_pct: float | None
    baseline_target_0p50_before_minus_1: bool | None
    baseline_target_1p00_before_minus_1: bool | None
    baseline_target_2p00_before_minus_1: bool | None
    baseline_target_3p00_before_minus_1: bool | None
    post_confirm_target_0p50_before_minus_1: bool | None
    post_confirm_target_1p00_before_minus_1: bool | None
    post_confirm_target_2p00_before_minus_1: bool | None
    post_confirm_target_3p00_before_minus_1: bool | None
    complete_horizon: bool
    missing_archive_days: str


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
        eta_text = "n/a" if eta is None else _format_duration(eta)
        suffix = f" | {detail}" if detail else ""
        pct = 0.0 if total <= 0 else processed / total * 100.0
        print(
            f"[P49] processed={processed}/{total} ({pct:.1f}%) "
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


def _parse_csv_floats(raw: str) -> tuple[float, ...]:
    values = tuple(float(item.strip()) for item in raw.split(",") if item.strip())
    if not values:
        raise ValueError("at least one numeric value is required")
    return values


def _event_at(path: PathSeries, index: int | None) -> str | None:
    if index is None:
        return None
    return datetime.fromtimestamp(path.timestamps[index], UTC).isoformat()


def _seconds(path: PathSeries, start_index: int, end_index: int) -> float:
    return max(0.0, path.timestamps[end_index] - path.timestamps[start_index])


def _seconds_from_touch(path: PathSeries, index: int) -> float:
    return max(0.0, path.timestamps[index] - path.signal.touch_at.timestamp())


def _pct_key(value: float) -> str:
    return f"{value:.2f}".replace(".", "p")


def _first_at_or_below(
    path: PathSeries,
    threshold: float,
    *,
    start: int = 0,
) -> int | None:
    for index in range(start, len(path.moves_pct)):
        if path.moves_pct[index] <= threshold:
            return index
    return None


def _first_at_or_above(
    path: PathSeries,
    threshold: float,
    *,
    start: int = 0,
) -> int | None:
    for index in range(start, len(path.moves_pct)):
        if path.moves_pct[index] >= threshold:
            return index
    return None


def _first_target_before_stop(
    path: PathSeries,
    target_pct: float,
    initial_stop_pct: float,
    *,
    start: int = 0,
    horizon_hours: int = 72,
) -> bool | None:
    target = _first_at_or_above(path, target_pct, start=start)
    stop = _first_at_or_below(path, -initial_stop_pct, start=start)
    if target is not None and (stop is None or target <= stop):
        return True
    if stop is not None and (target is None or stop < target):
        return False
    required_until = path.signal.touch_at + timedelta(hours=horizon_hours)
    if path.complete_through >= required_until:
        return False
    return None


def _target_columns(prefix: str, values: dict[float, bool | None]) -> dict[str, bool | None]:
    return {
        f"{prefix}_{_pct_key(target)}_before_minus_1": values.get(target)
        for target in DEFAULT_CONTINUATION_TARGETS_PCT
    }


def analyze_first_retest(
    path: PathSeries,
    *,
    activation_pct: float,
    config: P49Config,
) -> FirstRetestEvent:
    complete_horizon = path.complete_through >= path.signal.touch_at + timedelta(
        hours=config.horizon_hours
    )
    missing = ";".join(path.missing_archive_days)
    baseline_targets = {
        target: _first_target_before_stop(
            path, target, config.initial_stop_pct, horizon_hours=config.horizon_hours
        )
        for target in config.continuation_targets_pct
    }
    baseline_cols = _target_columns("baseline_target", baseline_targets)
    empty_post = _target_columns(
        "post_confirm_target",
        {target: None for target in config.continuation_targets_pct},
    )

    stop_index = _first_at_or_below(path, -config.initial_stop_pct)
    activation_index = _first_at_or_above(path, activation_pct)
    if activation_index is None or (stop_index is not None and stop_index < activation_index):
        return FirstRetestEvent(
            symbol=path.signal.symbol,
            direction=str(path.signal.direction),
            touch_at=path.signal.touch_at.isoformat(),
            entry_price=path.signal.entry_price,
            activation_pct=activation_pct,
            status="no_activation",
            activation_at=None,
            activation_seconds=None,
            initial_stop_before_activation=stop_index is not None,
            peak1_pct=None,
            peak1_at=None,
            retest_started_at=None,
            retest_low_pct=None,
            retest_low_at=None,
            retest_depth_from_peak_pct=None,
            crossed_entry_on_retest=None,
            hit_minus_0p25_on_retest=None,
            hit_minus_0p50_on_retest=None,
            hit_minus_0p75_on_retest=None,
            hit_minus_1p00_on_retest=None,
            retest_confirmed_at=None,
            activation_to_retest_confirm_seconds=None,
            confirmation_move_pct=None,
            peak1_reclaimed_at=None,
            peak1_reclaimed_before_minus_1=None,
            first_minus_1_after_confirm_at=None,
            max_after_confirm_pct=None,
            min_after_confirm_pct=None,
            complete_horizon=complete_horizon,
            missing_archive_days=missing,
            **baseline_cols,
            **empty_post,
        )

    running_peak_pct = path.moves_pct[activation_index]
    peak_index = activation_index
    retest_start_index: int | None = None
    retest_low_pct: float | None = None
    retest_low_index: int | None = None
    confirm_index: int | None = None
    retest_stop_index: int | None = None

    for index in range(activation_index + 1, len(path.moves_pct)):
        move = path.moves_pct[index]
        if retest_start_index is None:
            if move > running_peak_pct:
                running_peak_pct = move
                peak_index = index
                continue
            if running_peak_pct - move >= config.retest_start_drawdown_pct:
                retest_start_index = index
                retest_low_pct = move
                retest_low_index = index
                if move <= -config.initial_stop_pct:
                    retest_stop_index = index
                    break
            continue

        if retest_low_pct is None or retest_low_index is None:
            raise RuntimeError("retest low state is inconsistent")
        if move < retest_low_pct:
            retest_low_pct = move
            retest_low_index = index
        if move <= -config.initial_stop_pct:
            retest_stop_index = index
            break
        if move - retest_low_pct >= config.rebound_confirm_pct:
            confirm_index = index
            break

    if retest_start_index is None:
        return FirstRetestEvent(
            symbol=path.signal.symbol,
            direction=str(path.signal.direction),
            touch_at=path.signal.touch_at.isoformat(),
            entry_price=path.signal.entry_price,
            activation_pct=activation_pct,
            status="no_retest",
            activation_at=_event_at(path, activation_index),
            activation_seconds=_seconds_from_touch(path, activation_index),
            initial_stop_before_activation=False,
            peak1_pct=running_peak_pct,
            peak1_at=_event_at(path, peak_index),
            retest_started_at=None,
            retest_low_pct=None,
            retest_low_at=None,
            retest_depth_from_peak_pct=None,
            crossed_entry_on_retest=None,
            hit_minus_0p25_on_retest=None,
            hit_minus_0p50_on_retest=None,
            hit_minus_0p75_on_retest=None,
            hit_minus_1p00_on_retest=None,
            retest_confirmed_at=None,
            activation_to_retest_confirm_seconds=None,
            confirmation_move_pct=None,
            peak1_reclaimed_at=None,
            peak1_reclaimed_before_minus_1=None,
            first_minus_1_after_confirm_at=None,
            max_after_confirm_pct=None,
            min_after_confirm_pct=None,
            complete_horizon=complete_horizon,
            missing_archive_days=missing,
            **baseline_cols,
            **empty_post,
        )

    if retest_low_pct is None or retest_low_index is None:
        raise RuntimeError("retest started without a retest low")

    threshold_flags = {
        "crossed_entry_on_retest": retest_low_pct <= 0.0,
        "hit_minus_0p25_on_retest": retest_low_pct <= -0.25,
        "hit_minus_0p50_on_retest": retest_low_pct <= -0.50,
        "hit_minus_0p75_on_retest": retest_low_pct <= -0.75,
        "hit_minus_1p00_on_retest": retest_low_pct <= -1.00,
    }

    status: RetestStatus
    confirm_at: str | None
    confirm_seconds: float | None
    confirmation_move: float | None
    reclaim_at: str | None
    reclaim_before_stop: bool | None
    stop_after_confirm_at: str | None
    max_after_confirm: float | None
    min_after_confirm: float | None
    post_cols: dict[str, bool | None]

    if retest_stop_index is not None:
        status = "initial_stop_during_retest"
        confirm_at = None
        confirm_seconds = None
        confirmation_move = None
        reclaim_at = None
        reclaim_before_stop = False
        stop_after_confirm_at = None
        max_after_confirm = None
        min_after_confirm = None
        post_cols = empty_post
    elif confirm_index is None:
        status = "retest_unresolved"
        confirm_at = None
        confirm_seconds = None
        confirmation_move = None
        reclaim_at = None
        reclaim_before_stop = None
        stop_after_confirm_at = None
        max_after_confirm = None
        min_after_confirm = None
        post_cols = empty_post
    else:
        status = "retest_confirmed"
        confirm_at = _event_at(path, confirm_index)
        confirm_seconds = _seconds(path, activation_index, confirm_index)
        confirmation_move = path.moves_pct[confirm_index]
        reclaim_index = _first_at_or_above(path, running_peak_pct, start=confirm_index + 1)
        stop_after_confirm_index = _first_at_or_below(
            path,
            -config.initial_stop_pct,
            start=confirm_index + 1,
        )
        reclaim_at = _event_at(path, reclaim_index)
        reclaim_before_stop = reclaim_index is not None and (
            stop_after_confirm_index is None or reclaim_index <= stop_after_confirm_index
        )
        stop_after_confirm_at = _event_at(path, stop_after_confirm_index)
        max_value: float | None = None
        min_value: float | None = None
        for index in range(confirm_index, len(path.moves_pct)):
            move = path.moves_pct[index]
            if max_value is None or move > max_value:
                max_value = move
            if min_value is None or move < min_value:
                min_value = move
        max_after_confirm = max_value
        min_after_confirm = min_value
        post_targets = {
            target: _first_target_before_stop(
                path,
                target,
                config.initial_stop_pct,
                start=confirm_index,
                horizon_hours=config.horizon_hours,
            )
            for target in config.continuation_targets_pct
        }
        post_cols = _target_columns("post_confirm_target", post_targets)

    return FirstRetestEvent(
        symbol=path.signal.symbol,
        direction=str(path.signal.direction),
        touch_at=path.signal.touch_at.isoformat(),
        entry_price=path.signal.entry_price,
        activation_pct=activation_pct,
        status=status,
        activation_at=_event_at(path, activation_index),
        activation_seconds=_seconds_from_touch(path, activation_index),
        initial_stop_before_activation=False,
        peak1_pct=running_peak_pct,
        peak1_at=_event_at(path, peak_index),
        retest_started_at=_event_at(path, retest_start_index),
        retest_low_pct=retest_low_pct,
        retest_low_at=_event_at(path, retest_low_index),
        retest_depth_from_peak_pct=running_peak_pct - retest_low_pct,
        retest_confirmed_at=confirm_at,
        activation_to_retest_confirm_seconds=confirm_seconds,
        confirmation_move_pct=confirmation_move,
        peak1_reclaimed_at=reclaim_at,
        peak1_reclaimed_before_minus_1=reclaim_before_stop,
        first_minus_1_after_confirm_at=stop_after_confirm_at,
        max_after_confirm_pct=max_after_confirm,
        min_after_confirm_pct=min_after_confirm,
        complete_horizon=complete_horizon,
        missing_archive_days=missing,
        **threshold_flags,
        **baseline_cols,
        **post_cols,
    )


def discover_sources(root: Path) -> tuple[SignalSource, ...]:
    sources: list[SignalSource] = []
    for symbol in ALL_SYMBOLS:
        p40_dir = (
            root
            / "reports"
            / "cross_asset_validation"
            / f"{symbol}_{PERIOD_TAG}"
            / "p40"
        )
        source = discover_source(p40_dir)
        if source.symbol != symbol:
            raise ValueError(f"P40 symbol mismatch: expected {symbol}, got {source.symbol}")
        sources.append(source)
    return tuple(sources)


def load_all_signals(sources: tuple[SignalSource, ...]) -> tuple[CoreSignal, ...]:
    signals: list[CoreSignal] = []
    for source in sources:
        signals.extend(load_core_signals(source))
    return tuple(sorted(signals, key=lambda item: (item.touch_at, item.symbol, item.direction)))


def _dates_for_horizon_p49(start: datetime, hours: int) -> tuple[str, ...]:
    end = start + timedelta(hours=hours)
    current = start.date()
    last = (end - timedelta(microseconds=1)).date()
    days: list[str] = []
    while current <= last:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return tuple(days)


def _day_start_p49(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=UTC)


def _build_compact_path_series(
    signal: CoreSignal,
    archive_by_day: dict[str, Path],
    *,
    horizon_hours: int,
    cache: TradeDayCache,
) -> PathSeries:
    """Build one 72h signal path using packed doubles, not Python float objects.

    P49 only needs one signal path at a time.  The common historical path builder
    materializes Python lists/tuples, which is convenient for smaller studies but
    can multiply memory use on high-volume BTC/ETH public-trade tapes.
    """

    required_days = _dates_for_horizon_p49(signal.touch_at, horizon_hours)
    missing_days = tuple(day for day in required_days if day not in archive_by_day)
    continuous_days: list[str] = []
    for day in required_days:
        if day not in archive_by_day:
            break
        continuous_days.append(day)

    requested_end = signal.touch_at + timedelta(hours=horizon_hours)
    if not continuous_days:
        return PathSeries(
            signal=signal,
            timestamps=(),
            moves_pct=(),
            available_until=signal.touch_at,
            coverage_until=signal.touch_at,
            missing_archive_days=missing_days,
        )

    if len(continuous_days) == len(required_days):
        coverage_until = requested_end
    else:
        coverage_until = min(
            requested_end,
            _day_start_p49(required_days[len(continuous_days)]),
        )

    start_ts = signal.touch_at.timestamp()
    end_ts = requested_end.timestamp()
    timestamps = array("d")
    moves = array("d")
    for day in continuous_days:
        tape = cache.get(archive_by_day[day])
        start_index = bisect.bisect_left(tape.timestamps, start_ts)
        end_index = bisect.bisect_right(tape.timestamps, end_ts)
        for index in range(start_index, end_index):
            price = tape.prices[index]
            raw_move = (price - signal.entry_price) / signal.entry_price * 100.0
            move = raw_move if signal.direction == "Long" else -raw_move
            timestamps.append(tape.timestamps[index])
            moves.append(move)

    if not timestamps:
        return PathSeries(
            signal=signal,
            timestamps=(),
            moves_pct=(),
            available_until=signal.touch_at,
            coverage_until=coverage_until,
            missing_archive_days=missing_days,
        )

    available_until = datetime.fromtimestamp(timestamps[-1], UTC)
    return PathSeries(
        signal=signal,
        timestamps=cast(tuple[float, ...], timestamps),
        moves_pct=cast(tuple[float, ...], moves),
        available_until=available_until,
        coverage_until=coverage_until,
        missing_archive_days=missing_days,
    )


def _depth_bucket(low: float | None) -> str:
    if low is None:
        return "no_retest_low"
    if low > 0.0:
        return ">0.00"
    if low > -0.25:
        return "0.00..-0.25"
    if low > -0.50:
        return "-0.25..-0.50"
    if low > -0.75:
        return "-0.50..-0.75"
    if low > -1.00:
        return "-0.75..-1.00"
    return "<=-1.00"


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


def _depth_distribution(events: list[FirstRetestEvent]) -> dict[str, float | int | None]:
    lows = [event.retest_low_pct for event in events if event.retest_low_pct is not None]
    return {
        "n": len(lows),
        "p05": _quantile(lows, 0.05),
        "p10": _quantile(lows, 0.10),
        "p25": _quantile(lows, 0.25),
        "median": _median(lows),
        "p75": _quantile(lows, 0.75),
        "p90": _quantile(lows, 0.90),
        "p95": _quantile(lows, 0.95),
        "min": None if not lows else round(min(lows), 6),
        "max": None if not lows else round(max(lows), 6),
    }


def build_depth_bucket_rows(events: tuple[FirstRetestEvent, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    bucket_order = (
        ">0.00",
        "0.00..-0.25",
        "-0.25..-0.50",
        "-0.50..-0.75",
        "-0.75..-1.00",
        "<=-1.00",
    )
    for activation in sorted({event.activation_pct for event in events}):
        activated = [
            event
            for event in events
            if event.activation_pct == activation and event.status != "no_activation"
        ]
        for bucket in bucket_order:
            subset = [event for event in activated if _depth_bucket(event.retest_low_pct) == bucket]
            confirmed = [event for event in subset if event.status == "retest_confirmed"]
            row: dict[str, Any] = {
                "activation_pct": activation,
                "retest_low_bucket": bucket,
                "signals": len(subset),
                "percent_of_activated": _pct(len(subset), len(activated)),
                "retest_confirmed": len(confirmed),
                "peak1_reclaimed_before_minus_1": sum(
                    event.peak1_reclaimed_before_minus_1 is True for event in confirmed
                ),
            }
            for target in (0.50, 1.00, 2.00, 3.00):
                field = f"baseline_target_{_pct_key(target)}_before_minus_1"
                reached = sum(getattr(event, field) is True for event in subset)
                decisive = sum(getattr(event, field) is not None for event in subset)
                key = _pct_key(target)
                row[f"baseline_plus_{key}_before_minus_1"] = reached
                row[f"baseline_plus_{key}_pct"] = _pct(reached, decisive)
            rows.append(row)
    return rows


def build_runner_depth_rows(
    events: tuple[FirstRetestEvent, ...],
    config: P49Config,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for activation in sorted({event.activation_pct for event in events}):
        subset_activation = [event for event in events if event.activation_pct == activation]
        for target in (0.50, 1.00, 2.00, 3.00):
            field = f"baseline_target_{_pct_key(target)}_before_minus_1"
            runners = [
                event
                for event in subset_activation
                if getattr(event, field) is True and event.retest_low_pct is not None
            ]
            row: dict[str, Any] = {
                "activation_pct": activation,
                "future_target_pct": target,
                "runner_retests": len(runners),
            }
            row.update(
                {
                    f"retest_low_{key}": value
                    for key, value in _depth_distribution(runners).items()
                }
            )
            for stop in config.stop_candidates_pct:
                key = _signed_key(stop)
                survived = sum(
                    event.retest_low_pct is not None and event.retest_low_pct > stop
                    for event in runners
                )
                row[f"survive_stop_{key}"] = survived
                row[f"survive_stop_{key}_pct"] = _pct(survived, len(runners))
            rows.append(row)
    return rows


def _signed_key(value: float) -> str:
    prefix = "m" if value < 0 else "p"
    return prefix + f"{abs(value):.2f}".replace(".", "p")


def _simulate_stop_after_confirm(
    path: PathSeries,
    event: FirstRetestEvent,
    stop_pct: float,
    target_pct: float,
) -> tuple[str, bool | None]:
    if event.status != "retest_confirmed" or event.retest_confirmed_at is None:
        return "not_eligible", None
    confirm_at = datetime.fromisoformat(event.retest_confirmed_at).astimezone(UTC)
    confirm_ts = confirm_at.timestamp()
    start = 0
    while start < len(path.timestamps) and path.timestamps[start] < confirm_ts:
        start += 1
    if start >= len(path.moves_pct):
        return "data_end", None
    if path.moves_pct[start] <= stop_pct:
        return "immediate_exit", False
    target_index = _first_at_or_above(path, target_pct, start=start)
    stop_index = _first_at_or_below(path, stop_pct, start=start)
    if target_index is not None and (stop_index is None or target_index <= stop_index):
        return "target", True
    if stop_index is not None and (target_index is None or stop_index < target_index):
        return "stop", False
    if event.complete_horizon:
        return "horizon", False
    return "data_end", None


def _new_policy_counts(config: P49Config) -> dict[tuple[float, float, float], PolicyCounts]:
    return {
        (activation, stop, target): PolicyCounts()
        for activation in config.activation_levels_pct
        for stop in config.stop_candidates_pct
        for target in config.continuation_targets_pct
    }


def _policy_outcomes_after_confirm(
    path: PathSeries,
    event: FirstRetestEvent,
    config: P49Config,
) -> dict[tuple[float, float], tuple[str, bool | None]]:
    if event.status != "retest_confirmed" or event.retest_confirmed_at is None:
        return {}

    confirm_at = datetime.fromisoformat(event.retest_confirmed_at).astimezone(UTC)
    start = bisect.bisect_left(path.timestamps, confirm_at.timestamp())
    if start >= len(path.moves_pct):
        return {
            (stop, target): ("data_end", None)
            for stop in config.stop_candidates_pct
            for target in config.continuation_targets_pct
        }

    start_move = path.moves_pct[start]
    first_target: dict[float, int | None] = {
        target: None for target in config.continuation_targets_pct
    }
    first_stop: dict[float, int | None] = {stop: None for stop in config.stop_candidates_pct}
    unresolved_targets = len(first_target)
    unresolved_stops = len(first_stop)

    for index in range(start, len(path.moves_pct)):
        move = path.moves_pct[index]
        for target in config.continuation_targets_pct:
            if first_target[target] is None and move >= target:
                first_target[target] = index
                unresolved_targets -= 1
        for stop in config.stop_candidates_pct:
            if first_stop[stop] is None and move <= stop:
                first_stop[stop] = index
                unresolved_stops -= 1
        if unresolved_targets == 0 and unresolved_stops == 0:
            break

    outcomes: dict[tuple[float, float], tuple[str, bool | None]] = {}
    for stop in config.stop_candidates_pct:
        for target in config.continuation_targets_pct:
            if start_move <= stop:
                outcomes[(stop, target)] = ("immediate_exit", False)
                continue
            target_index = first_target[target]
            stop_index = first_stop[stop]
            if target_index is not None and (
                stop_index is None or target_index <= stop_index
            ):
                outcomes[(stop, target)] = ("target", True)
            elif stop_index is not None and (
                target_index is None or stop_index < target_index
            ):
                outcomes[(stop, target)] = ("stop", False)
            elif event.complete_horizon:
                outcomes[(stop, target)] = ("horizon", False)
            else:
                outcomes[(stop, target)] = ("data_end", None)
    return outcomes


def _accumulate_policy_counts(
    counts: dict[tuple[float, float, float], PolicyCounts],
    path: PathSeries,
    events: tuple[FirstRetestEvent, ...],
    config: P49Config,
) -> None:
    for event in events:
        if event.status != "retest_confirmed":
            continue
        outcomes = _policy_outcomes_after_confirm(path, event, config)
        for stop in config.stop_candidates_pct:
            for target in config.continuation_targets_pct:
                item = counts[(event.activation_pct, stop, target)]
                outcome, target_preserved = outcomes[(stop, target)]
                baseline_field = f"post_confirm_target_{_pct_key(target)}_before_minus_1"
                baseline_runner = getattr(event, baseline_field) is True
                item.eligible += 1
                item.baseline_runners += int(baseline_runner)
                item.immediate += int(outcome == "immediate_exit")
                item.tightened_stops += int(outcome in {"immediate_exit", "stop"})
                item.censored += int(outcome == "data_end")
                if baseline_runner:
                    item.preserved += int(target_preserved is True)
                    item.killed += int(target_preserved is False)


def _policy_rows_from_counts(
    counts: dict[tuple[float, float, float], PolicyCounts],
    config: P49Config,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for activation in config.activation_levels_pct:
        for stop in config.stop_candidates_pct:
            for target in config.continuation_targets_pct:
                item = counts[(activation, stop, target)]
                rows.append(
                    {
                        "activation_pct": activation,
                        "stop_after_retest_confirm_pct": stop,
                        "continuation_target_pct": target,
                        "eligible_confirmed_retests": item.eligible,
                        "baseline_runners": item.baseline_runners,
                        "runner_preserved": item.preserved,
                        "runner_killed": item.killed,
                        "runner_preservation_pct": _pct(
                            item.preserved, item.baseline_runners
                        ),
                        "runner_kill_pct": _pct(item.killed, item.baseline_runners),
                        "immediate_exit_at_confirmation": item.immediate,
                        "tightened_stop_exits": item.tightened_stops,
                        "censored": item.censored,
                    }
                )
    return rows


def build_post_retest_policy_rows(
    paths: dict[tuple[str, str], PathSeries],
    events: tuple[FirstRetestEvent, ...],
    config: P49Config,
) -> list[dict[str, Any]]:
    counts = _new_policy_counts(config)
    events_by_key: dict[tuple[str, str], list[FirstRetestEvent]] = {}
    for event in events:
        events_by_key.setdefault((event.symbol, event.touch_at), []).append(event)
    for key, signal_events in events_by_key.items():
        _accumulate_policy_counts(counts, paths[key], tuple(signal_events), config)
    return _policy_rows_from_counts(counts, config)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _research_input_fingerprint(
    sources: tuple[SignalSource, ...],
    config: P49Config,
) -> str:
    payload: dict[str, Any] = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "period": PERIOD_TAG,
        "symbols": list(ALL_SYMBOLS),
        "config": asdict(config),
        "sources": [],
    }
    source_rows: list[dict[str, str]] = []
    for source in sources:
        manifest_path = source.dataset_dir / "dataset_manifest.json"
        source_rows.append(
            {
                "symbol": source.symbol,
                "features_sha256": _sha256(source.features_path),
                "summary_sha256": _sha256(source.summary_path),
                "dataset_manifest_sha256": _sha256(manifest_path),
            }
        )
    payload["sources"] = source_rows
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _serialize_policy_counts(
    counts: dict[tuple[float, float, float], PolicyCounts],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for (activation, stop, target), item in sorted(counts.items()):
        rows.append(
            {
                "activation_pct": activation,
                "stop_pct": stop,
                "target_pct": target,
                "eligible": item.eligible,
                "baseline_runners": item.baseline_runners,
                "preserved": item.preserved,
                "killed": item.killed,
                "immediate": item.immediate,
                "tightened_stops": item.tightened_stops,
                "censored": item.censored,
            }
        )
    return rows


def _deserialize_policy_counts(
    rows: list[dict[str, Any]],
    config: P49Config,
) -> dict[tuple[float, float, float], PolicyCounts]:
    counts = _new_policy_counts(config)
    for row in rows:
        key = (
            float(row["activation_pct"]),
            float(row["stop_pct"]),
            float(row["target_pct"]),
        )
        if key not in counts:
            raise ValueError(f"checkpoint policy key is incompatible with config: {key}")
        counts[key] = PolicyCounts(
            eligible=int(row["eligible"]),
            baseline_runners=int(row["baseline_runners"]),
            preserved=int(row["preserved"]),
            killed=int(row["killed"]),
            immediate=int(row["immediate"]),
            tightened_stops=int(row["tightened_stops"]),
            censored=int(row["censored"]),
        )
    return counts


def _write_checkpoint(
    path: Path,
    *,
    input_fingerprint: str,
    processed: int,
    total: int,
    events: list[FirstRetestEvent],
    policy_counts: dict[tuple[float, float, float], PolicyCounts],
) -> None:
    payload = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "input_fingerprint": input_fingerprint,
        "processed_signals": processed,
        "total_signals": total,
        "event_rows": len(events),
        "events": [asdict(event) for event in events],
        "policy_counts": _serialize_policy_counts(policy_counts),
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
    input_fingerprint: str,
    total: int,
    config: P49Config,
) -> tuple[int, list[FirstRetestEvent], dict[tuple[float, float, float], PolicyCounts]]:
    if not path.exists():
        return 0, [], _new_policy_counts(config)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("checkpoint_version") != CHECKPOINT_VERSION:
        raise ValueError(
            f"checkpoint version mismatch: {payload.get('checkpoint_version')}"
        )
    if payload.get("input_fingerprint") != input_fingerprint:
        raise ValueError(
            "P49 checkpoint input fingerprint mismatch; use a different OutputDir "
            "instead of mixing incompatible research inputs."
        )
    processed = int(payload.get("processed_signals", -1))
    checkpoint_total = int(payload.get("total_signals", -1))
    if checkpoint_total != total or not 0 <= processed <= total:
        raise ValueError("P49 checkpoint signal counts are invalid")
    raw_events = payload.get("events")
    raw_policy = payload.get("policy_counts")
    if not isinstance(raw_events, list) or not isinstance(raw_policy, list):
        raise ValueError("P49 checkpoint payload is incomplete")
    expected_event_rows = processed * len(config.activation_levels_pct)
    if len(raw_events) != expected_event_rows:
        raise ValueError(
            "P49 checkpoint event count mismatch: "
            f"expected {expected_event_rows}, got {len(raw_events)}"
        )
    events = [FirstRetestEvent(**dict(item)) for item in raw_events]
    policy_rows = [dict(item) for item in raw_policy]
    return processed, events, _deserialize_policy_counts(policy_rows, config)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_summary_md(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# P49 First Retest / Stop Tightening Anatomy",
        "",
        "Research only. Entry V1 and frozen P46 are unchanged.",
        "Downloads: DISABLED. Local raw public trades are the only path source.",
        "",
        "## Fixed terminology",
        "",
        (
            "A first retest exists only after price first reaches a positive activation "
            "milestone. Pre-activation adverse movement is initial Entry noise, not a retest."
        ),
        (
            "After activation, Peak #1 is the running favourable peak until price draws down "
            f"by at least {summary['config']['retest_start_drawdown_pct']:.3f} percentage points."
        ),
        (
            "The first-retetst low is the deepest direction-normalized move after that pullback "
            "starts. The retest is causally confirmed only after price rebounds from that low by "
            f"{summary['config']['rebound_confirm_pct']:.3f} percentage points."
        ),
        "",
        "## Coverage",
        "",
        f"- Core Entry signals: **{summary['signals']}**",
        f"- Signal/activation rows: **{summary['event_rows']}**",
        f"- Period: **{PERIOD_TAG}**",
        f"- Horizon: **{summary['config']['horizon_hours']}h**",
        "",
        "## Outputs",
        "",
        "- `first_retest_events.csv`: one row per Entry x activation milestone.",
        "- `depth_buckets.csv`: continuation outcomes by first-retest depth bucket.",
        "- `runner_retest_depth.csv`: retest-depth quantiles for future +0.5/+1/+2/+3 runners.",
        "- `post_retest_stop_policy.csv`: causal stop tightening only after retest confirmation.",
        "- `summary.json`: machine-readable provenance and counts.",
        (
            "- `checkpoint.json`: resumable bounded-memory checkpoint for this exact "
            "input fingerprint."
        ),
        "",
        (
            "Do not promote any row to production directly. The five new assets remain "
            "reserved for OOS confirmation."
        ),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_research(root: Path, output_dir: Path, config: P49Config) -> dict[str, Any]:
    sources = discover_sources(root)
    signals = load_all_signals(sources)
    if config.expected_signals and len(signals) != config.expected_signals:
        raise ValueError(
            "Entry V1 signal count mismatch: "
            f"expected {config.expected_signals}, got {len(signals)}"
        )

    source_by_symbol = {source.symbol: source for source in sources}
    archive_by_symbol = {
        symbol: _archive_map(source_by_symbol[symbol].dataset_dir) for symbol in ALL_SYMBOLS
    }
    signals_by_symbol: dict[str, list[CoreSignal]] = {symbol: [] for symbol in ALL_SYMBOLS}
    for signal in signals:
        signals_by_symbol[signal.symbol].append(signal)

    output_dir.mkdir(parents=True, exist_ok=True)
    total_work = len(signals)
    input_fingerprint = _research_input_fingerprint(sources, config)
    checkpoint_path = output_dir / "checkpoint.json"
    processed, events, policy_counts = _load_checkpoint(
        checkpoint_path,
        input_fingerprint=input_fingerprint,
        total=total_work,
        config=config,
    )

    reporter = ProgressReporter(config.progress_interval_seconds)
    reporter.emit(
        processed=processed,
        total=total_work,
        force=True,
        detail=(
            "resume from checkpoint"
            if processed
            else "memory-bounded raw-trade path anatomy"
        ),
    )

    signal_ordinal = 0
    for symbol in ALL_SYMBOLS:
        symbol_signals = sorted(signals_by_symbol[symbol], key=lambda item: item.touch_at)
        pending_signals: list[CoreSignal] = []
        for signal in symbol_signals:
            signal_ordinal += 1
            if signal_ordinal > processed:
                pending_signals.append(signal)
        if not pending_signals:
            continue

        cache = TradeDayCache(max_days=config.day_cache_size)
        reporter.emit(
            processed=processed,
            total=total_work,
            force=True,
            detail=(
                f"symbol={symbol} start pending={len(pending_signals)} "
                f"total_symbol={len(symbol_signals)}"
            ),
        )
        for signal in pending_signals:
            path = _build_compact_path_series(
                signal,
                archive_by_symbol[symbol],
                horizon_hours=config.horizon_hours,
                cache=cache,
            )
            signal_events = tuple(
                analyze_first_retest(path, activation_pct=activation, config=config)
                for activation in config.activation_levels_pct
            )
            events.extend(signal_events)
            _accumulate_policy_counts(policy_counts, path, signal_events, config)
            processed += 1
            reporter.emit(
                processed=processed,
                total=total_work,
                detail=(
                    f"symbol={symbol} cache_hits={cache.hits} "
                    f"cache_misses={cache.misses}"
                ),
            )
            del path
            if processed % CHECKPOINT_INTERVAL_SIGNALS == 0:
                _write_checkpoint(
                    checkpoint_path,
                    input_fingerprint=input_fingerprint,
                    processed=processed,
                    total=total_work,
                    events=events,
                    policy_counts=policy_counts,
                )
        _write_checkpoint(
            checkpoint_path,
            input_fingerprint=input_fingerprint,
            processed=processed,
            total=total_work,
            events=events,
            policy_counts=policy_counts,
        )
        reporter.emit(
            processed=processed,
            total=total_work,
            force=True,
            detail=(
                f"symbol={symbol} complete cache_hits={cache.hits} "
                f"cache_misses={cache.misses} checkpoint=saved"
            ),
        )
        del cache

    if processed != total_work:
        raise RuntimeError(f"P49 processed signal mismatch: {processed} != {total_work}")
    _write_checkpoint(
        checkpoint_path,
        input_fingerprint=input_fingerprint,
        processed=processed,
        total=total_work,
        events=events,
        policy_counts=policy_counts,
    )

    events_tuple = tuple(events)
    depth_rows = build_depth_bucket_rows(events_tuple)
    runner_rows = build_runner_depth_rows(events_tuple, config)
    policy_rows = _policy_rows_from_counts(policy_counts, config)

    status_counts: dict[str, int] = {}
    for event in events_tuple:
        status_counts[event.status] = status_counts.get(event.status, 0) + 1

    source_provenance: list[dict[str, str]] = []
    for source in sources:
        source_provenance.append(
            {
                "symbol": source.symbol,
                "p40_dir": str(source.p40_dir),
                "features_path": str(source.features_path),
                "features_sha256": _sha256(source.features_path),
                "summary_path": str(source.summary_path),
                "summary_sha256": _sha256(source.summary_path),
                "dataset_dir": str(source.dataset_dir),
            }
        )

    summary: dict[str, Any] = {
        "research_version": "P49_FIRST_RETEST_STOP_ANATOMY_V1_2_MEMORY_BOUNDED",
        "generated_at": datetime.now(UTC).isoformat(),
        "period": PERIOD_TAG,
        "symbols": list(ALL_SYMBOLS),
        "signals": len(signals),
        "event_rows": len(events_tuple),
        "status_counts": status_counts,
        "config": asdict(config),
        "downloads": "DISABLED",
        "memory_mode": "one_symbol_cache_compact_signal_path_no_path_retention",
        "entry_v1_changed": False,
        "p46_changed": False,
        "exit_risk_production_changed": False,
        "source_provenance": source_provenance,
    }

    _write_csv(output_dir / "first_retest_events.csv", [asdict(event) for event in events_tuple])
    _write_csv(output_dir / "depth_buckets.csv", depth_rows)
    _write_csv(output_dir / "runner_retest_depth.csv", runner_rows)
    _write_csv(output_dir / "post_retest_stop_policy.csv", policy_rows)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_summary_md(output_dir / "summary.md", summary)
    reporter.emit(processed=total_work, total=total_work, force=True, detail="done")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="P49 first retest / stop tightening anatomy")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--initial-stop-pct", type=float, default=1.0)
    parser.add_argument("--activation-levels-pct", default="0.10,0.20,0.25,0.50")
    parser.add_argument("--retest-start-drawdown-pct", type=float, default=0.05)
    parser.add_argument("--rebound-confirm-pct", type=float, default=0.05)
    parser.add_argument("--stop-candidates-pct", default="-0.75,-0.50,-0.25,0.10")
    parser.add_argument("--continuation-targets-pct", default="0.50,1.00,2.00,3.00")
    parser.add_argument("--horizon-hours", type=int, default=72)
    parser.add_argument("--day-cache-size", type=int, default=4)
    parser.add_argument("--progress-interval-seconds", type=float, default=25.0)
    parser.add_argument("--expected-signals", type=int, default=EXPECTED_SIGNALS)
    args = parser.parse_args()

    root = args.root.resolve()
    output_dir = args.output_dir
    if output_dir is None:
        output_dir = (
            root
            / "reports"
            / "first_retest_stop_anatomy_p49"
            / "ALL9_P49_WORKING"
        )

    config = P49Config(
        initial_stop_pct=args.initial_stop_pct,
        activation_levels_pct=_parse_csv_floats(args.activation_levels_pct),
        retest_start_drawdown_pct=args.retest_start_drawdown_pct,
        rebound_confirm_pct=args.rebound_confirm_pct,
        stop_candidates_pct=_parse_csv_floats(args.stop_candidates_pct),
        continuation_targets_pct=_parse_csv_floats(args.continuation_targets_pct),
        horizon_hours=args.horizon_hours,
        day_cache_size=args.day_cache_size,
        progress_interval_seconds=args.progress_interval_seconds,
        expected_signals=args.expected_signals,
    )
    summary = run_research(root, output_dir.resolve(), config)
    print(f"P49 signals: {summary['signals']}")
    print(f"P49 event rows: {summary['event_rows']}")
    print(f"P49 status counts: {summary['status_counts']}")
    print(f"Report: {output_dir.resolve() / 'summary.json'}")
    print(f"Readable summary: {output_dir.resolve() / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
