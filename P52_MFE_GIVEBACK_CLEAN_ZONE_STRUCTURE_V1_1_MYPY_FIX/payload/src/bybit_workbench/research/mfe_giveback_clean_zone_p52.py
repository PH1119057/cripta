from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from bybit_workbench.research.exit_break_even_v13 import PathSeries, TradeDayCache
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
from bybit_workbench.research.mfe_activated_risk_p51 import (
    DEFAULT_MFE_MILESTONES,
    DEFAULT_STOPS,
    DEFAULT_TARGETS,
    EXPECTED_MINUS_100_AFTER_ACTIVATION,
    EXPECTED_PLUS_110,
    ExactBaselineEvent,
    Heartbeat,
    P51Config,
    _candidate_net_usd,
    _discover_exact_baseline_dir,
    _first_at_or_above,
    _first_at_or_below,
    _index_at_or_after,
    _load_exact_baseline,
    _load_p50_cohort,
    _parse_dt,
    _pct,
    _signal_key,
    _signal_key_from_signal,
    _validate_exact_equivalence,
    _write_csv,
)

P52_VERSION = "P52_MFE_GIVEBACK_CLEAN_ZONE_STRUCTURE_V1"
EARLY_STRUCTURE_MINUTES = 60
EXPECTED_RESOLVED_BASELINE = EXPECTED_PLUS_110 + EXPECTED_MINUS_100_AFTER_ACTIVATION
EXPECTED_RUNNER_PLUS_2 = 416
EXPECTED_RUNNER_PLUS_3 = 289
SMALL_SAMPLE_N = 20

StructureState = Literal[
    "protective_hold_reclaim",
    "protective_clean_break_against",
    "obstacle_rejection_against",
    "obstacle_clean_break_with",
]
StructureSign = Literal["favorable", "adverse"]


@dataclass(frozen=True, slots=True)
class P52Config:
    activation_pct: float = 0.10
    initial_stop_pct: float = 1.00
    mfe_milestones_pct: tuple[float, ...] = DEFAULT_MFE_MILESTONES
    stop_candidates_pct: tuple[float, ...] = DEFAULT_STOPS
    continuation_targets_pct: tuple[float, ...] = DEFAULT_TARGETS
    horizon_hours: int = 72
    day_cache_size: int = 4
    progress_interval_seconds: float = 25.0
    early_structure_minutes: int = EARLY_STRUCTURE_MINUTES
    expected_signals: int = EXPECTED_SIGNALS

    def __post_init__(self) -> None:
        if self.activation_pct != 0.10 or self.initial_stop_pct != 1.00:
            raise ValueError("P52 V1 exact baseline is frozen at +0.10 activation / -1.00 stop")
        if self.mfe_milestones_pct != DEFAULT_MFE_MILESTONES:
            raise ValueError("P52 V1 MFE milestones are frozen")
        if self.stop_candidates_pct != DEFAULT_STOPS:
            raise ValueError("P52 V1 stop candidates are frozen")
        if self.continuation_targets_pct != DEFAULT_TARGETS:
            raise ValueError("P52 V1 continuation targets are frozen")
        if self.horizon_hours != 72:
            raise ValueError("P52 V1 horizon is frozen at 72 hours")
        if self.early_structure_minutes != 60:
            raise ValueError("P52 V1 early structure window is frozen at 60 minutes")
        if self.day_cache_size <= 0 or self.progress_interval_seconds <= 0:
            raise ValueError("cache size and heartbeat must be positive")


@dataclass(frozen=True, slots=True)
class ZoneEvent:
    symbol: str
    phase_id: int
    chain_id: int
    role: str
    event_at: datetime
    outcome: str
    outcome_at: datetime


@dataclass(frozen=True, slots=True)
class EntryZoneRef:
    symbol: str
    direction: str
    touch_at: datetime
    phase_id: int | None
    chain_id: int | None
    phase_role: str | None


@dataclass(frozen=True, slots=True)
class SignalFacts:
    symbol: str
    direction: str
    touch_at: datetime
    activation_at: datetime
    baseline: ExactBaselineEvent
    plus2_before_minus1: bool
    plus3_before_minus1: bool


def classify_structure(
    direction: str, role: str, outcome: str
) -> tuple[StructureState, StructureSign]:
    d = direction.strip().lower()
    r = role.strip().lower()
    o = outcome.strip().lower()
    if d not in {"long", "short"}:
        raise ValueError(f"Unexpected direction: {direction}")
    if r not in {"support", "resistance"}:
        raise ValueError(f"Unexpected zone role: {role}")
    if o not in {"bounce", "false_break_reclaim", "clean_break"}:
        raise ValueError(f"Unexpected resolved zone outcome: {outcome}")

    protective = r == ("support" if d == "long" else "resistance")
    if protective:
        if o == "clean_break":
            return "protective_clean_break_against", "adverse"
        return "protective_hold_reclaim", "favorable"
    if o == "clean_break":
        return "obstacle_clean_break_with", "favorable"
    return "obstacle_rejection_against", "adverse"


