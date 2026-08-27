from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import threading
import time
from collections.abc import Iterable
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
    _parse_csv_floats,
    _sha256,
    discover_sources,
    load_all_signals,
)
from bybit_workbench.research.flow_reversal_v1 import _archive_map

P50_VERSION = "P50_MULTI_RETEST_ENTRY_RECROSS_LIFECYCLE_V1"
CHECKPOINT_VERSION = "p50-multi-retest-entry-recross-v1"
EXPECTED_COHORT = 995
CHECKPOINT_INTERVAL_SIGNALS = 25
DEFAULT_ACTIVATION_PCT = 0.10
DEFAULT_RETEST_DRAWDOWN_PCT = 0.05
DEFAULT_STOPS = (-0.75, -0.60, -0.50, -0.35, -0.25, 0.10)
DEFAULT_TARGETS = (0.50, 1.00, 2.00, 3.00)
ACTION_NUMBERS = (1, 2, 3, 4, 5, 6)

RetestStatus = Literal["reclaimed_peak", "initial_stop", "unresolved"]
VisitStatus = Literal["recovered_plus_0p10", "initial_stop", "unresolved"]


@dataclass(frozen=True, slots=True)
class P50Config:
    activation_pct: float = DEFAULT_ACTIVATION_PCT
    initial_stop_pct: float = 1.0
    retest_drawdown_pct: float = DEFAULT_RETEST_DRAWDOWN_PCT
    stop_candidates_pct: tuple[float, ...] = DEFAULT_STOPS
    continuation_targets_pct: tuple[float, ...] = DEFAULT_TARGETS
    horizon_hours: int = 72
    day_cache_size: int = 4
    progress_interval_seconds: float = 25.0
    expected_signals: int = EXPECTED_SIGNALS
    expected_cohort: int = EXPECTED_COHORT

    def __post_init__(self) -> None:
        if self.activation_pct <= 0:
            raise ValueError("activation_pct must be positive")
        if self.initial_stop_pct <= 0:
            raise ValueError("initial_stop_pct must be positive")
        if self.retest_drawdown_pct <= 0:
            raise ValueError("retest_drawdown_pct must be positive")
        if self.stop_candidates_pct != DEFAULT_STOPS:
            raise ValueError("P50 V1 stop candidates are frozen")
        if self.continuation_targets_pct != DEFAULT_TARGETS:
            raise ValueError("P50 V1 continuation targets are frozen")
        if self.horizon_hours <= 0 or self.day_cache_size <= 0:
            raise ValueError("horizon_hours and day_cache_size must be positive")
        if self.progress_interval_seconds <= 0:
            raise ValueError("progress_interval_seconds must be positive")


@dataclass(frozen=True, slots=True)
class RetestCycleEvent:
    symbol: str
    direction: str
    touch_at: str
    cycle_no: int
    peak_pct: float
    peak_at: str
    retest_started_at: str
    retest_start_move_pct: float
    low_pct: float
    low_at: str
    status: RetestStatus
    reclaim_at: str | None
    initial_stop_at: str | None
    crossed_entry: bool
    higher_low_vs_previous: bool | None
    low_delta_vs_previous_pct: float | None
    seconds_from_touch_to_start: float
    retest_seconds: float | None


@dataclass(frozen=True, slots=True)
class EntryVisitEvent:
    symbol: str
    direction: str
    touch_at: str
    visit_no: int
    started_at: str
    low_pct: float
    low_at: str
    status: VisitStatus
    recovered_plus_0p10_at: str | None
    initial_stop_at: str | None
    higher_low_vs_previous: bool | None
    low_delta_vs_previous_pct: float | None
    pre_visit_peak_pct: float
    seconds_from_touch_to_start: float
    seconds_to_resolution: float | None
    zero_crossings_in_visit: int


@dataclass(frozen=True, slots=True)
class SignalLifecycle:
    symbol: str
    direction: str
    touch_at: str
    activation_at: str
    retest_cycles: int
    reclaimed_cycles: int
    entry_visits: int
    recovered_entry_visits: int
    initial_stop_at: str | None
    max_favourable_pct: float
    min_adverse_pct: float
    complete_horizon: bool
    missing_archive_days: str


@dataclass(slots=True)
class TradeoffCounts:
    eligible: int = 0
    future_runners: int = 0
    future_initial_stop_losers: int = 0
    horizon_nonrunners: int = 0
    censored: int = 0
    lost_runners: int = 0
    saved_losers: int = 0
    candidate_stop_exits: int = 0


