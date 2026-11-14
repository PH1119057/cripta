from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import statistics
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from bybit_workbench.research.exit_break_even_v13 import CoreSignal, PathSeries, TradeDayCache
from bybit_workbench.research.first_retest_stop_anatomy_p49 import (
    ALL_SYMBOLS,
    EXPECTED_SIGNALS,
    PERIOD_TAG,
    _build_compact_path_series,
    _sha256,
    discover_sources,
    load_all_signals,
)
from bybit_workbench.research.flow_reversal_v1 import _archive_map
from bybit_workbench.research.multi_retest_entry_recross_p50 import (
    EXPECTED_COHORT,
    P50Config,
    analyze_entry_visits,
)

P51_VERSION = "P51_MFE_ACTIVATED_RISK_GIVEBACK_V1"
CHECKPOINT_VERSION = "p51-mfe-activated-risk-giveback-v1"
DEFAULT_MFE_MILESTONES = (0.25, 0.50, 0.75, 1.00)
DEFAULT_STOPS = (-0.75, -0.60, -0.50)
DEFAULT_TARGETS = (1.10, 2.00, 3.00)
ACTION_NUMBERS = (1, 2, 3, 4, 5, 6)
EXPECTED_PLUS_110 = 594
EXPECTED_MINUS_100_AFTER_ACTIVATION = 394
EXPECTED_DATA_END_AFTER_ACTIVATION = 7
CHECKPOINT_INTERVAL_SIGNALS = 25

BaselineOutcome = Literal["reached_plus_1p10", "hit_minus_1p00", "data_end"]
FutureOutcome = Literal[
    "runner",
    "initial_stop",
    "horizon_nonrunner",
    "censored",
    "target_already_reached",
]


@dataclass(frozen=True, slots=True)
class P51Config:
    activation_pct: float = 0.10
    initial_stop_pct: float = 1.00
    mfe_milestones_pct: tuple[float, ...] = DEFAULT_MFE_MILESTONES
    stop_candidates_pct: tuple[float, ...] = DEFAULT_STOPS
    continuation_targets_pct: tuple[float, ...] = DEFAULT_TARGETS
    horizon_hours: int = 72
    day_cache_size: int = 4
    progress_interval_seconds: float = 25.0
    expected_signals: int = EXPECTED_SIGNALS
    expected_cohort: int = EXPECTED_COHORT

    def __post_init__(self) -> None:
        if self.activation_pct != 0.10:
            raise ValueError("P51 V1 activation_pct is frozen at +0.10")
        if self.initial_stop_pct != 1.00:
            raise ValueError("P51 V1 initial_stop_pct is frozen at -1.00")
        if self.mfe_milestones_pct != DEFAULT_MFE_MILESTONES:
            raise ValueError("P51 V1 MFE milestones are frozen")
        if self.stop_candidates_pct != DEFAULT_STOPS:
            raise ValueError("P51 V1 stop candidates are frozen")
        if self.continuation_targets_pct != DEFAULT_TARGETS:
            raise ValueError("P51 V1 continuation targets are frozen")
        if self.horizon_hours != 72:
            raise ValueError("P51 V1 horizon is frozen at 72 hours")
        if self.day_cache_size <= 0:
            raise ValueError("day_cache_size must be positive")
        if self.progress_interval_seconds <= 0:
            raise ValueError("progress_interval_seconds must be positive")


@dataclass(frozen=True, slots=True)
class ExactBaselineEvent:
    symbol: str
    direction: str
    touch_at: datetime
    outcome: BaselineOutcome
    event_at: datetime | None
    complete_horizon: bool


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


@dataclass(frozen=True, slots=True)
class FirstRuleSignalRow:
    symbol: str
    direction: str
    touch_at: str
    mfe_milestone_pct: float
    action_visit_no: int | None
    action_at: str | None
    cumulative_mfe_before_visit_pct: float | None
    action_visit_low_pct: float | None
    action_zero_crossings: int | None
    stop_pct: float
    baseline_outcome: BaselineOutcome
    baseline_event_at: str | None
    acted_before_baseline_outcome: bool
    candidate_stop_before_baseline_outcome: bool
    saved_baseline_loser: bool
    killed_baseline_winner: bool
    illustrative_delta_usd: float


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
        self._thread = threading.Thread(target=self._run, name="p51-heartbeat", daemon=True)

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
            f"[P51] processed={processed}/{self.total} ({pct:.1f}%) "
            f"elapsed={_fmt(elapsed)} ETA={eta_text} stage_elapsed={_fmt(now - detail_started)} "
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


def _signal_key(symbol: str, direction: str, touch_at: datetime) -> tuple[str, str, int]:
    return symbol, direction, int(round(touch_at.timestamp() * 1_000_000))


def _signal_key_from_signal(signal: CoreSignal) -> tuple[str, str, int]:
    return _signal_key(signal.symbol, str(signal.direction), signal.touch_at)


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _index_at_or_after(path: PathSeries, value: datetime) -> int:
    return bisect.bisect_left(path.timestamps, value.timestamp())


def _first_at_or_above(path: PathSeries, level: float, start: int = 0) -> int | None:
    for index in range(max(0, start), len(path.moves_pct)):
        if path.moves_pct[index] >= level:
            return index
    return None


def _first_at_or_below(path: PathSeries, level: float, start: int = 0) -> int | None:
    for index in range(max(0, start), len(path.moves_pct)):
        if path.moves_pct[index] <= level:
            return index
    return None


def _minimum_until(path: PathSeries, start: int, end: int) -> float:
    if start > end:
        return path.moves_pct[end]
    return min(path.moves_pct[start : end + 1])


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    weight = pos - low
    return round(ordered[low] * (1.0 - weight) + ordered[high] * weight, 6)


def _median(values: list[float]) -> float | None:
    return None if not values else round(statistics.median(values), 6)