def _dt_or_none(value: str | None) -> datetime | None:
    text = "" if value is None else value.strip()
    return None if not text else _parse_dt(text)


def _int_or_none(value: str | None) -> int | None:
    text = "" if value is None else value.strip()
    return None if not text else int(text)


def _discover_p45_dir(root: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        candidate = explicit.resolve()
    else:
        candidate = root / "reports" / "clean_zone_lifecycle_p451" / f"ENTRY_V1_{PERIOD_TAG}"
    required = (
        candidate / "summary.json",
        candidate / "independent_zone_touch_outcomes.csv",
        candidate / "core_lifecycle_features.csv",
    )
    if not all(path.is_file() for path in required):
        raise FileNotFoundError(f"Completed P45.1 report not found: {candidate}")
    return candidate


def _validate_p45_contract(p45_dir: Path) -> dict[str, Any]:
    summary_path = p45_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    frozen = summary.get("frozen_parameters", summary.get("config", {}))
    if int(summary.get("core_signals", 0)) != EXPECTED_SIGNALS:
        raise ValueError("P52 requires P45.1 complete ALL9 1063-signal report")
    if int(summary.get("independent_zone_touches", 0)) <= 0:
        raise ValueError("P45.1 has no independent zone touch outcomes")
    break_closes = frozen.get("break_confirm_closes")
    horizon_bars = frozen.get("touch_outcome_horizon_bars")
    if break_closes is not None and int(break_closes) != 2:
        raise ValueError("P52 requires P45.1 clean_break = 2 confirming closes")
    if horizon_bars is not None and int(horizon_bars) != 96:
        raise ValueError("P52 requires P45.1 96x15m touch outcome horizon")
    return {
        "p45_dir": str(p45_dir),
        "p45_summary_sha256": _sha256(summary_path),
        "p45_touch_events_sha256": _sha256(p45_dir / "independent_zone_touch_outcomes.csv"),
        "p45_core_features_sha256": _sha256(p45_dir / "core_lifecycle_features.csv"),
        "independent_zone_touches": int(summary.get("independent_zone_touches", 0)),
    }


def _load_zone_events(p45_dir: Path) -> dict[str, list[ZoneEvent]]:
    by_symbol: defaultdict[str, list[ZoneEvent]] = defaultdict(list)
    path = p45_dir / "independent_zone_touch_outcomes.csv"
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            outcome = row.get("outcome", "").strip()
            outcome_at = _dt_or_none(row.get("outcome_at"))
            if (
                outcome not in {"bounce", "false_break_reclaim", "clean_break"}
                or outcome_at is None
            ):
                continue
            event_at = _parse_dt(row["event_at"])
            if outcome_at < event_at:
                raise ValueError("P45.1 event has outcome_at before event_at")
            event = ZoneEvent(
                symbol=row["symbol"],
                phase_id=int(row["phase_id"]),
                chain_id=int(row["chain_id"]),
                role=row["role"],
                event_at=event_at,
                outcome=outcome,
                outcome_at=outcome_at,
            )
            by_symbol[event.symbol].append(event)
    for events in by_symbol.values():
        events.sort(key=lambda item: (item.outcome_at, item.event_at, item.phase_id))
    if not by_symbol:
        raise ValueError("No resolved P45.1 zone events loaded")
    return dict(by_symbol)


def _load_entry_zone_refs(p45_dir: Path) -> dict[tuple[str, str, int], EntryZoneRef]:
    result: dict[tuple[str, str, int], EntryZoneRef] = {}
    with (p45_dir / "core_lifecycle_features.csv").open(
        "r", newline="", encoding="utf-8-sig"
    ) as handle:
        for row in csv.DictReader(handle):
            touch_at = _parse_dt(row["touch_at"])
            item = EntryZoneRef(
                symbol=row["symbol"],
                direction=row["direction"],
                touch_at=touch_at,
                phase_id=_int_or_none(row.get("phase_id")),
                chain_id=_int_or_none(row.get("chain_id")),
                phase_role=(row.get("phase_role") or "").strip() or None,
            )
            result[_signal_key(item.symbol, item.direction, item.touch_at)] = item
    if len(result) != EXPECTED_SIGNALS:
        raise ValueError(f"P45.1 core feature signal count mismatch: {len(result)}")
    return result


def _baseline_limit(baseline: ExactBaselineEvent, fallback: datetime) -> datetime:
    return fallback if baseline.event_at is None else baseline.event_at


def causal_events_for_signal(
    events: list[ZoneEvent],
    *,
    activation_at: datetime,
    baseline_limit: datetime,
    event_start_at: datetime | None = None,
) -> list[ZoneEvent]:
    start_at = activation_at if event_start_at is None else max(activation_at, event_start_at)
    return [
        event
        for event in events
        if event.event_at >= start_at
        and event.outcome_at >= event.event_at
        and event.outcome_at <= baseline_limit
    ]


def _first_event(
    events: list[ZoneEvent],
    *,
    activation_at: datetime,
    baseline_limit: datetime,
    early_minutes: int | None,
) -> ZoneEvent | None:
    candidates = causal_events_for_signal(
        events,
        activation_at=activation_at,
        baseline_limit=baseline_limit,
    )
    if early_minutes is not None:
        cutoff = activation_at + timedelta(minutes=early_minutes)
        candidates = [event for event in candidates if event.outcome_at <= cutoff]
    return (
        None
        if not candidates
        else min(candidates, key=lambda item: (item.outcome_at, item.event_at))
    )


def _event_row(
    facts: SignalFacts, event: ZoneEvent | None, *, early_window: bool
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "symbol": facts.symbol,
        "direction": facts.direction,
        "touch_at": facts.touch_at.isoformat(),
        "activation_at": facts.activation_at.isoformat(),
        "baseline_outcome": facts.baseline.outcome,
        "baseline_event_at": (
            facts.baseline.event_at.isoformat() if facts.baseline.event_at else None
        ),
        "plus2_before_minus1": facts.plus2_before_minus1,
        "plus3_before_minus1": facts.plus3_before_minus1,
        "early_60m": early_window,
        "structure_resolved": event is not None,
    }
    if event is None:
        row.update(
            {
                "structure_state": "none_resolved",
                "structure_sign": "none",
                "zone_role": None,
                "zone_outcome": None,
                "zone_event_at": None,
                "zone_outcome_at": None,
                "minutes_activation_to_resolution": None,
            }
        )
        return row
    state, sign = classify_structure(facts.direction, event.role, event.outcome)
    row.update(
        {
            "structure_state": state,
            "structure_sign": sign,
            "zone_role": event.role,
            "zone_outcome": event.outcome,
            "zone_event_at": event.event_at.isoformat(),
            "zone_outcome_at": event.outcome_at.isoformat(),
            "minutes_activation_to_resolution": round(
                (event.outcome_at - facts.activation_at).total_seconds() / 60.0, 3
            ),
        }
    )
    return row


def _summarize_structure_rows(rows: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    states = (
        "protective_hold_reclaim",
        "obstacle_clean_break_with",
        "obstacle_rejection_against",
        "protective_clean_break_against",
        "none_resolved",
    )
    for state in states:
        subset = [
            row
            for row in rows
            if row["structure_state"] == state and row["baseline_outcome"] != "data_end"
        ]
        if not subset:
            continue
        resolved = subset
        wins = sum(row["baseline_outcome"] == "reached_plus_1p10" for row in resolved)
        losses = sum(row["baseline_outcome"] == "hit_minus_1p00" for row in resolved)
        plus2 = sum(bool(row["plus2_before_minus1"]) for row in subset)
        plus3 = sum(bool(row["plus3_before_minus1"]) for row in subset)
        result.append(
            {
                "window": label,
                "structure_state": state,
                "n": len(subset),
                "resolved_baseline_n": len(resolved),
                "plus1p10_first_n": wins,
                "minus1_first_n": losses,
                "plus1p10_first_pct": _pct(wins, len(resolved)),
                "minus1_first_pct": _pct(losses, len(resolved)),
                "plus2_before_minus1_n": plus2,
                "plus2_before_minus1_pct": _pct(plus2, len(subset)),
                "plus3_before_minus1_n": plus3,
                "plus3_before_minus1_pct": _pct(plus3, len(subset)),
                "small_sample": len(subset) < SMALL_SAMPLE_N,
            }
        )
    return result


def _summarize_structure_sign(rows: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for sign in ("favorable", "adverse", "none"):
        subset = [
            row
            for row in rows
            if row["structure_sign"] == sign and row["baseline_outcome"] != "data_end"
        ]
        if not subset:
            continue
        wins = sum(row["baseline_outcome"] == "reached_plus_1p10" for row in subset)
        losses = sum(row["baseline_outcome"] == "hit_minus_1p00" for row in subset)
        plus2 = sum(bool(row["plus2_before_minus1"]) for row in subset)
        plus3 = sum(bool(row["plus3_before_minus1"]) for row in subset)
        result.append(
            {
                "window": label,
                "structure_sign": sign,
                "n": len(subset),
                "plus1p10_first_pct": _pct(wins, len(subset)),
                "minus1_first_pct": _pct(losses, len(subset)),
                "plus2_before_minus1_pct": _pct(plus2, len(subset)),
                "plus3_before_minus1_pct": _pct(plus3, len(subset)),
                "small_sample": len(subset) < SMALL_SAMPLE_N,
            }
        )
    return result


def _structure_balance_row(facts: SignalFacts, events: list[ZoneEvent]) -> dict[str, Any]:
    favorable = 0
    adverse = 0
    for event in events:
        _, sign = classify_structure(facts.direction, event.role, event.outcome)
        if sign == "favorable":
            favorable += 1
        else:
            adverse += 1
    net = favorable - adverse
    state = "net_favorable" if net > 0 else "net_adverse" if net < 0 else "balanced"
    return {
        "symbol": facts.symbol,
        "direction": facts.direction,
        "touch_at": facts.touch_at.isoformat(),
        "baseline_outcome": facts.baseline.outcome,
        "plus2_before_minus1": facts.plus2_before_minus1,
        "plus3_before_minus1": facts.plus3_before_minus1,
        "favorable_events": favorable,
        "adverse_events": adverse,
        "net_structure": net,
        "balance_state": state,
    }


def _summarize_balance(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for state in ("net_favorable", "balanced", "net_adverse"):
        subset = [
            row for row in rows
            if row["balance_state"] == state and row["baseline_outcome"] != "data_end"
        ]
        resolved = subset
        wins = sum(row["baseline_outcome"] == "reached_plus_1p10" for row in resolved)
        losses = sum(row["baseline_outcome"] == "hit_minus_1p00" for row in resolved)
        p2 = sum(bool(row["plus2_before_minus1"]) for row in subset)
        p3 = sum(bool(row["plus3_before_minus1"]) for row in subset)
        result.append(
            {
                "balance_state": state,
                "n": len(subset),
                "resolved_baseline_n": len(resolved),
                "plus1p10_first_pct": _pct(wins, len(resolved)),
                "minus1_first_pct": _pct(losses, len(resolved)),
                "plus2_before_minus1_pct": _pct(p2, len(subset)),
                "plus3_before_minus1_pct": _pct(p3, len(subset)),
            }
        )
    return result


def _entry_zone_followup(
    facts: SignalFacts,
    ref: EntryZoneRef | None,
    events: list[ZoneEvent],
    baseline_limit: datetime,
) -> dict[str, Any] | None:
    if ref is None or ref.phase_id is None:
        return None
    candidates = [
        event
        for event in causal_events_for_signal(
            events, activation_at=facts.activation_at, baseline_limit=baseline_limit
        )
        if event.phase_id == ref.phase_id
    ]
    if not candidates:
        return None
    event = min(candidates, key=lambda item: (item.outcome_at, item.event_at))
    state, sign = classify_structure(facts.direction, event.role, event.outcome)
    return {
        "symbol": facts.symbol,
        "direction": facts.direction,
        "touch_at": facts.touch_at.isoformat(),
        "entry_phase_id": ref.phase_id,
        "entry_chain_id": ref.chain_id,
        "entry_phase_role": ref.phase_role,
        "followup_state": state,
        "followup_sign": sign,
        "followup_outcome": event.outcome,
        "followup_event_at": event.event_at.isoformat(),
        "followup_outcome_at": event.outcome_at.isoformat(),
        "within_60m": event.outcome_at <= facts.activation_at + timedelta(minutes=60),
        "baseline_outcome": facts.baseline.outcome,
        "plus2_before_minus1": facts.plus2_before_minus1,
        "plus3_before_minus1": facts.plus3_before_minus1,
    }


def _find_return_to_entry(path: PathSeries, milestone_index: int, limit_index: int) -> int | None:
    for index in range(milestone_index + 1, min(limit_index + 1, len(path.moves_pct))):
        if path.moves_pct[index] <= 0.0:
            return index
    return None


def _candidate_stop_index(
    path: PathSeries, stop: float, action_index: int, limit_index: int
) -> int | None:
    found = _first_at_or_below(path, stop, action_index)
    if found is None or found > limit_index:
        return None
    return found


def _runner_before_initial_stop(path: PathSeries, target: float) -> tuple[bool, int | None]:
    target_index = _first_at_or_above(path, target)
    stop_index = _first_at_or_below(path, -1.00)
    if target_index is not None and (stop_index is None or target_index < stop_index):
        return True, target_index
    return False, target_index


def _economic_delta(
    baseline: ExactBaselineEvent,
    candidate_stop_hit: bool,
    stop: float,
    economics: dict[str, float],
) -> tuple[bool, bool, float]:
    if not candidate_stop_hit:
        return False, False, 0.0
    candidate = _candidate_net_usd(stop, economics)
    if baseline.outcome == "hit_minus_1p00":
        return True, False, round(candidate - economics["illustrative_loss_net_usd"], 6)
    if baseline.outcome == "reached_plus_1p10":
        return False, True, round(candidate - economics["illustrative_win_net_usd"], 6)
    return False, False, 0.0


def _mfe_structure_rows(
    facts: SignalFacts,
    path: PathSeries,
    events: list[ZoneEvent],
    economics: dict[str, float],
    config: P52Config,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    baseline_limit = _baseline_limit(facts.baseline, path.complete_through)
    limit_index = min(_index_at_or_after(path, baseline_limit), len(path.moves_pct) - 1)
    plus2_runner, plus2_index = _runner_before_initial_stop(path, 2.0)
    plus3_runner, plus3_index = _runner_before_initial_stop(path, 3.0)

    for milestone in config.mfe_milestones_pct:
        milestone_index = _first_at_or_above(path, milestone)
        if milestone_index is None or milestone_index > limit_index:
            continue
        return_index = _find_return_to_entry(path, milestone_index, limit_index)
        if return_index is None:
            continue
        return_at = datetime.fromtimestamp(path.timestamps[return_index], UTC)
        post_giveback = causal_events_for_signal(
            events,
            activation_at=facts.activation_at,
            event_start_at=return_at,
            baseline_limit=baseline_limit,
        )
        if not post_giveback:
            continue
        event = min(post_giveback, key=lambda item: (item.outcome_at, item.event_at))
        state, sign = classify_structure(facts.direction, event.role, event.outcome)
        action_index = min(_index_at_or_after(path, event.outcome_at), len(path.moves_pct) - 1)
        for stop in config.stop_candidates_pct:
            stop_index = _candidate_stop_index(path, stop, action_index, limit_index)
            candidate_hit = stop_index is not None
            saved, killed, delta = _economic_delta(
                facts.baseline, candidate_hit, stop, economics
            )
            lost2 = bool(
                plus2_runner
                and plus2_index is not None
                and plus2_index > action_index
                and stop_index is not None
                and stop_index < plus2_index
            )
            lost3 = bool(
                plus3_runner
                and plus3_index is not None
                and plus3_index > action_index
                and stop_index is not None
                and stop_index < plus3_index
            )
            output.append(
                {
                    "symbol": facts.symbol,
                    "direction": facts.direction,
                    "touch_at": facts.touch_at.isoformat(),
                    "mfe_milestone_pct": milestone,
                    "mfe_at": datetime.fromtimestamp(
                        path.timestamps[milestone_index], UTC
                    ).isoformat(),
                    "giveback_return_entry_at": return_at.isoformat(),
                    "structure_state": state,
                    "structure_sign": sign,
                    "structure_event_at": event.event_at.isoformat(),
                    "structure_outcome_at": event.outcome_at.isoformat(),
                    "stop_pct": stop,
                    "baseline_outcome": facts.baseline.outcome,
                    "candidate_stop_hit_before_baseline": candidate_hit,
                    "saved_baseline_loser": saved,
                    "killed_baseline_plus1p10_winner": killed,
                    "illustrative_delta_usd": delta,
                    "baseline_plus2_runner": plus2_runner,
                    "lost_plus2_runner": lost2,
                    "baseline_plus3_runner": plus3_runner,
                    "lost_plus3_runner": lost3,
                }
            )
    return output


def _scope_keys(row: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    month = str(row["touch_at"])[:7]
    return (
        ("ALL9", "ALL9"),
        ("DIRECTION", str(row["direction"])),
        ("SYMBOL", str(row["symbol"])),
        ("MONTH", month),
    )


def _aggregate_tradeoff(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, float, str, float], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for scope_type, scope_value in _scope_keys(row):
            key = (
                scope_type,
                scope_value,
                float(row["mfe_milestone_pct"]),
                str(row["structure_state"]),
                float(row["stop_pct"]),
            )
            buckets[key].append(row)
    result: list[dict[str, Any]] = []
    for key, subset in sorted(buckets.items(), key=lambda item: item[0]):
        scope_type, scope_value, milestone, state, stop = key
        acted = len(subset)
        baseline_winners = sum(row["baseline_outcome"] == "reached_plus_1p10" for row in subset)
        baseline_losers = sum(row["baseline_outcome"] == "hit_minus_1p00" for row in subset)
        saved = sum(bool(row["saved_baseline_loser"]) for row in subset)
        killed = sum(bool(row["killed_baseline_plus1p10_winner"]) for row in subset)
        plus2 = sum(bool(row["baseline_plus2_runner"]) for row in subset)
        lost2 = sum(bool(row["lost_plus2_runner"]) for row in subset)
        plus3 = sum(bool(row["baseline_plus3_runner"]) for row in subset)
        lost3 = sum(bool(row["lost_plus3_runner"]) for row in subset)
        result.append(
            {
                "scope_type": scope_type,
                "scope_value": scope_value,
                "mfe_milestone_pct": milestone,
                "structure_state": state,
                "stop_pct": stop,
                "eligible_actions": acted,
                "baseline_plus1p10_winners": baseline_winners,
                "baseline_minus1_losers": baseline_losers,
                "saved_full_minus1": saved,
                "killed_plus1p10": killed,
                "plus1p10_retention_pct": _pct(baseline_winners - killed, baseline_winners),
                "baseline_plus2_runners": plus2,
                "lost_plus2_runners": lost2,
                "plus2_retention_pct": _pct(plus2 - lost2, plus2),
                "baseline_plus3_runners": plus3,
                "lost_plus3_runners": lost3,
                "plus3_retention_pct": _pct(plus3 - lost3, plus3),
                "illustrative_delta_usd": round(
                    sum(float(row["illustrative_delta_usd"]) for row in subset), 6
                ),
                "small_sample": acted < SMALL_SAMPLE_N,
            }
        )
    return result


def _aggregate_entry_zone(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for window_name, window_rows in (
        ("all_causal", rows),
        ("first_60m", [row for row in rows if bool(row["within_60m"])]),
    ):
        by_state: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in window_rows:
            if row["baseline_outcome"] != "data_end":
                by_state[str(row["followup_state"])].append(row)
        for state, subset in sorted(by_state.items()):
            resolved = subset
            wins = sum(row["baseline_outcome"] == "reached_plus_1p10" for row in resolved)
            losses = sum(row["baseline_outcome"] == "hit_minus_1p00" for row in resolved)
            result.append(
                {
                    "window": window_name,
                    "followup_state": state,
                    "n": len(subset),
                    "plus1p10_first_n": wins,
                    "minus1_first_n": losses,
                    "plus1p10_first_pct": _pct(wins, len(resolved)),
                    "minus1_first_pct": _pct(losses, len(resolved)),
                    "small_sample": len(subset) < SMALL_SAMPLE_N,
                }
            )
    return result


def _first_event_stability(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["structure_state"] == "none_resolved" or row["baseline_outcome"] == "data_end":
            continue
        dimensions = (
            ("ALL9", "ALL9"),
            ("DIRECTION", str(row["direction"])),
            ("SYMBOL", str(row["symbol"])),
            ("MONTH", str(row["touch_at"])[:7]),
        )
        for dim, value in dimensions:
            buckets[(dim, value, str(row["structure_state"]))].append(row)
    result: list[dict[str, Any]] = []
    for (dim, value, state), subset in sorted(buckets.items()):
        resolved = [row for row in subset if row["baseline_outcome"] != "data_end"]
        wins = sum(row["baseline_outcome"] == "reached_plus_1p10" for row in resolved)
        losses = sum(row["baseline_outcome"] == "hit_minus_1p00" for row in resolved)
        result.append(
            {
                "dimension": dim,
                "value": value,
                "structure_state": state,
                "n": len(subset),
                "plus1p10_first_pct": _pct(wins, len(resolved)),
                "minus1_first_pct": _pct(losses, len(resolved)),
                "plus3_before_minus1_pct": _pct(
                    sum(bool(row["plus3_before_minus1"]) for row in subset), len(subset)
                ),
                "small_sample": len(subset) < SMALL_SAMPLE_N,
            }
        )
    return result


def _write_summary_md(path: Path, summary: dict[str, Any]) -> None:
    early = summary["headline"]["early_60m"]
    lines = [
        "# P52 — MFE + Giveback + Clean Zone Structure",
        "",
        f"Generated: `{summary['generated_at']}`",
        "",
        "Research only. No production Entry / Exit / Risk / Execution changes.",
        "Reserved NEW5 OOS assets were not opened by P52.",
        "",
        "## Frozen causal question",
        "",
        "After +0.10 activation, do resolved support/resistance events separate future runners "
        "from trades that later hit the original -1.00, and does that separation become useful "
        "after an MFE milestone followed by giveback to Entry?",
        "",
        "## Early 60m first-event headline",
        "",
    ]
    for row in early:
        lines.append(
            f"- `{row['structure_state']}`: N={row['n']}, "
            f"+1.10 first={row['plus1p10_first_pct']}%, "
            f"-1 first={row['minus1_first_pct']}%, +3 before -1={row['plus3_before_minus1_pct']}%."
        )
    lines.extend(
        [
            "",
            "## Interpretation contract",
            "",
            "- `protective_clean_break_against`: primary deterioration hypothesis.",
            "- `obstacle_clean_break_with`: runner-preservation hypothesis; "
            "tightening is expected to be dangerous.",
            "- hold/reclaim and obstacle rejection are evaluated, not assumed to be gates.",
            "- Any row with N < 20 is descriptive only.",
            "- Stop grid is frozen at -0.75/-0.60/-0.50; no optimizer is run.",
            "- Results must be checked across direction, symbol and month before any "
            "production proposal.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_research(
    root: Path,
    p50_dir: Path,
    p45_dir: Path | None,
    exact_baseline_dir: Path | None,
    output_dir: Path,
    config: P52Config,
) -> dict[str, Any]:
    root = root.resolve()
    p50_dir = p50_dir.resolve()
    p45_dir = _discover_p45_dir(root, p45_dir)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    p51_config = P51Config(
        activation_pct=config.activation_pct,
        initial_stop_pct=config.initial_stop_pct,
        mfe_milestones_pct=config.mfe_milestones_pct,
        stop_candidates_pct=config.stop_candidates_pct,
        continuation_targets_pct=config.continuation_targets_pct,
        horizon_hours=config.horizon_hours,
        day_cache_size=config.day_cache_size,
        progress_interval_seconds=config.progress_interval_seconds,
    )
    cohort_keys, p50_provenance = _load_p50_cohort(p50_dir, p51_config)
    baseline_dir = _discover_exact_baseline_dir(root, exact_baseline_dir)
    baseline_events, exact_provenance, economics = _load_exact_baseline(
        baseline_dir, cohort_keys
    )
    p45_provenance = _validate_p45_contract(p45_dir)
    zone_events = _load_zone_events(p45_dir)
    entry_zone_refs = _load_entry_zone_refs(p45_dir)

    sources = discover_sources(root)
    all_signals = load_all_signals(sources)
    selected = [signal for signal in all_signals if _signal_key_from_signal(signal) in cohort_keys]
    if len(selected) != len(cohort_keys):
        raise ValueError(f"P52 selected signal count mismatch: {len(selected)}")
    source_by_symbol = {source.symbol: source for source in sources}
    archives = {
        symbol: _archive_map(source_by_symbol[symbol].dataset_dir)
        for symbol in ALL_SYMBOLS
    }
    cache = TradeDayCache(max_days=config.day_cache_size)

    early_rows: list[dict[str, Any]] = []
    full_rows: list[dict[str, Any]] = []
    balance_rows: list[dict[str, Any]] = []
    entry_zone_rows: list[dict[str, Any]] = []
    mfe_rows: list[dict[str, Any]] = []
    runner_counts = {2.0: 0, 3.0: 0}

    heartbeat = Heartbeat(len(selected), config.progress_interval_seconds)
    heartbeat.start()
    try:
        for index, signal in enumerate(selected, 1):
            heartbeat.update(index - 1, f"{signal.symbol} {signal.touch_at.isoformat()}")
            key = _signal_key_from_signal(signal)
            baseline = baseline_events[key]
            path = _build_compact_path_series(
                signal,
                archives[signal.symbol],
                horizon_hours=config.horizon_hours,
                cache=cache,
            )
            _validate_exact_equivalence(path, baseline, p51_config)
            activation_index = _first_at_or_above(path, config.activation_pct)
            if activation_index is None:
                raise ValueError("P52 cohort signal lacks +0.10 activation")
            activation_at = datetime.fromtimestamp(path.timestamps[activation_index], UTC)
            p2, _ = _runner_before_initial_stop(path, 2.0)
            p3, _ = _runner_before_initial_stop(path, 3.0)
            runner_counts[2.0] += int(p2)
            runner_counts[3.0] += int(p3)
            facts = SignalFacts(
                symbol=signal.symbol,
                direction=str(signal.direction),
                touch_at=signal.touch_at,
                activation_at=activation_at,
                baseline=baseline,
                plus2_before_minus1=p2,
                plus3_before_minus1=p3,
            )
            limit = _baseline_limit(baseline, path.complete_through)
            symbol_events = zone_events.get(signal.symbol, [])
            early_event = _first_event(
                symbol_events,
                activation_at=activation_at,
                baseline_limit=limit,
                early_minutes=config.early_structure_minutes,
            )
            full_event = _first_event(
                symbol_events,
                activation_at=activation_at,
                baseline_limit=limit,
                early_minutes=None,
            )
            early_rows.append(_event_row(facts, early_event, early_window=True))
            full_rows.append(_event_row(facts, full_event, early_window=False))
            all_causal = causal_events_for_signal(
                symbol_events, activation_at=activation_at, baseline_limit=limit
            )
            balance_rows.append(_structure_balance_row(facts, all_causal))
            entry_row = _entry_zone_followup(
                facts, entry_zone_refs.get(key), symbol_events, limit
            )
            if entry_row is not None:
                entry_zone_rows.append(entry_row)
            if baseline.outcome != "data_end":
                mfe_rows.extend(
                    _mfe_structure_rows(facts, path, symbol_events, economics, config)
                )
            heartbeat.update(index, f"{signal.symbol} complete")
    finally:
        heartbeat.close()

    # Runner counts are a control. +1.10 is already hard-reconciled by P51 helpers.
    runner_control = {
        "plus2_before_minus1": runner_counts[2.0],
        "plus3_before_minus1": runner_counts[3.0],
        "expected_plus2_seen_data": EXPECTED_RUNNER_PLUS_2,
        "expected_plus3_seen_data": EXPECTED_RUNNER_PLUS_3,
        "plus2_matches_prior_seen_data": runner_counts[2.0] == EXPECTED_RUNNER_PLUS_2,
        "plus3_matches_prior_seen_data": runner_counts[3.0] == EXPECTED_RUNNER_PLUS_3,
    }

    early_summary = _summarize_structure_rows(early_rows, "first_resolved_within_60m")
    full_summary = _summarize_structure_rows(full_rows, "first_resolved_before_baseline_outcome")
    sign_summary = (
        _summarize_structure_sign(early_rows, "first_resolved_within_60m")
        + _summarize_structure_sign(full_rows, "first_resolved_before_baseline_outcome")
    )
    balance_summary = _summarize_balance(balance_rows)
    entry_summary = _aggregate_entry_zone(entry_zone_rows)
    tradeoff = _aggregate_tradeoff(mfe_rows)
    stability = _first_event_stability(early_rows)

    _write_csv(output_dir / "signal_first_structure_60m.csv", early_rows)
    _write_csv(output_dir / "signal_first_structure_full.csv", full_rows)
    _write_csv(output_dir / "first_structure_summary.csv", early_summary + full_summary)
    _write_csv(output_dir / "first_structure_sign_summary.csv", sign_summary)
    _write_csv(output_dir / "signal_structure_balance.csv", balance_rows)
    _write_csv(output_dir / "structure_balance_summary.csv", balance_summary)
    _write_csv(output_dir / "entry_zone_followup_signal_rows.csv", entry_zone_rows)
    _write_csv(output_dir / "entry_zone_followup_summary.csv", entry_summary)
    _write_csv(output_dir / "mfe_giveback_structure_signal_rows.csv", mfe_rows)
    _write_csv(output_dir / "mfe_giveback_structure_stop_tradeoff.csv", tradeoff)
    _write_csv(output_dir / "structure_stability.csv", stability)

    baseline_counts: defaultdict[str, int] = defaultdict(int)
    for event in baseline_events.values():
        baseline_counts[event.outcome] += 1
    if (
        baseline_counts["reached_plus_1p10"] + baseline_counts["hit_minus_1p00"]
        != EXPECTED_RESOLVED_BASELINE
    ):
        raise ValueError("P52 resolved exact baseline control failed")

    summary: dict[str, Any] = {
        "version": P52_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "period_tag": PERIOD_TAG,
        "symbols": list(ALL_SYMBOLS),
        "cohort": len(selected),
        "exact_resolved_cohort": EXPECTED_RESOLVED_BASELINE,
        "config": {
            "activation_pct": config.activation_pct,
            "initial_stop_pct": config.initial_stop_pct,
            "mfe_milestones_pct": list(config.mfe_milestones_pct),
            "stop_candidates_pct": list(config.stop_candidates_pct),
            "continuation_targets_pct": list(config.continuation_targets_pct),
            "early_structure_minutes": config.early_structure_minutes,
            "horizon_hours": config.horizon_hours,
        },
        "causal_contract": {
            "zone_touch_event_must_start_after_activation": True,
            "zone_outcome_must_be_fully_resolved_before_baseline_outcome": True,
            "classification_clock": "P45.1 outcome_at, never future label at event_at",
            "giveback_structure_event_must_start_after_return_to_entry": True,
            "small_sample_n": SMALL_SAMPLE_N,
            "no_optimizer": True,
        },
        "headline": {
            "early_60m": early_summary,
            "full_first_event": full_summary,
            "sign": sign_summary,
            "balance": balance_summary,
            "entry_zone": entry_summary,
        },
        "runner_control": runner_control,
        "provenance": {
            **p50_provenance,
            **exact_provenance,
            **p45_provenance,
        },
        "guardrails": {
            "research_only": True,
            "downloads_disabled": True,
            "reserved_five_oos_assets_touched": False,
            "entry_changed": False,
            "exit_changed": False,
            "risk_changed": False,
            "execution_changed": False,
        },
        "outputs": [
            "signal_first_structure_60m.csv",
            "signal_first_structure_full.csv",
            "first_structure_summary.csv",
            "first_structure_sign_summary.csv",
            "signal_structure_balance.csv",
            "structure_balance_summary.csv",
            "entry_zone_followup_signal_rows.csv",
            "entry_zone_followup_summary.csv",
            "mfe_giveback_structure_signal_rows.csv",
            "mfe_giveback_structure_stop_tradeoff.csv",
            "structure_stability.csv",
        ],
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_summary_md(output_dir / "summary.md", summary)
    (output_dir / "RUN_COMPLETE.json").write_text(
        json.dumps(
            {
                "version": P52_VERSION,
                "completed_at": datetime.now(UTC).isoformat(),
                "cohort": len(selected),
                "reserved_five_oos_assets_touched": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return summary


def _parse_floats(value: str) -> tuple[float, ...]:
    return tuple(float(item.strip()) for item in value.split(",") if item.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="P52 MFE + giveback + clean-zone structure")
    parser.add_argument("--root", type=Path, default=Path(r"C:\cripta"))
    parser.add_argument("--p50-dir", type=Path, required=True)
    parser.add_argument("--p45-dir", type=Path, default=None)
    parser.add_argument("--exact-baseline-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mfe-milestones-pct", default="0.25,0.50,0.75,1.00")
    parser.add_argument("--stop-candidates-pct", default="-0.75,-0.60,-0.50")
    parser.add_argument("--continuation-targets-pct", default="1.10,2.00,3.00")
    parser.add_argument("--horizon-hours", type=int, default=72)
    parser.add_argument("--day-cache-size", type=int, default=4)
    parser.add_argument("--progress-interval-seconds", type=float, default=25.0)
    args = parser.parse_args()
    config = P52Config(
        mfe_milestones_pct=_parse_floats(args.mfe_milestones_pct),
        stop_candidates_pct=_parse_floats(args.stop_candidates_pct),
        continuation_targets_pct=_parse_floats(args.continuation_targets_pct),
        horizon_hours=args.horizon_hours,
        day_cache_size=args.day_cache_size,
        progress_interval_seconds=args.progress_interval_seconds,
    )
    summary = run_research(
        root=args.root,
        p50_dir=args.p50_dir,
        p45_dir=args.p45_dir,
        exact_baseline_dir=args.exact_baseline_dir,
        output_dir=args.output_dir,
        config=config,
    )
    print(json.dumps(summary["runner_control"], indent=2), flush=True)
    print(f"P52 COMPLETE: {args.output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