class Heartbeat:
    def __init__(self, total: int, interval_seconds: float) -> None:
        self.total = total
        self.interval_seconds = interval_seconds
        self.started = time.monotonic()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._processed = 0
        self._detail = "starting"
        self._detail_started = self.started
        self._thread = threading.Thread(target=self._run, name="p50-heartbeat", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self.emit(force=True)

    def update(self, processed: int, detail: str) -> None:
        with self._lock:
            self._processed = processed
            if detail != self._detail:
                self._detail = detail
                self._detail_started = time.monotonic()

    def emit(self, *, force: bool = False) -> None:
        if not force and self._stop.is_set():
            return
        with self._lock:
            processed = self._processed
            detail = self._detail
            detail_started = self._detail_started
        now = time.monotonic()
        elapsed = now - self.started
        pct = 0.0 if self.total <= 0 else processed / self.total * 100.0
        eta: float | None = None
        if processed > 0 and processed < self.total:
            eta = elapsed / processed * (self.total - processed)
        eta_text = "n/a" if eta is None else _fmt(eta)
        print(
            f"[P50] processed={processed}/{self.total} ({pct:.1f}%) "
            f"elapsed={_fmt(elapsed)} ETA={eta_text} stage_elapsed={_fmt(now-detail_started)} "
            f"| {detail}",
            flush=True,
        )

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.emit()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=max(1.0, self.interval_seconds))
        self.emit(force=True)