def _pct(numerator: int, denominator: int) -> float | None:
    return None if denominator <= 0 else round(numerator / denominator * 100.0, 2)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _discover_exact_baseline_dir(root: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        candidate = explicit.resolve()
        if not (candidate / "event_results.csv").is_file():
            raise FileNotFoundError(f"Exact baseline event_results.csv not found: {candidate}")
        return candidate

    base = root / "reports" / "untouched_minus1_plus110_v1"
    candidates = sorted(
        (item.parent for item in base.glob("ALL9_*/summary.json")),
        key=lambda item: item.name,
        reverse=True,
    )
    for candidate in candidates:
        events = candidate / "event_results.csv"
        scope = candidate / "scope_summary.csv"
        if events.is_file() and scope.is_file():
            return candidate
    raise FileNotFoundError(
        "Exact untouched -1.00 vs +1.10 baseline not found under "
        f"{base}. Run the accepted exact baseline first or pass --exact-baseline-dir."
    )


def _load_p50_cohort(
    p50_dir: Path, config: P51Config
) -> tuple[set[tuple[str, str, int]], dict[str, Any]]:
    summary_path = p50_dir / "summary.json"
    lifecycle_path = p50_dir / "signal_lifecycle.csv"
    if not summary_path.is_file() or not lifecycle_path.is_file():
        raise FileNotFoundError(f"Completed P50 report not found: {p50_dir}")
    summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    if summary.get("cohort") != config.expected_cohort:
        raise ValueError("P51 requires the completed 995-signal P50 cohort")
    if summary.get("reserved_five_oos_assets_touched") is not False:
        raise ValueError("P50 provenance says reserved OOS assets were touched")

    keys: set[tuple[str, str, int]] = set()
    with lifecycle_path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            keys.add(_signal_key(row["symbol"], row["direction"], _parse_dt(row["touch_at"])))
    if len(keys) != config.expected_cohort:
        raise ValueError(f"P50 signal_lifecycle cohort mismatch: {len(keys)}")
    provenance = {
        "p50_dir": str(p50_dir),
        "p50_summary_sha256": _sha256(summary_path),
        "p50_lifecycle_sha256": _sha256(lifecycle_path),
        "p50_visits_sha256": _sha256(p50_dir / "entry_zone_visits.csv"),
    }
    return keys, provenance


def _load_exact_baseline(
    baseline_dir: Path,
    cohort_keys: set[tuple[str, str, int]],
) -> tuple[dict[tuple[str, str, int], ExactBaselineEvent], dict[str, Any], dict[str, float]]:
    summary_path = baseline_dir / "summary.json"
    events_path = baseline_dir / "event_results.csv"
    scope_path = baseline_dir / "scope_summary.csv"
    if not summary_path.is_file() or not events_path.is_file() or not scope_path.is_file():
        raise FileNotFoundError(f"Incomplete exact baseline directory: {baseline_dir}")

    summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    config = summary.get("config", {})
    guardrails = summary.get("guardrails", {})
    if config.get("target_pct") != 1.1 or config.get("stop_pct") != 1.0:
        raise ValueError("P51 exact baseline must be untouched +1.10 / -1.00")
    if config.get("horizon_hours") != 72 or guardrails.get("expected_all9") != EXPECTED_SIGNALS:
        raise ValueError("P51 exact baseline horizon/signal contract mismatch")

    all_events: dict[tuple[str, str, int], ExactBaselineEvent] = {}
    with events_path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            touch = _parse_dt(row["touch_at"])
            outcome_text = row["outcome"]
            if outcome_text not in {"reached_plus_1p10", "hit_minus_1p00", "data_end"}:
                raise ValueError(f"Unexpected exact baseline outcome: {outcome_text}")
            outcome = cast(BaselineOutcome, outcome_text)
            event_at = _parse_dt(row["event_at"]) if row.get("event_at") else None
            event = ExactBaselineEvent(
                symbol=row["symbol"],
                direction=row["direction"],
                touch_at=touch,
                outcome=outcome,
                event_at=event_at,
                complete_horizon=row.get("complete_horizon", "").strip().lower() == "true",
            )
            all_events[_signal_key(event.symbol, event.direction, event.touch_at)] = event
    if len(all_events) != EXPECTED_SIGNALS:
        raise ValueError(f"Exact baseline signal count mismatch: {len(all_events)}")

    cohort_events = {key: all_events[key] for key in cohort_keys if key in all_events}
    if len(cohort_events) != len(cohort_keys):
        raise ValueError("Exact baseline does not cover the complete P50 cohort")
    counts = defaultdict(int)
    for event in cohort_events.values():
        counts[event.outcome] += 1
    expected = {
        "reached_plus_1p10": EXPECTED_PLUS_110,
        "hit_minus_1p00": EXPECTED_MINUS_100_AFTER_ACTIVATION,
        "data_end": EXPECTED_DATA_END_AFTER_ACTIVATION,
    }
    if dict(counts) != expected:
        raise ValueError(f"P51 exact P50-cohort split mismatch: {dict(counts)} != {expected}")

    economics: dict[str, float] = {}
    with scope_path.open("r", newline="", encoding="utf-8-sig") as handle:
        all9 = next((row for row in csv.DictReader(handle) if row["scope"] == "ALL9"), None)
    if all9 is None:
        raise ValueError("ALL9 economics row missing from exact baseline")
    for key in (
        "illustrative_margin_usd",
        "illustrative_leverage",
        "illustrative_notional_usd",
        "illustrative_round_trip_cost_pct",
        "illustrative_win_net_usd",
        "illustrative_loss_net_usd",
        "illustrative_aggregate_net_usd",
    ):
        raw_value = all9.get(key)
        if raw_value is None:
            raise ValueError(f"Missing exact baseline economics field: {key}")
        economics[key] = float(raw_value)

    provenance = {
        "exact_baseline_dir": str(baseline_dir),
        "exact_baseline_summary_sha256": _sha256(summary_path),
        "exact_baseline_events_sha256": _sha256(events_path),
        "exact_baseline_scope_sha256": _sha256(scope_path),
        "cohort_plus_1p10": counts["reached_plus_1p10"],
        "cohort_minus_1p00": counts["hit_minus_1p00"],
        "cohort_data_end": counts["data_end"],
    }
    return cohort_events, provenance, economics


def _path_exact_outcome(path: PathSeries, config: P51Config) -> tuple[BaselineOutcome, int | None]:
    target_index = _first_at_or_above(path, 1.10)
    stop_index = _first_at_or_below(path, -config.initial_stop_pct)
    if target_index is not None and (stop_index is None or target_index < stop_index):
        return "reached_plus_1p10", target_index
    if stop_index is not None and (target_index is None or stop_index < target_index):
        return "hit_minus_1p00", stop_index
    return "data_end", None


def _validate_exact_equivalence(
    path: PathSeries,
    baseline: ExactBaselineEvent,
    config: P51Config,
) -> None:
    outcome, index = _path_exact_outcome(path, config)
    if outcome != baseline.outcome:
        raise ValueError(
            "P51 exact baseline/raw-path outcome mismatch for "
            f"{baseline.symbol} {baseline.touch_at.isoformat()}: {outcome} != {baseline.outcome}"
        )
    if index is not None and baseline.event_at is not None:
        raw_at = datetime.fromtimestamp(path.timestamps[index], UTC)
        delta_seconds = abs((raw_at - baseline.event_at).total_seconds())
        if delta_seconds > 1.0:
            raise ValueError(
                "P51 exact baseline/raw-path event time mismatch for "
                f"{baseline.symbol} {baseline.touch_at.isoformat()}"
            )


def _future_outcome(
    path: PathSeries,
    action_index: int,
    target: float,
    config: P51Config,
) -> tuple[FutureOutcome, int | None, int | None]:
    first_target = _first_at_or_above(path, target)
    if first_target is not None and first_target <= action_index:
        return "target_already_reached", first_target, None
    target_index = _first_at_or_above(path, target, action_index + 1)
    stop_index = _first_at_or_below(path, -config.initial_stop_pct, action_index + 1)
    if target_index is not None and (stop_index is None or target_index < stop_index):
        return "runner", target_index, stop_index
    if stop_index is not None and (target_index is None or stop_index < target_index):
        return "initial_stop", target_index, stop_index
    complete = path.complete_through >= path.signal.touch_at + timedelta(hours=config.horizon_hours)
    return ("horizon_nonrunner" if complete else "censored"), target_index, stop_index


def _new_tradeoff() -> dict[tuple[str, float, int, float, float], TradeoffCounts]:
    result: dict[tuple[str, float, int, float, float], TradeoffCounts] = {}
    for mode in ("conditional_recovery_no", "first_recovery_after_mfe"):
        numbers = ACTION_NUMBERS if mode == "conditional_recovery_no" else (0,)
        for milestone in DEFAULT_MFE_MILESTONES:
            for number in numbers:
                for stop in DEFAULT_STOPS:
                    for target in DEFAULT_TARGETS:
                        result[(mode, milestone, number, stop, target)] = TradeoffCounts()
    return result


def _new_room_samples() -> dict[tuple[float, float], list[float]]:
    return {
        (milestone, target): []
        for milestone in DEFAULT_MFE_MILESTONES
        for target in DEFAULT_TARGETS
    }


def _accumulate_tradeoff(
    *,
    path: PathSeries,
    action_index: int,
    mode: str,
    milestone: float,
    recovery_no: int,
    config: P51Config,
    tradeoff: dict[tuple[str, float, int, float, float], TradeoffCounts],
    room_samples: dict[tuple[float, float], list[float]] | None,
) -> None:
    number = recovery_no if mode == "conditional_recovery_no" else 0
    for target in config.continuation_targets_pct:
        outcome, target_index, initial_stop_index = _future_outcome(
            path, action_index, target, config
        )
        if outcome == "target_already_reached":
            continue
        if room_samples is not None and outcome == "runner" and target_index is not None:
            room_samples[(milestone, target)].append(
                _minimum_until(path, action_index + 1, target_index)
            )
        for stop in config.stop_candidates_pct:
            counts = tradeoff[(mode, milestone, number, stop, target)]
            counts.eligible += 1
            if outcome == "runner":
                counts.future_runners += 1
            elif outcome == "initial_stop":
                counts.future_initial_stop_losers += 1
            elif outcome == "horizon_nonrunner":
                counts.horizon_nonrunners += 1
            else:
                counts.censored += 1

            candidate_index = _first_at_or_below(path, stop, action_index + 1)
            if candidate_index is not None:
                counts.candidate_stop_exits += 1
            if (
                outcome == "runner"
                and target_index is not None
                and candidate_index is not None
                and candidate_index < target_index
            ):
                counts.lost_runners += 1
            elif (
                outcome == "initial_stop"
                and initial_stop_index is not None
                and candidate_index is not None
                and candidate_index < initial_stop_index
            ):
                counts.saved_losers += 1


def _build_tradeoff_rows(
    tradeoff: dict[tuple[str, float, int, float, float], TradeoffCounts]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in sorted(tradeoff):
        mode, milestone, number, stop, target = key
        counts = tradeoff[key]
        if counts.eligible == 0:
            continue
        rows.append(
            {
                "mode": mode,
                "mfe_milestone_pct": milestone,
                "recovery_no": "FIRST" if mode == "first_recovery_after_mfe" else number,
                "stop_pct": stop,
                "target_pct": target,
                **asdict(counts),
                "runner_retention_pct": _pct(
                    counts.future_runners - counts.lost_runners, counts.future_runners
                ),
                "runner_lost_pct": _pct(counts.lost_runners, counts.future_runners),
                "loser_saved_pct": _pct(counts.saved_losers, counts.future_initial_stop_losers),
            }
        )
    return rows


def _build_room_rows(room_samples: dict[tuple[float, float], list[float]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for milestone, target in sorted(room_samples):
        values = room_samples[(milestone, target)]
        rows.append(
            {
                "mfe_milestone_pct": milestone,
                "target_pct": target,
                "future_runners": len(values),
                "required_room_min_pct": min(values) if values else None,
                "required_room_p10_pct": _quantile(values, 0.10),
                "required_room_p25_pct": _quantile(values, 0.25),
                "required_room_median_pct": _median(values),
                "survive_stop_m0p75": sum(value > -0.75 for value in values),
                "survive_stop_m0p60": sum(value > -0.60 for value in values),
                "survive_stop_m0p50": sum(value > -0.50 for value in values),
                "survive_stop_m0p75_pct": _pct(sum(value > -0.75 for value in values), len(values)),
                "survive_stop_m0p60_pct": _pct(sum(value > -0.60 for value in values), len(values)),
                "survive_stop_m0p50_pct": _pct(sum(value > -0.50 for value in values), len(values)),
            }
        )
    return rows


def _scope_labels(symbol: str, direction: str) -> tuple[str, str, str]:
    return "ALL9", f"DIRECTION:{direction}", f"SYMBOL:{symbol}"


def _candidate_net_usd(stop: float, economics: dict[str, float]) -> float:
    notional = economics["illustrative_notional_usd"]
    cost_pct = economics["illustrative_round_trip_cost_pct"]
    gross = notional * stop / 100.0
    cost = notional * cost_pct / 100.0
    return gross - cost


def _build_economic_rows(
    first_rule_rows: list[FirstRuleSignalRow], economics: dict[str, float]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    aggregate: dict[tuple[str, float, float], dict[str, float]] = {}
    for row in first_rule_rows:
        for scope in _scope_labels(row.symbol, row.direction):
            key = scope, row.mfe_milestone_pct, row.stop_pct
            bucket = aggregate.setdefault(
                key,
                {
                    "signals_with_rule_action": 0.0,
                    "actions_before_baseline_outcome": 0.0,
                    "baseline_winners_acted": 0.0,
                    "baseline_losers_acted": 0.0,
                    "saved_baseline_losers": 0.0,
                    "killed_baseline_winners": 0.0,
                    "candidate_stop_before_baseline_outcome": 0.0,
                    "illustrative_delta_usd": 0.0,
                },
            )
            bucket["signals_with_rule_action"] += float(row.action_visit_no is not None)
            bucket["actions_before_baseline_outcome"] += float(row.acted_before_baseline_outcome)
            bucket["baseline_winners_acted"] += float(
                row.acted_before_baseline_outcome and row.baseline_outcome == "reached_plus_1p10"
            )
            bucket["baseline_losers_acted"] += float(
                row.acted_before_baseline_outcome and row.baseline_outcome == "hit_minus_1p00"
            )
            bucket["saved_baseline_losers"] += float(row.saved_baseline_loser)
            bucket["killed_baseline_winners"] += float(row.killed_baseline_winner)
            bucket["candidate_stop_before_baseline_outcome"] += float(
                row.candidate_stop_before_baseline_outcome
            )
            bucket["illustrative_delta_usd"] += row.illustrative_delta_usd

    rows: list[dict[str, Any]] = []
    for (scope, milestone, stop), bucket in sorted(aggregate.items()):
        delta = round(bucket["illustrative_delta_usd"], 6)
        row = {
            "scope": scope,
            "mfe_milestone_pct": milestone,
            "stop_pct": stop,
            **{key: int(value) for key, value in bucket.items() if key != "illustrative_delta_usd"},
            "illustrative_delta_usd": delta,
        }
        if scope == "ALL9":
            row["illustrative_all9_baseline_net_usd"] = economics["illustrative_aggregate_net_usd"]
            row["illustrative_all9_net_after_rule_usd"] = round(
                economics["illustrative_aggregate_net_usd"] + delta, 6
            )
        rows.append(row)
    return [row for row in rows if row["scope"] == "ALL9"], rows


def _bin_loser_mfe(value: float) -> str:
    if value < 0.25:
        return "0.10_to_0.25"
    if value < 0.50:
        return "0.25_to_0.50"
    if value < 0.75:
        return "0.50_to_0.75"
    if value < 1.00:
        return "0.75_to_1.00"
    return "1.00_to_1.10"


def _new_giveback() -> dict[float, dict[str, Any]]:
    return {
        milestone: {
            "reached": 0,
            "returned_to_entry": 0,
            "first_post_mfe_visit_recovered": 0,
            "first_post_mfe_visit_initial_stop": 0,
            "first_post_mfe_visit_unresolved": 0,
            "first_post_mfe_visit_lows": [],
            "givebacks": [],
            "seconds_mfe_to_visit": [],
            "seconds_visit_to_resolution": [],
        }
        for milestone in DEFAULT_MFE_MILESTONES
    }


def _build_giveback_rows(giveback: dict[float, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for milestone in DEFAULT_MFE_MILESTONES:
        bucket = giveback[milestone]
        reached = int(bucket["reached"])
        returned = int(bucket["returned_to_entry"])
        lows = list(bucket["first_post_mfe_visit_lows"])
        givebacks = list(bucket["givebacks"])
        seconds_to_visit = list(bucket["seconds_mfe_to_visit"])
        resolution_seconds = list(bucket["seconds_visit_to_resolution"])
        rows.append(
            {
                "mfe_milestone_pct": milestone,
                "signals_reached_mfe": reached,
                "returned_to_entry_after_mfe": returned,
                "returned_to_entry_pct": _pct(returned, reached),
                "first_post_mfe_visit_recovered": int(bucket["first_post_mfe_visit_recovered"]),
                "first_post_mfe_visit_initial_stop": int(
                    bucket["first_post_mfe_visit_initial_stop"]
                ),
                "first_post_mfe_visit_unresolved": int(bucket["first_post_mfe_visit_unresolved"]),
                "first_post_mfe_visit_low_median_pct": _median(lows),
                "first_post_mfe_visit_low_p25_pct": _quantile(lows, 0.25),
                "giveback_from_cumulative_mfe_median_pct": _median(givebacks),
                "seconds_mfe_to_first_entry_visit_median": _median(seconds_to_visit),
                "seconds_first_visit_to_resolution_median": _median(resolution_seconds),
            }
        )
    return rows


def _checkpoint_payload(
    *,
    fingerprint: str,
    processed: int,
    total: int,
    tradeoff: dict[tuple[str, float, int, float, float], TradeoffCounts],
    room_samples: dict[tuple[float, float], list[float]],
    first_rule_rows: list[FirstRuleSignalRow],
    giveback: dict[float, dict[str, Any]],
    loser_mfe_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    tradeoff_rows = [
        {
            "mode": key[0],
            "milestone": key[1],
            "number": key[2],
            "stop": key[3],
            "target": key[4],
            **asdict(value),
        }
        for key, value in tradeoff.items()
    ]
    rooms = [
        {"milestone": key[0], "target": key[1], "values": value}
        for key, value in room_samples.items()
    ]
    return {
        "checkpoint_version": CHECKPOINT_VERSION,
        "fingerprint": fingerprint,
        "processed": processed,
        "total": total,
        "tradeoff": tradeoff_rows,
        "rooms": rooms,
        "first_rule_rows": [asdict(row) for row in first_rule_rows],
        "giveback": {str(key): value for key, value in giveback.items()},
        "loser_mfe_rows": loser_mfe_rows,
    }


def _write_checkpoint(path: Path, **kwargs: Any) -> None:
    payload = _checkpoint_payload(**kwargs)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def _load_checkpoint(
    path: Path,
    *,
    fingerprint: str,
    total: int,
) -> tuple[
    int,
    dict[tuple[str, float, int, float, float], TradeoffCounts],
    dict[tuple[float, float], list[float]],
    list[FirstRuleSignalRow],
    dict[float, dict[str, Any]],
    list[dict[str, Any]],
]:
    if not path.is_file():
        return 0, _new_tradeoff(), _new_room_samples(), [], _new_giveback(), []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("checkpoint_version") != CHECKPOINT_VERSION:
        raise ValueError("P51 checkpoint version mismatch")
    if payload.get("fingerprint") != fingerprint or payload.get("total") != total:
        raise ValueError("P51 checkpoint fingerprint mismatch; use a clean output directory")

    tradeoff = _new_tradeoff()
    for row in payload.get("tradeoff", []):
        key = (
            str(row["mode"]),
            float(row["milestone"]),
            int(row["number"]),
            float(row["stop"]),
            float(row["target"]),
        )
        tradeoff[key] = TradeoffCounts(
            eligible=int(row["eligible"]),
            future_runners=int(row["future_runners"]),
            future_initial_stop_losers=int(row["future_initial_stop_losers"]),
            horizon_nonrunners=int(row["horizon_nonrunners"]),
            censored=int(row["censored"]),
            lost_runners=int(row["lost_runners"]),
            saved_losers=int(row["saved_losers"]),
            candidate_stop_exits=int(row["candidate_stop_exits"]),
        )

    rooms = _new_room_samples()
    for row in payload.get("rooms", []):
        rooms[(float(row["milestone"]), float(row["target"]))] = [
            float(value) for value in row["values"]
        ]
    first_rule_rows = [FirstRuleSignalRow(**row) for row in payload.get("first_rule_rows", [])]
    giveback = _new_giveback()
    for key, value in payload.get("giveback", {}).items():
        giveback[float(key)] = value
    loser_mfe_rows = [dict(row) for row in payload.get("loser_mfe_rows", [])]
    return int(payload["processed"]), tradeoff, rooms, first_rule_rows, giveback, loser_mfe_rows


def _fingerprint(
    config: P51Config,
    p50_provenance: dict[str, Any],
    baseline_provenance: dict[str, Any],
    source_provenance: list[dict[str, str]],
) -> str:
    payload = {
        "version": P51_VERSION,
        "config": asdict(config),
        "p50": p50_provenance,
        "baseline": baseline_provenance,
        "sources": source_provenance,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _first_rule_economic_row(
    *,
    path: PathSeries,
    baseline: ExactBaselineEvent,
    milestone: float,
    action_visit_no: int | None,
    action_index: int | None,
    cumulative_mfe: float | None,
    visit_low: float | None,
    zero_crossings: int | None,
    stop: float,
    economics: dict[str, float],
) -> FirstRuleSignalRow:
    acted = action_index is not None and (
        baseline.event_at is None
        or datetime.fromtimestamp(path.timestamps[action_index], UTC) < baseline.event_at
    )
    candidate_before = False
    saved = False
    killed = False
    delta = 0.0
    if acted and action_index is not None:
        candidate_index = _first_at_or_below(path, stop, action_index + 1)
        candidate_at = (
            datetime.fromtimestamp(path.timestamps[candidate_index], UTC)
            if candidate_index is not None
            else None
        )
        candidate_before = candidate_at is not None and (
            baseline.event_at is None or candidate_at < baseline.event_at
        )
        if candidate_before and baseline.outcome == "hit_minus_1p00":
            saved = True
            delta = _candidate_net_usd(stop, economics) - economics["illustrative_loss_net_usd"]
        elif candidate_before and baseline.outcome == "reached_plus_1p10":
            killed = True
            delta = _candidate_net_usd(stop, economics) - economics["illustrative_win_net_usd"]

    action_at = (
        datetime.fromtimestamp(path.timestamps[action_index], UTC).isoformat()
        if action_index is not None
        else None
    )
    return FirstRuleSignalRow(
        symbol=baseline.symbol,
        direction=baseline.direction,
        touch_at=baseline.touch_at.isoformat(),
        mfe_milestone_pct=milestone,
        action_visit_no=action_visit_no,
        action_at=action_at,
        cumulative_mfe_before_visit_pct=cumulative_mfe,
        action_visit_low_pct=visit_low,
        action_zero_crossings=zero_crossings,
        stop_pct=stop,
        baseline_outcome=baseline.outcome,
        baseline_event_at=baseline.event_at.isoformat() if baseline.event_at else None,
        acted_before_baseline_outcome=acted,
        candidate_stop_before_baseline_outcome=candidate_before,
        saved_baseline_loser=saved,
        killed_baseline_winner=killed,
        illustrative_delta_usd=round(delta, 6),
    )


def _process_signal(
    *,
    path: PathSeries,
    baseline: ExactBaselineEvent,
    config: P51Config,
    economics: dict[str, float],
    tradeoff: dict[tuple[str, float, int, float, float], TradeoffCounts],
    room_samples: dict[tuple[float, float], list[float]],
    first_rule_rows: list[FirstRuleSignalRow],
    giveback: dict[float, dict[str, Any]],
    loser_mfe_rows: list[dict[str, Any]],
) -> None:
    _validate_exact_equivalence(path, baseline, config)
    p50_config = P50Config(
        activation_pct=config.activation_pct,
        initial_stop_pct=config.initial_stop_pct,
        horizon_hours=config.horizon_hours,
        day_cache_size=config.day_cache_size,
        progress_interval_seconds=config.progress_interval_seconds,
        expected_signals=config.expected_signals,
        expected_cohort=config.expected_cohort,
    )
    visits = analyze_entry_visits(path, p50_config)

    recovered_actions: list[tuple[int, int, float, float, int]] = []
    cumulative_mfe = config.activation_pct
    for event, recovery_index in visits:
        cumulative_mfe = max(cumulative_mfe, event.pre_visit_peak_pct)
        if event.status == "recovered_plus_0p10" and recovery_index is not None:
            recovered_actions.append(
                (
                    event.visit_no,
                    recovery_index,
                    cumulative_mfe,
                    event.low_pct,
                    event.zero_crossings_in_visit,
                )
            )
            if event.visit_no in ACTION_NUMBERS:
                for milestone in config.mfe_milestones_pct:
                    if cumulative_mfe >= milestone:
                        _accumulate_tradeoff(
                            path=path,
                            action_index=recovery_index,
                            mode="conditional_recovery_no",
                            milestone=milestone,
                            recovery_no=event.visit_no,
                            config=config,
                            tradeoff=tradeoff,
                            room_samples=None,
                        )

    for milestone in config.mfe_milestones_pct:
        first_action = next(
            (item for item in recovered_actions if item[2] >= milestone),
            None,
        )
        visit_no: int | None
        action_index: int | None
        action_mfe: float | None
        visit_low: float | None
        zero_crossings: int | None
        if first_action is not None:
            visit_no, action_index, action_mfe, visit_low, zero_crossings = first_action
            _accumulate_tradeoff(
                path=path,
                action_index=action_index,
                mode="first_recovery_after_mfe",
                milestone=milestone,
                recovery_no=visit_no,
                config=config,
                tradeoff=tradeoff,
                room_samples=room_samples,
            )
        else:
            visit_no = None
            action_index = None
            action_mfe = None
            visit_low = None
            zero_crossings = None
        for stop in config.stop_candidates_pct:
            first_rule_rows.append(
                _first_rule_economic_row(
                    path=path,
                    baseline=baseline,
                    milestone=milestone,
                    action_visit_no=visit_no,
                    action_index=action_index,
                    cumulative_mfe=action_mfe,
                    visit_low=visit_low,
                    zero_crossings=zero_crossings,
                    stop=stop,
                    economics=economics,
                )
            )

    initial_stop_index = _first_at_or_below(path, -config.initial_stop_pct)
    for milestone in config.mfe_milestones_pct:
        milestone_index = _first_at_or_above(path, milestone)
        if milestone_index is None or (
            initial_stop_index is not None and initial_stop_index < milestone_index
        ):
            continue
        bucket = giveback[milestone]
        bucket["reached"] = int(bucket["reached"]) + 1
        first_visit: tuple[Any, int | None] | None = None
        for event, recovery_index in visits:
            start_index = _index_at_or_after(path, _parse_dt(event.started_at))
            if start_index > milestone_index:
                first_visit = event, recovery_index
                break
        if first_visit is None:
            continue
        event, _ = first_visit
        bucket["returned_to_entry"] = int(bucket["returned_to_entry"]) + 1
        status = event.status.replace("recovered_plus_0p10", "recovered")
        status_key = f"first_post_mfe_visit_{status}"
        bucket[status_key] = int(bucket[status_key]) + 1
        bucket["first_post_mfe_visit_lows"].append(event.low_pct)
        visit_start_index = _index_at_or_after(path, _parse_dt(event.started_at))
        cumulative_peak_before_visit = max(
            path.moves_pct[milestone_index : visit_start_index + 1]
        )
        bucket["givebacks"].append(cumulative_peak_before_visit - event.low_pct)
        bucket["seconds_mfe_to_visit"].append(
            max(0.0, _parse_dt(event.started_at).timestamp() - path.timestamps[milestone_index])
        )
        if event.seconds_to_resolution is not None:
            bucket["seconds_visit_to_resolution"].append(event.seconds_to_resolution)

    if baseline.outcome == "hit_minus_1p00" and baseline.event_at is not None:
        stop_index = _index_at_or_after(path, baseline.event_at)
        stop_index = min(stop_index, len(path.moves_pct) - 1)
        mfe_before_stop = max(path.moves_pct[: stop_index + 1])
        loser_mfe_rows.append(
            {
                "symbol": baseline.symbol,
                "direction": baseline.direction,
                "touch_at": baseline.touch_at.isoformat(),
                "minus_1_at": baseline.event_at.isoformat(),
                "mfe_before_minus_1_pct": round(mfe_before_stop, 6),
                "mfe_bin": _bin_loser_mfe(mfe_before_stop),
            }
        )


def _build_loser_mfe_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = (
        "0.10_to_0.25",
        "0.25_to_0.50",
        "0.50_to_0.75",
        "0.75_to_1.00",
        "1.00_to_1.10",
    )
    result: list[dict[str, Any]] = []
    scopes = {
        "ALL9": rows,
        "LONG": [row for row in rows if row["direction"] == "Long"],
        "SHORT": [row for row in rows if row["direction"] == "Short"],
    }
    for scope, subset in scopes.items():
        for bin_name in order:
            count = sum(row["mfe_bin"] == bin_name for row in subset)
            result.append(
                {
                    "scope": scope,
                    "mfe_bin": bin_name,
                    "count": count,
                    "pct_of_scope_losers": _pct(count, len(subset)),
                }
            )
    return result


def _write_summary_md(path: Path, summary: dict[str, Any]) -> None:
    baseline = summary["exact_baseline"]
    lines = [
        "# P51 — MFE-Activated Risk / Giveback Anatomy",
        "",
        f"Generated: `{summary['generated_at']}`",
        "",
        f"- Cohort: **{summary['cohort']}** (+0.10 before original -1.00)",
        f"- Exact +1.10 first: **{baseline['cohort_plus_1p10']}**",
        f"- Exact -1.00 first after activation: **{baseline['cohort_minus_1p00']}**",
        f"- Data end: **{baseline['cohort_data_end']}**",
        "- MFE milestones: **+0.25 / +0.50 / +0.75 / +1.00%**",
        "- Candidate tightened stops: **-0.75 / -0.60 / -0.50%**",
        "- Future targets: **+1.10 / +2.00 / +3.00%**",
        "- Rule action: first causal Entry recovery (+0.10) after the MFE milestone "
        "was already reached.",
        "- Reserved BNB/AVAX/SUI/AAVE/LTC OOS: **NOT TOUCHED**",
        "- Production Entry / Exit / Risk: **UNCHANGED**",
        "",
        "This is discovery anatomy, not a production stop selection.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_research(
    root: Path,
    p50_dir: Path,
    exact_baseline_dir: Path | None,
    output_dir: Path,
    config: P51Config,
) -> dict[str, Any]:
    cohort_keys, p50_provenance = _load_p50_cohort(p50_dir, config)
    baseline_dir = _discover_exact_baseline_dir(root, exact_baseline_dir)
    baseline_events, baseline_provenance, economics = _load_exact_baseline(
        baseline_dir, cohort_keys
    )

    sources = discover_sources(root)
    all_signals = load_all_signals(sources)
    if len(all_signals) != config.expected_signals:
        raise ValueError(f"P51 frozen Entry signal count mismatch: {len(all_signals)}")
    selected = [signal for signal in all_signals if _signal_key_from_signal(signal) in cohort_keys]
    if len(selected) != config.expected_cohort:
        raise ValueError(f"P51 selected cohort mismatch: {len(selected)}")

    source_by_symbol = {source.symbol: source for source in sources}
    archives = {
        symbol: _archive_map(source_by_symbol[symbol].dataset_dir) for symbol in ALL_SYMBOLS
    }
    source_provenance = [
        {
            "symbol": source.symbol,
            "features_sha256": _sha256(source.features_path),
            "summary_sha256": _sha256(source.summary_path),
            "dataset_manifest_sha256": _sha256(source.dataset_dir / "dataset_manifest.json"),
        }
        for source in sources
    ]
    fingerprint = _fingerprint(config, p50_provenance, baseline_provenance, source_provenance)

    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = output_dir / "checkpoint.json"
    processed, tradeoff, rooms, first_rule_rows, giveback, loser_mfe_rows = _load_checkpoint(
        checkpoint,
        fingerprint=fingerprint,
        total=len(selected),
    )

    signals_by_symbol: dict[str, list[CoreSignal]] = {symbol: [] for symbol in ALL_SYMBOLS}
    for signal in selected:
        signals_by_symbol[signal.symbol].append(signal)

    heartbeat = Heartbeat(len(selected), config.progress_interval_seconds)
    heartbeat.update(processed, "resume from checkpoint" if processed else "MFE/giveback anatomy")
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
                    f"symbol={symbol} signal={signal_no}/{len(symbol_signals)} stage=analyze_mfe",
                )
                baseline = baseline_events[_signal_key_from_signal(signal)]
                _process_signal(
                    path=path,
                    baseline=baseline,
                    config=config,
                    economics=economics,
                    tradeoff=tradeoff,
                    room_samples=rooms,
                    first_rule_rows=first_rule_rows,
                    giveback=giveback,
                    loser_mfe_rows=loser_mfe_rows,
                )
                processed += 1
                symbol_done += 1
                heartbeat.update(
                    processed,
                    f"symbol={symbol} signal={signal_no}/{len(symbol_signals)} stage=done",
                )
                del path
                if processed % CHECKPOINT_INTERVAL_SIGNALS == 0:
                    _write_checkpoint(
                        checkpoint,
                        fingerprint=fingerprint,
                        processed=processed,
                        total=len(selected),
                        tradeoff=tradeoff,
                        room_samples=rooms,
                        first_rule_rows=first_rule_rows,
                        giveback=giveback,
                        loser_mfe_rows=loser_mfe_rows,
                    )
            _write_checkpoint(
                checkpoint,
                fingerprint=fingerprint,
                processed=processed,
                total=len(selected),
                tradeoff=tradeoff,
                room_samples=rooms,
                first_rule_rows=first_rule_rows,
                giveback=giveback,
                loser_mfe_rows=loser_mfe_rows,
            )
            heartbeat.update(processed, f"symbol={symbol} complete checkpoint=saved")
            heartbeat.emit(force=True)
            del cache
    finally:
        heartbeat.close()

    if processed != len(selected):
        raise RuntimeError(f"P51 processed mismatch: {processed} != {len(selected)}")
    if len(loser_mfe_rows) != EXPECTED_MINUS_100_AFTER_ACTIVATION:
        raise RuntimeError(
            "P51 exact loser anatomy mismatch: "
            f"{len(loser_mfe_rows)} != {EXPECTED_MINUS_100_AFTER_ACTIVATION}"
        )

    tradeoff_rows = _build_tradeoff_rows(tradeoff)
    first_tradeoff_rows = [
        row for row in tradeoff_rows if row["mode"] == "first_recovery_after_mfe"
    ]
    conditional_rows = [row for row in tradeoff_rows if row["mode"] == "conditional_recovery_no"]
    room_rows = _build_room_rows(rooms)
    economics_rows, stability_rows = _build_economic_rows(first_rule_rows, economics)
    giveback_rows = _build_giveback_rows(giveback)
    loser_summary = _build_loser_mfe_summary(loser_mfe_rows)

    summary: dict[str, Any] = {
        "research_version": P51_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "period": PERIOD_TAG,
        "symbols": list(ALL_SYMBOLS),
        "cohort_definition": "+0.10 before original -1.00 initial stop",
        "cohort": len(selected),
        "config": asdict(config),
        "downloads": "DISABLED",
        "entry_v1_changed": False,
        "p46_changed": False,
        "live_execution_changed": False,
        "exit_risk_production_changed": False,
        "reserved_five_oos_assets_touched": False,
        "p50_provenance": p50_provenance,
        "exact_baseline": baseline_provenance,
        "illustrative_economics": economics,
        "source_provenance": source_provenance,
        "notes": [
            "Fixed discovery grid only; P51 does not optimize thresholds.",
            "A rule acts only after a full Entry visit recovered causally to +0.10 "
            "after the MFE milestone had already been reached.",
            "The exact +1.10/-1.00 baseline is reconciled signal-by-signal against "
            "the raw path before aggregation.",
            "Illustrative economics exclude slippage, funding and portfolio concurrency.",
        ],
    }

    _write_csv(output_dir / "first_recovery_after_mfe_tradeoff.csv", first_tradeoff_rows)
    _write_csv(output_dir / "conditional_recovery_number_tradeoff.csv", conditional_rows)
    _write_csv(output_dir / "runner_required_room_after_mfe_recovery.csv", room_rows)
    _write_csv(
        output_dir / "first_rule_signal_economics.csv",
        [asdict(row) for row in first_rule_rows],
    )
    _write_csv(output_dir / "exact_plus110_economic_tradeoff.csv", economics_rows)
    _write_csv(output_dir / "rule_stability_by_symbol_direction.csv", stability_rows)
    _write_csv(output_dir / "giveback_anatomy_by_mfe.csv", giveback_rows)
    _write_csv(output_dir / "loser_mfe_before_minus1.csv", loser_mfe_rows)
    _write_csv(output_dir / "loser_mfe_before_minus1_summary.csv", loser_summary)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _write_summary_md(output_dir / "summary.md", summary)
    _write_checkpoint(
        checkpoint,
        fingerprint=fingerprint,
        processed=processed,
        total=len(selected),
        tradeoff=tradeoff,
        room_samples=rooms,
        first_rule_rows=first_rule_rows,
        giveback=giveback,
        loser_mfe_rows=loser_mfe_rows,
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="P51 MFE-activated risk / giveback anatomy")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--p50-dir", type=Path)
    parser.add_argument("--exact-baseline-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--mfe-milestones-pct", default="0.25,0.50,0.75,1.00")
    parser.add_argument("--stop-candidates-pct", default="-0.75,-0.60,-0.50")
    parser.add_argument("--continuation-targets-pct", default="1.10,2.00,3.00")
    parser.add_argument("--horizon-hours", type=int, default=72)
    parser.add_argument("--day-cache-size", type=int, default=4)
    parser.add_argument("--progress-interval-seconds", type=float, default=25.0)
    args = parser.parse_args()

    def parse_tuple(raw: str) -> tuple[float, ...]:
        return tuple(float(item.strip()) for item in raw.split(",") if item.strip())

    config = P51Config(
        mfe_milestones_pct=parse_tuple(args.mfe_milestones_pct),
        stop_candidates_pct=parse_tuple(args.stop_candidates_pct),
        continuation_targets_pct=parse_tuple(args.continuation_targets_pct),
        horizon_hours=args.horizon_hours,
        day_cache_size=args.day_cache_size,
        progress_interval_seconds=args.progress_interval_seconds,
    )
    root = args.root.resolve()
    p50_dir = args.p50_dir or (
        root / "reports" / "multi_retest_entry_recross_p50" / "ALL9_P50_WORKING"
    )
    output_dir = args.output_dir or (
        root / "reports" / "mfe_activated_risk_p51" / "ALL9_P51_WORKING"
    )
    run_research(root, p50_dir.resolve(), args.exact_baseline_dir, output_dir.resolve(), config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