def _fmt(seconds: float) -> str:
    value = max(0, int(round(seconds)))
    hours, rem = divmod(value, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _event_at(path: PathSeries, index: int | None) -> str | None:
    if index is None:
        return None
    return datetime.fromtimestamp(path.timestamps[index], UTC).isoformat()


def _seconds(path: PathSeries, start: int, end: int) -> float:
    return max(0.0, path.timestamps[end] - path.timestamps[start])


def _first_at_or_above_from(path: PathSeries, level: float, start: int) -> int | None:
    for index in range(max(0, start), len(path.moves_pct)):
        if path.moves_pct[index] >= level:
            return index
    return None


def _first_at_or_below_from(path: PathSeries, level: float, start: int) -> int | None:
    for index in range(max(0, start), len(path.moves_pct)):
        if path.moves_pct[index] <= level:
            return index
    return None


def _activation(path: PathSeries, config: P50Config) -> int:
    activation = _first_at_or_above_from(path, config.activation_pct, 0)
    stop = _first_at_or_below_from(path, -config.initial_stop_pct, 0)
    if activation is None or (stop is not None and stop < activation):
        raise ValueError(
            "P50 cohort inconsistency: no +0.10-before--1 activation for "
            f"{path.signal.symbol} {path.signal.touch_at.isoformat()}"
        )
    return activation


def analyze_retest_cycles(
    path: PathSeries,
    config: P50Config,
) -> list[tuple[RetestCycleEvent, int | None]]:
    activation = _activation(path, config)
    events: list[tuple[RetestCycleEvent, int | None]] = []
    cursor = activation
    cycle_no = 1
    previous_low: float | None = None

    while cursor < len(path.moves_pct) - 1:
        peak = path.moves_pct[cursor]
        peak_index = cursor
        retest_start: int | None = None
        for index in range(cursor + 1, len(path.moves_pct)):
            move = path.moves_pct[index]
            if move > peak:
                peak = move
                peak_index = index
                continue
            if peak - move >= config.retest_drawdown_pct:
                retest_start = index
                break
        if retest_start is None:
            break

        low = path.moves_pct[retest_start]
        low_index = retest_start
        reclaim: int | None = None
        stop: int | None = None
        for index in range(retest_start, len(path.moves_pct)):
            move = path.moves_pct[index]
            if move < low:
                low = move
                low_index = index
            if move <= -config.initial_stop_pct:
                stop = index
                break
            if index > retest_start and move >= peak:
                reclaim = index
                break

        if reclaim is not None:
            status: RetestStatus = "reclaimed_peak"
            resolution = reclaim
        elif stop is not None:
            status = "initial_stop"
            resolution = stop
        else:
            status = "unresolved"
            resolution = None

        event = RetestCycleEvent(
            symbol=path.signal.symbol,
            direction=str(path.signal.direction),
            touch_at=path.signal.touch_at.isoformat(),
            cycle_no=cycle_no,
            peak_pct=peak,
            peak_at=_event_at(path, peak_index) or "",
            retest_started_at=_event_at(path, retest_start) or "",
            retest_start_move_pct=path.moves_pct[retest_start],
            low_pct=low,
            low_at=_event_at(path, low_index) or "",
            status=status,
            reclaim_at=_event_at(path, reclaim),
            initial_stop_at=_event_at(path, stop),
            crossed_entry=low <= 0.0,
            higher_low_vs_previous=None if previous_low is None else low > previous_low,
            low_delta_vs_previous_pct=None if previous_low is None else low - previous_low,
            seconds_from_touch_to_start=max(
                0.0, path.timestamps[retest_start] - path.signal.touch_at.timestamp()
            ),
            retest_seconds=None if resolution is None else _seconds(path, retest_start, resolution),
        )
        events.append((event, reclaim))
        previous_low = low
        cycle_no += 1
        if reclaim is None:
            break
        cursor = reclaim
    return events


def analyze_entry_visits(
    path: PathSeries,
    config: P50Config,
) -> list[tuple[EntryVisitEvent, int | None]]:
    activation = _activation(path, config)
    events: list[tuple[EntryVisitEvent, int | None]] = []
    armed = True
    visit_no = 1
    previous_low: float | None = None
    running_peak = path.moves_pct[activation]
    index = activation + 1

    while index < len(path.moves_pct):
        move = path.moves_pct[index]
        running_peak = max(running_peak, move)
        if move <= -config.initial_stop_pct:
            break
        if not armed:
            if move >= config.activation_pct:
                armed = True
                running_peak = move
            index += 1
            continue
        if move > 0.0:
            index += 1
            continue

        start = index
        low = move
        low_index = index
        zero_crossings = 1
        last_nonpositive = True
        recovery: int | None = None
        stop: int | None = None
        index += 1
        while index < len(path.moves_pct):
            move = path.moves_pct[index]
            if move < low:
                low = move
                low_index = index
            now_nonpositive = move <= 0.0
            if now_nonpositive != last_nonpositive:
                zero_crossings += 1
                last_nonpositive = now_nonpositive
            if move <= -config.initial_stop_pct:
                stop = index
                break
            if move >= config.activation_pct:
                recovery = index
                break
            index += 1

        if recovery is not None:
            status: VisitStatus = "recovered_plus_0p10"
            resolution = recovery
        elif stop is not None:
            status = "initial_stop"
            resolution = stop
        else:
            status = "unresolved"
            resolution = None

        event = EntryVisitEvent(
            symbol=path.signal.symbol,
            direction=str(path.signal.direction),
            touch_at=path.signal.touch_at.isoformat(),
            visit_no=visit_no,
            started_at=_event_at(path, start) or "",
            low_pct=low,
            low_at=_event_at(path, low_index) or "",
            status=status,
            recovered_plus_0p10_at=_event_at(path, recovery),
            initial_stop_at=_event_at(path, stop),
            higher_low_vs_previous=None if previous_low is None else low > previous_low,
            low_delta_vs_previous_pct=None if previous_low is None else low - previous_low,
            pre_visit_peak_pct=running_peak,
            seconds_from_touch_to_start=max(
                0.0, path.timestamps[start] - path.signal.touch_at.timestamp()
            ),
            seconds_to_resolution=None if resolution is None else _seconds(path, start, resolution),
            zero_crossings_in_visit=zero_crossings,
        )
        events.append((event, recovery))
        previous_low = low
        visit_no += 1
        if recovery is None:
            break
        armed = False
        running_peak = path.moves_pct[recovery]
        index = recovery + 1
    return events


def _future_outcome(
    path: PathSeries,
    action: int,
    target: float,
    config: P50Config,
) -> tuple[str, int | None, int | None]:
    start = action + 1
    target_index = _first_at_or_above_from(path, target, start)
    stop_index = _first_at_or_below_from(path, -config.initial_stop_pct, start)
    if target_index is not None and (stop_index is None or target_index < stop_index):
        return "runner", target_index, stop_index
    if stop_index is not None and (target_index is None or stop_index < target_index):
        return "initial_stop", target_index, stop_index
    complete = path.complete_through >= path.signal.touch_at + timedelta(hours=config.horizon_hours)
    return ("horizon_nonrunner" if complete else "censored"), target_index, stop_index


def _minimum_until(path: PathSeries, start: int, end: int) -> float:
    if end < start:
        return path.moves_pct[start]
    return min(path.moves_pct[start : end + 1])


def _action_keys(number: int) -> Iterable[int]:
    if number in ACTION_NUMBERS:
        yield number


def _new_tradeoff() -> dict[tuple[str, int, float, float], TradeoffCounts]:
    return {
        (action, number, stop, target): TradeoffCounts()
        for action in ("peak_reclaim", "entry_recovery")
        for number in ACTION_NUMBERS
        for stop in DEFAULT_STOPS
        for target in DEFAULT_TARGETS
    }


def _new_room_samples() -> dict[tuple[str, int, float], list[float]]:
    return {
        (action, number, target): []
        for action in ("peak_reclaim", "entry_recovery")
        for number in ACTION_NUMBERS
        for target in DEFAULT_TARGETS
    }


def _accumulate_action(
    *,
    path: PathSeries,
    action_type: str,
    action_number: int,
    action_index: int,
    config: P50Config,
    tradeoff: dict[tuple[str, int, float, float], TradeoffCounts],
    room_samples: dict[tuple[str, int, float], list[float]],
) -> None:
    for number in _action_keys(action_number):
        for target in config.continuation_targets_pct:
            outcome, target_index, initial_stop_index = _future_outcome(
                path, action_index, target, config
            )
            if outcome == "runner" and target_index is not None:
                room_samples[(action_type, number, target)].append(
                    _minimum_until(path, action_index + 1, target_index)
                )
            for stop in config.stop_candidates_pct:
                counts = tradeoff[(action_type, number, stop, target)]
                counts.eligible += 1
                if outcome == "runner":
                    counts.future_runners += 1
                elif outcome == "initial_stop":
                    counts.future_initial_stop_losers += 1
                elif outcome == "horizon_nonrunner":
                    counts.horizon_nonrunners += 1
                else:
                    counts.censored += 1

                candidate_index = _first_at_or_below_from(path, stop, action_index + 1)
                if candidate_index is not None:
                    counts.candidate_stop_exits += 1
                if outcome == "runner" and target_index is not None:
                    if candidate_index is not None and candidate_index < target_index:
                        counts.lost_runners += 1
                elif (
                    outcome == "initial_stop"
                    and initial_stop_index is not None
                    and candidate_index is not None
                    and candidate_index < initial_stop_index
                ):
                    counts.saved_losers += 1


def _signal_lifecycle(
    path: PathSeries,
    config: P50Config,
    cycles: list[tuple[RetestCycleEvent, int | None]],
    visits: list[tuple[EntryVisitEvent, int | None]],
) -> SignalLifecycle:
    activation = _activation(path, config)
    stop = _first_at_or_below_from(path, -config.initial_stop_pct, activation + 1)
    complete = path.complete_through >= path.signal.touch_at + timedelta(hours=config.horizon_hours)
    return SignalLifecycle(
        symbol=path.signal.symbol,
        direction=str(path.signal.direction),
        touch_at=path.signal.touch_at.isoformat(),
        activation_at=_event_at(path, activation) or "",
        retest_cycles=len(cycles),
        reclaimed_cycles=sum(event.status == "reclaimed_peak" for event, _ in cycles),
        entry_visits=len(visits),
        recovered_entry_visits=sum(event.status == "recovered_plus_0p10" for event, _ in visits),
        initial_stop_at=_event_at(path, stop),
        max_favourable_pct=max(path.moves_pct),
        min_adverse_pct=min(path.moves_pct),
        complete_horizon=complete,
        missing_archive_days=";".join(path.missing_archive_days),
    )


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    fraction = pos - lo
    value = ordered[lo] * (1.0 - fraction) + ordered[hi] * fraction
    return round(value, 6)


def _pct(n: int, d: int) -> float | None:
    return None if d <= 0 else round(n / d * 100.0, 2)


def build_retest_number_summary(events: list[RetestCycleEvent]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number in range(1, 7):
        subset = [event for event in events if event.cycle_no == number]
        lows = [event.low_pct for event in subset]
        reclaimed = [event for event in subset if event.status == "reclaimed_peak"]
        rows.append(
            {
                "cycle_no": number,
                "eligible": len(subset),
                "reclaimed": len(reclaimed),
                "reclaimed_pct": _pct(len(reclaimed), len(subset)),
                "initial_stop": sum(event.status == "initial_stop" for event in subset),
                "unresolved": sum(event.status == "unresolved" for event in subset),
                "crossed_entry": sum(event.crossed_entry for event in subset),
                "crossed_entry_pct": _pct(
                    sum(event.crossed_entry for event in subset), len(subset)
                ),
                "higher_low": sum(event.higher_low_vs_previous is True for event in subset),
                "higher_low_pct": _pct(
                    sum(event.higher_low_vs_previous is True for event in subset),
                    sum(event.higher_low_vs_previous is not None for event in subset),
                ),
                "low_p10": _quantile(lows, 0.10),
                "low_p25": _quantile(lows, 0.25),
                "low_median": None if not lows else round(statistics.median(lows), 6),
                "low_p75": _quantile(lows, 0.75),
            }
        )
    return rows


def build_visit_number_summary(events: list[EntryVisitEvent]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number in range(1, 7):
        subset = [event for event in events if event.visit_no == number]
        lows = [event.low_pct for event in subset]
        recovered = [event for event in subset if event.status == "recovered_plus_0p10"]
        durations = [
            event.seconds_to_resolution
            for event in subset
            if event.seconds_to_resolution is not None
        ]
        rows.append(
            {
                "visit_no": number,
                "eligible": len(subset),
                "recovered_plus_0p10": len(recovered),
                "recovered_pct": _pct(len(recovered), len(subset)),
                "initial_stop": sum(event.status == "initial_stop" for event in subset),
                "unresolved": sum(event.status == "unresolved" for event in subset),
                "higher_low": sum(event.higher_low_vs_previous is True for event in subset),
                "higher_low_pct": _pct(
                    sum(event.higher_low_vs_previous is True for event in subset),
                    sum(event.higher_low_vs_previous is not None for event in subset),
                ),
                "low_p10": _quantile(lows, 0.10),
                "low_p25": _quantile(lows, 0.25),
                "low_median": None if not lows else round(statistics.median(lows), 6),
                "resolution_seconds_median": (
                    None if not durations else round(statistics.median(durations), 3)
                ),
            }
        )
    return rows


def build_low_trend_summary(events: list[EntryVisitEvent]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number in range(2, 7):
        subset = [event for event in events if event.visit_no == number]
        groups = {
            "higher_low": [
                event for event in subset if event.higher_low_vs_previous is True
            ],
            "lower_or_equal_low": [
                event for event in subset if event.higher_low_vs_previous is False
            ],
        }
        for trend, chosen in groups.items():
            recovered = sum(
                event.status == "recovered_plus_0p10" for event in chosen
            )
            initial_stop = sum(event.status == "initial_stop" for event in chosen)
            rows.append(
                {
                    "visit_no": number,
                    "low_trend": trend,
                    "events": len(chosen),
                    "recovered_plus_0p10": recovered,
                    "recovered_pct": _pct(recovered, len(chosen)),
                    "initial_stop": initial_stop,
                    "initial_stop_pct": _pct(initial_stop, len(chosen)),
                }
            )
    return rows


def build_tradeoff_rows(
    counts: dict[tuple[str, int, float, float], TradeoffCounts],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in sorted(counts, key=lambda item: (item[0], item[1], item[2], item[3])):
        action, number, stop, target = key
        value = counts[key]
        rows.append(
            {
                "action": action,
                "action_no": number,
                "stop_pct": stop,
                "target_pct": target,
                **asdict(value),
                "runner_retention_pct": _pct(
                    value.future_runners - value.lost_runners, value.future_runners
                ),
                "runner_lost_pct": _pct(value.lost_runners, value.future_runners),
                "loser_saved_pct": _pct(
                    value.saved_losers, value.future_initial_stop_losers
                ),
            }
        )
    return rows


def build_room_rows(samples: dict[tuple[str, int, float], list[float]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in sorted(samples, key=lambda item: (item[0], item[1], item[2])):
        action, number, target = key
        values = samples[key]
        row: dict[str, Any] = {
            "action": action,
            "action_no": number,
            "target_pct": target,
            "future_runners": len(values),
            "min_move_p05": _quantile(values, 0.05),
            "min_move_p10": _quantile(values, 0.10),
            "min_move_p25": _quantile(values, 0.25),
            "min_move_median": None if not values else round(statistics.median(values), 6),
        }
        for stop in DEFAULT_STOPS:
            row[f"survive_stop_{_signed_key(stop)}"] = sum(value > stop for value in values)
            row[f"survive_stop_{_signed_key(stop)}_pct"] = _pct(
                sum(value > stop for value in values), len(values)
            )
        rows.append(row)
    return rows


def _signed_key(value: float) -> str:
    return ("m" if value < 0 else "p") + f"{abs(value):.2f}".replace(".", "p")


def _read_p49_cohort(
    p49_dir: Path,
    config: P50Config,
) -> tuple[set[tuple[str, str, str]], dict[str, Any]]:
    summary_path = p49_dir / "summary.json"
    events_path = p49_dir / "first_retest_events.csv"
    if not summary_path.is_file() or not events_path.is_file():
        raise FileNotFoundError(
            "P50 requires completed P49.2 summary.json and first_retest_events.csv"
        )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("research_version") != "P49_FIRST_RETEST_STOP_ANATOMY_V1_2_MEMORY_BOUNDED":
        raise ValueError("P50 requires P49.2 MEMORY BOUNDED report")
    if int(summary.get("signals", -1)) != config.expected_signals:
        raise ValueError("P49.2 signal count mismatch")
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
            f"P50 cohort mismatch: expected {config.expected_cohort}, got {len(selected)}"
        )
    return selected, {
        "p49_dir": str(p49_dir.resolve()),
        "p49_summary_sha256": _sha256(summary_path),
        "p49_events_sha256": _sha256(events_path),
    }


def _signal_key(signal: CoreSignal) -> tuple[str, str, str]:
    return signal.symbol, str(signal.direction), signal.touch_at.isoformat()


def _fingerprint(config: P50Config, provenance: dict[str, Any], sources: tuple[Any, ...]) -> str:
    payload: dict[str, Any] = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "period": PERIOD_TAG,
        "symbols": list(ALL_SYMBOLS),
        "config": asdict(config),
        "p49": provenance,
        "sources": [],
    }
    for source in sources:
        payload["sources"].append(
            {
                "symbol": source.symbol,
                "features_sha256": _sha256(source.features_path),
                "summary_sha256": _sha256(source.summary_path),
                "dataset_manifest_sha256": _sha256(source.dataset_dir / "dataset_manifest.json"),
            }
        )
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _serialize_tradeoff(
    counts: dict[tuple[str, int, float, float], TradeoffCounts],
) -> list[dict[str, Any]]:
    return [
        {"action": k[0], "number": k[1], "stop": k[2], "target": k[3], **asdict(v)}
        for k, v in counts.items()
    ]


def _deserialize_tradeoff(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, int, float, float], TradeoffCounts]:
    counts = _new_tradeoff()
    for row in rows:
        key = (str(row["action"]), int(row["number"]), float(row["stop"]), float(row["target"]))
        counts[key] = TradeoffCounts(
            eligible=int(row["eligible"]),
            future_runners=int(row["future_runners"]),
            future_initial_stop_losers=int(row["future_initial_stop_losers"]),
            horizon_nonrunners=int(row["horizon_nonrunners"]),
            censored=int(row["censored"]),
            lost_runners=int(row["lost_runners"]),
            saved_losers=int(row["saved_losers"]),
            candidate_stop_exits=int(row["candidate_stop_exits"]),
        )
    return counts


def _serialize_rooms(samples: dict[tuple[str, int, float], list[float]]) -> list[dict[str, Any]]:
    return [
        {"action": key[0], "number": key[1], "target": key[2], "values": values}
        for key, values in samples.items()
    ]


def _deserialize_rooms(rows: list[dict[str, Any]]) -> dict[tuple[str, int, float], list[float]]:
    samples = _new_room_samples()
    for row in rows:
        key = (str(row["action"]), int(row["number"]), float(row["target"]))
        samples[key] = [float(value) for value in row["values"]]
    return samples


def _write_checkpoint(
    path: Path,
    *,
    fingerprint: str,
    processed: int,
    total: int,
    cycles: list[RetestCycleEvent],
    visits: list[EntryVisitEvent],
    lifecycles: list[SignalLifecycle],
    tradeoff: dict[tuple[str, int, float, float], TradeoffCounts],
    rooms: dict[tuple[str, int, float], list[float]],
) -> None:
    payload = {
        "checkpoint_version": CHECKPOINT_VERSION,
        "fingerprint": fingerprint,
        "processed": processed,
        "total": total,
        "cycles": [asdict(event) for event in cycles],
        "visits": [asdict(event) for event in visits],
        "lifecycles": [asdict(event) for event in lifecycles],
        "tradeoff": _serialize_tradeoff(tradeoff),
        "rooms": _serialize_rooms(rooms),
    }
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temp.replace(path)


def _load_checkpoint(
    path: Path,
    *,
    fingerprint: str,
    total: int,
) -> tuple[
    int,
    list[RetestCycleEvent],
    list[EntryVisitEvent],
    list[SignalLifecycle],
    dict[tuple[str, int, float, float], TradeoffCounts],
    dict[tuple[str, int, float], list[float]],
]:
    if not path.is_file():
        return 0, [], [], [], _new_tradeoff(), _new_room_samples()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("checkpoint_version") != CHECKPOINT_VERSION:
        raise ValueError("P50 checkpoint version mismatch")
    if payload.get("fingerprint") != fingerprint:
        raise ValueError("P50 checkpoint fingerprint mismatch")
    if int(payload.get("total", -1)) != total:
        raise ValueError("P50 checkpoint total mismatch")
    processed = int(payload.get("processed", -1))
    lifecycles = [SignalLifecycle(**dict(row)) for row in payload.get("lifecycles", [])]
    if len(lifecycles) != processed:
        raise ValueError("P50 checkpoint lifecycle row count mismatch")
    return (
        processed,
        [RetestCycleEvent(**dict(row)) for row in payload.get("cycles", [])],
        [EntryVisitEvent(**dict(row)) for row in payload.get("visits", [])],
        lifecycles,
        _deserialize_tradeoff([dict(row) for row in payload.get("tradeoff", [])]),
        _deserialize_rooms([dict(row) for row in payload.get("rooms", [])]),
    )


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for field in row:
            if field not in seen:
                seen.add(field)
                fields.append(field)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_summary_md(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# P50 Multi-Retest / Entry Recross Lifecycle",
        "",
        "Research only. Downloads: DISABLED.",
        "Entry V1, frozen P46, live Execution, Exit and Risk production logic are unchanged.",
        "",
        f"- Cohort: {summary['cohort']} (+0.10 before -1.00)",
        f"- Retest-cycle rows: {summary['retest_cycle_rows']}",
        f"- Entry-zone visit rows: {summary['entry_visit_rows']}",
        f"- Complete 72h: {summary['complete_horizon']}",
        f"- Right-censored: {summary['right_censored']}",
        "",
        "No stop rule is selected by P50. The five reserved OOS assets remain untouched.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_research(root: Path, p49_dir: Path, output_dir: Path, config: P50Config) -> dict[str, Any]:
    selected_keys, provenance = _read_p49_cohort(p49_dir, config)
    sources = discover_sources(root)
    all_signals = load_all_signals(sources)
    if len(all_signals) != config.expected_signals:
        raise ValueError(f"P50 Entry signal count mismatch: {len(all_signals)}")
    selected = [signal for signal in all_signals if _signal_key(signal) in selected_keys]
    selected_signal_keys = {_signal_key(signal) for signal in selected}
    if len(selected) != config.expected_cohort or selected_signal_keys != selected_keys:
        raise ValueError("P50 selected cohort does not map exactly to current frozen Entry signals")

    source_by_symbol = {source.symbol: source for source in sources}
    archives = {
        symbol: _archive_map(source_by_symbol[symbol].dataset_dir) for symbol in ALL_SYMBOLS
    }
    signals_by_symbol: dict[str, list[CoreSignal]] = {symbol: [] for symbol in ALL_SYMBOLS}
    for signal in selected:
        signals_by_symbol[signal.symbol].append(signal)

    output_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = _fingerprint(config, provenance, sources)
    checkpoint = output_dir / "checkpoint.json"
    processed, cycle_events, visit_events, lifecycles, tradeoff, rooms = _load_checkpoint(
        checkpoint, fingerprint=fingerprint, total=len(selected)
    )

    heartbeat = Heartbeat(len(selected), config.progress_interval_seconds)
    heartbeat.update(processed, "resume from checkpoint" if processed else "multi-retest lifecycle")
    heartbeat.start()
    try:
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
            symbol_done = len(symbol_signals) - len(pending)
            for signal in pending:
                signal_no = symbol_done + 1
                heartbeat.update(
                    processed,
                    f"symbol={symbol} signal={signal_no}/{len(symbol_signals)} stage=build_path "
                    f"cache_hits={cache.hits} cache_misses={cache.misses}",
                )
                path = _build_compact_path_series(
                    signal,
                    archives[symbol],
                    horizon_hours=config.horizon_hours,
                    cache=cache,
                )
                heartbeat.update(
                    processed,
                    f"symbol={symbol} signal={signal_no}/{len(symbol_signals)} "
                    "stage=analyze_lifecycle",
                )
                cycles = analyze_retest_cycles(path, config)
                visits = analyze_entry_visits(path, config)
                cycle_events.extend(event for event, _ in cycles)
                visit_events.extend(event for event, _ in visits)
                lifecycles.append(_signal_lifecycle(path, config, cycles, visits))
                for cycle_event, action_index in cycles:
                    if cycle_event.status == "reclaimed_peak" and action_index is not None:
                        _accumulate_action(
                            path=path,
                            action_type="peak_reclaim",
                            action_number=cycle_event.cycle_no,
                            action_index=action_index,
                            config=config,
                            tradeoff=tradeoff,
                            room_samples=rooms,
                        )
                for visit_event, action_index in visits:
                    if (
                        visit_event.status == "recovered_plus_0p10"
                        and action_index is not None
                    ):
                        _accumulate_action(
                            path=path,
                            action_type="entry_recovery",
                            action_number=visit_event.visit_no,
                            action_index=action_index,
                            config=config,
                            tradeoff=tradeoff,
                            room_samples=rooms,
                        )
                processed += 1
                symbol_done += 1
                heartbeat.update(
                    processed,
                    f"symbol={symbol} signal={signal_no}/{len(symbol_signals)} stage=done "
                    f"retests={len(cycles)} entry_visits={len(visits)}",
                )
                del path
                if processed % CHECKPOINT_INTERVAL_SIGNALS == 0:
                    _write_checkpoint(
                        checkpoint,
                        fingerprint=fingerprint,
                        processed=processed,
                        total=len(selected),
                        cycles=cycle_events,
                        visits=visit_events,
                        lifecycles=lifecycles,
                        tradeoff=tradeoff,
                        rooms=rooms,
                    )
            _write_checkpoint(
                checkpoint,
                fingerprint=fingerprint,
                processed=processed,
                total=len(selected),
                cycles=cycle_events,
                visits=visit_events,
                lifecycles=lifecycles,
                tradeoff=tradeoff,
                rooms=rooms,
            )
            heartbeat.update(processed, f"symbol={symbol} complete checkpoint=saved")
            heartbeat.emit(force=True)
            del cache
    finally:
        heartbeat.close()

    if processed != len(selected):
        raise RuntimeError(f"P50 processed mismatch: {processed} != {len(selected)}")

    retest_summary = build_retest_number_summary(cycle_events)
    visit_summary = build_visit_number_summary(visit_events)
    low_trend = build_low_trend_summary(visit_events)
    tradeoff_rows = build_tradeoff_rows(tradeoff)
    room_rows = build_room_rows(rooms)

    summary: dict[str, Any] = {
        "research_version": P50_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "period": PERIOD_TAG,
        "symbols": list(ALL_SYMBOLS),
        "cohort_definition": "+0.10 before original -1.00 initial stop",
        "cohort": len(selected),
        "retest_cycle_rows": len(cycle_events),
        "entry_visit_rows": len(visit_events),
        "complete_horizon": sum(item.complete_horizon for item in lifecycles),
        "right_censored": sum(not item.complete_horizon for item in lifecycles),
        "config": asdict(config),
        "downloads": "DISABLED",
        "entry_v1_changed": False,
        "p46_changed": False,
        "exit_risk_production_changed": False,
        "reserved_five_oos_assets_touched": False,
        "p49_provenance": provenance,
        "source_provenance": [
            {
                "symbol": source.symbol,
                "features_sha256": _sha256(source.features_path),
                "summary_sha256": _sha256(source.summary_path),
                "dataset_manifest_sha256": _sha256(source.dataset_dir / "dataset_manifest.json"),
            }
            for source in sources
        ],
    }

    _write_csv(output_dir / "retest_cycles.csv", [asdict(event) for event in cycle_events])
    _write_csv(output_dir / "entry_zone_visits.csv", [asdict(event) for event in visit_events])
    _write_csv(output_dir / "signal_lifecycle.csv", [asdict(event) for event in lifecycles])
    _write_csv(output_dir / "retest_by_number.csv", retest_summary)
    _write_csv(output_dir / "entry_recross_by_number.csv", visit_summary)
    _write_csv(output_dir / "entry_recross_low_trend.csv", low_trend)
    _write_csv(output_dir / "stop_tradeoff_by_action_number.csv", tradeoff_rows)
    _write_csv(output_dir / "runner_required_room_by_action_number.csv", room_rows)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _write_summary_md(output_dir / "summary.md", summary)
    _write_checkpoint(
        checkpoint,
        fingerprint=fingerprint,
        processed=processed,
        total=len(selected),
        cycles=cycle_events,
        visits=visit_events,
        lifecycles=lifecycles,
        tradeoff=tradeoff,
        rooms=rooms,
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="P50 multi-retest / Entry recross lifecycle")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--p49-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--activation-pct", type=float, default=DEFAULT_ACTIVATION_PCT)
    parser.add_argument("--initial-stop-pct", type=float, default=1.0)
    parser.add_argument("--retest-drawdown-pct", type=float, default=DEFAULT_RETEST_DRAWDOWN_PCT)
    parser.add_argument("--stop-candidates-pct", default="-0.75,-0.60,-0.50,-0.35,-0.25,0.10")
    parser.add_argument("--continuation-targets-pct", default="0.50,1.00,2.00,3.00")
    parser.add_argument("--horizon-hours", type=int, default=72)
    parser.add_argument("--day-cache-size", type=int, default=4)
    parser.add_argument("--progress-interval-seconds", type=float, default=25.0)
    parser.add_argument("--expected-signals", type=int, default=EXPECTED_SIGNALS)
    parser.add_argument("--expected-cohort", type=int, default=EXPECTED_COHORT)
    args = parser.parse_args()

    root = args.root.resolve()
    p49_dir = args.p49_dir or (
        root / "reports" / "first_retest_stop_anatomy_p49" / "ALL9_P49_WORKING"
    )
    output = args.output_dir or (
        root / "reports" / "multi_retest_entry_recross_p50" / "ALL9_P50_WORKING"
    )
    config = P50Config(
        activation_pct=args.activation_pct,
        initial_stop_pct=args.initial_stop_pct,
        retest_drawdown_pct=args.retest_drawdown_pct,
        stop_candidates_pct=_parse_csv_floats(args.stop_candidates_pct),
        continuation_targets_pct=_parse_csv_floats(args.continuation_targets_pct),
        horizon_hours=args.horizon_hours,
        day_cache_size=args.day_cache_size,
        progress_interval_seconds=args.progress_interval_seconds,
        expected_signals=args.expected_signals,
        expected_cohort=args.expected_cohort,
    )
    summary = run_research(root, p49_dir.resolve(), output.resolve(), config)
    print(f"P50 cohort: {summary['cohort']}")
    print(f"P50 retest rows: {summary['retest_cycle_rows']}")
    print(f"P50 Entry-zone visit rows: {summary['entry_visit_rows']}")
    print(f"Report: {output.resolve() / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
