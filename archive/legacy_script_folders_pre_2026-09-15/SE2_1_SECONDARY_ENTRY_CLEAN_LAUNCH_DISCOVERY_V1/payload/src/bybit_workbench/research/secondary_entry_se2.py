from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

RESEARCH_VERSION = "SE2_SECONDARY_ENTRY_CLEAN_LAUNCH_DISCOVERY_V1"
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
DEV_SYMBOLS = ("UNIUSDT", "LINKUSDT")
HOLDOUT_SYMBOLS = tuple(symbol for symbol in ALL_SYMBOLS if symbol not in DEV_SYMBOLS)
EXPECTED_SIGNAL_COUNT = 1063
EXPECTED_SE1_EVENTS_SHA256 = "1dca79fdaa452c346d5ff5249d3fb028a8ce33e5788fa6e1e53c89215cf41424"
EXPECTED_SE1_RUN_CONTRACT_SHA256 = (
    "2d198b2220adae9cd3f2a997b481e90d4bf85722d8afac009ceecff63a4e82cc"
)
EXPECTED_TRIGGERED_SE1_ROWS = 17576
PRIMARY_TARGET_PCT = 1.10
SECONDARY_TARGETS_PCT = (0.50, 1.00, 1.10, 2.00, 3.00)
BASE_MIN_ADVERSE_PCT = (0.10, 0.25, 0.50, 0.75)
BASE_REBOUND_PCT = (0.10, 0.15, 0.20, 0.25, 0.30)
ZERO_CROSSING_MAX = (1, 2, 3, 5, 8)
TOUCH_TO_SCALE_MAX_MIN = (5, 10, 15, 30, 60)
LAUNCH_TO_SCALE_MAX_MIN = (1, 2, 5, 10, 15)
REBOUND_SPEED_MIN_PCT_PER_MIN = (0.02, 0.05, 0.10, 0.20)
TEMPORAL_FOLDS = (
    ("F1", datetime(2026, 5, 18, tzinfo=UTC), datetime(2026, 6, 17, tzinfo=UTC)),
    ("F2", datetime(2026, 6, 17, tzinfo=UTC), datetime(2026, 7, 17, tzinfo=UTC)),
    ("F3", datetime(2026, 7, 17, tzinfo=UTC), datetime(2026, 8, 17, tzinfo=UTC)),
)

Family = Literal["BASE", "Z", "T", "L", "V", "ZT", "ZL", "ZV"]


@dataclass(frozen=True)
class CausalFeatures:
    symbol: str
    touch_at: datetime
    min_adverse_depth_pct: float
    rebound_confirmation_pct: float
    zero_crossings_before_scale: int
    seconds_touch_to_launch: float
    seconds_launch_to_scale: float
    seconds_touch_to_scale: float
    launch_move_vs_main_pct: float
    scale_entry_move_vs_main_pct: float
    structural_stop_distance_from_scale_pct: float

    @property
    def rebound_speed_pct_per_min(self) -> float:
        minutes = self.seconds_launch_to_scale / 60.0
        if minutes <= 0:
            return math.inf
        return self.rebound_confirmation_pct / minutes


@dataclass(frozen=True)
class Outcome:
    secondary_exit_reason: str
    target_hits: dict[str, bool]
    secondary_mfe_to_exit_pct: float
    secondary_mae_to_exit_pct: float
    secondary_mfe_to_horizon_pct: float
    secondary_mae_to_horizon_pct: float

    def hit(self, target_pct: float) -> bool:
        return self.target_hits.get(f"{target_pct:.2f}", False)


@dataclass(frozen=True)
class Event:
    features: CausalFeatures
    outcome: Outcome


@dataclass(frozen=True)
class Candidate:
    family: Family
    min_adverse_pct: float
    rebound_pct: float
    max_zero_crossings: int | None = None
    max_touch_to_scale_min: int | None = None
    max_launch_to_scale_min: int | None = None
    min_rebound_speed_pct_per_min: float | None = None

    @property
    def complexity(self) -> int:
        return sum(
            value is not None
            for value in (
                self.max_zero_crossings,
                self.max_touch_to_scale_min,
                self.max_launch_to_scale_min,
                self.min_rebound_speed_pct_per_min,
            )
        )

    @property
    def candidate_id(self) -> str:
        parts = [
            f"A{self.min_adverse_pct:.2f}",
            f"R{self.rebound_pct:.2f}",
            self.family,
        ]
        if self.max_zero_crossings is not None:
            parts.append(f"ZLE{self.max_zero_crossings}")
        if self.max_touch_to_scale_min is not None:
            parts.append(f"TLE{self.max_touch_to_scale_min}m")
        if self.max_launch_to_scale_min is not None:
            parts.append(f"LLE{self.max_launch_to_scale_min}m")
        if self.min_rebound_speed_pct_per_min is not None:
            parts.append(f"VGE{self.min_rebound_speed_pct_per_min:.2f}")
        return "_".join(parts)

    def matches(self, features: CausalFeatures) -> bool:
        if not math.isclose(features.min_adverse_depth_pct, self.min_adverse_pct):
            return False
        if not math.isclose(features.rebound_confirmation_pct, self.rebound_pct):
            return False
        if (
            self.max_zero_crossings is not None
            and features.zero_crossings_before_scale > self.max_zero_crossings
        ):
            return False
        if (
            self.max_touch_to_scale_min is not None
            and features.seconds_touch_to_scale > self.max_touch_to_scale_min * 60.0
        ):
            return False
        if (
            self.max_launch_to_scale_min is not None
            and features.seconds_launch_to_scale > self.max_launch_to_scale_min * 60.0
        ):
            return False
        return not (
            self.min_rebound_speed_pct_per_min is not None
            and features.rebound_speed_pct_per_min < self.min_rebound_speed_pct_per_min
        )


@dataclass(frozen=True)
class Economics:
    margin_usd: float = 100.0
    leverage: float = 10.0
    primary_cost_pct_notional: float = 0.10

    @property
    def notional_usd(self) -> float:
        return self.margin_usd * self.leverage

    def winner_pnl(self, target_pct: float, cost_pct_notional: float) -> float:
        return self.notional_usd * (target_pct - cost_pct_notional) / 100.0

    def loser_pnl(self, stop_distance_pct: float, cost_pct_notional: float) -> float:
        return -self.notional_usd * (stop_distance_pct + cost_pct_notional) / 100.0


@dataclass(frozen=True)
class Metrics:
    candidate_id: str
    family: str
    complexity: int
    min_adverse_pct: float
    rebound_pct: float
    max_zero_crossings: int | None
    max_touch_to_scale_min: int | None
    max_launch_to_scale_min: int | None
    min_rebound_speed_pct_per_min: float | None
    triggered: int
    resolved: int
    unresolved: int
    retention_vs_base_pct: float
    target_0_50_pct: float
    target_1_00_pct: float
    target_1_10_pct: float
    target_2_00_pct: float
    target_3_00_pct: float
    primary_win_rate_pct: float
    primary_ev_usd: float
    primary_profit_factor: float | None
    primary_net_usd: float
    base_primary_ev_usd: float
    ev_improvement_vs_base_usd: float
    positive_temporal_folds: int
    evaluable_temporal_folds: int
    min_temporal_fold_ev_usd: float | None
    positive_symbols: int
    evaluable_symbols: int
    top_symbol_winner_concentration_pct: float
    max_loss_streak: int
    robustness_pass: bool


@dataclass(frozen=True)
class RobustnessProtocol:
    min_resolved: int = 60
    min_profit_factor: float = 1.20
    min_ev_improvement_vs_base_usd: float = 1.00
    min_positive_temporal_folds: int = 3
    min_evaluable_temporal_folds: int = 3
    min_evaluable_symbols: int = 7
    min_positive_symbol_fraction: float = 0.70
    min_symbol_resolved: int = 5
    max_top_symbol_winner_concentration_pct: float = 30.0


@dataclass(frozen=True)
class BootstrapSummary:
    candidate_id: str
    samples: int
    iterations: int
    ev_p05: float
    ev_p10: float
    ev_median: float
    ev_p90: float
    ev_p95: float


def _parse_float(row: dict[str, str], field: str) -> float:
    raw = row.get(field, "")
    if raw == "":
        raise ValueError(f"missing numeric field {field}")
    return float(raw)


def _parse_int(row: dict[str, str], field: str) -> int:
    raw = row.get(field, "")
    if raw == "":
        raise ValueError(f"missing integer field {field}")
    return int(raw)


def _parse_datetime(row: dict[str, str], field: str) -> datetime:
    raw = row.get(field, "")
    if raw == "":
        raise ValueError(f"missing datetime field {field}")
    value = datetime.fromisoformat(raw)
    if value.tzinfo is None:
        raise ValueError(f"naive datetime forbidden: {field}={raw}")
    return value.astimezone(UTC)


def _parse_target_hits(raw: str) -> dict[str, bool]:
    payload: Any = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("target_hits_json must be an object")
    result: dict[str, bool] = {}
    for target in SECONDARY_TARGETS_PCT:
        key = f"{target:.2f}"
        result[key] = payload.get(key) is not None
    return result


def load_events(path: Path) -> list[Event]:
    events: list[Event] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "symbol",
            "touch_at",
            "min_adverse_depth_pct",
            "rebound_confirmation_pct",
            "trigger_status",
            "zero_crossings_before_scale",
            "seconds_touch_to_launch",
            "seconds_launch_to_scale",
            "seconds_touch_to_scale",
            "launch_move_vs_main_pct",
            "scale_entry_move_vs_main_pct",
            "structural_stop_distance_from_scale_pct",
            "secondary_exit_reason",
            "secondary_mfe_to_exit_pct",
            "secondary_mae_to_exit_pct",
            "secondary_mfe_to_horizon_pct",
            "secondary_mae_to_horizon_pct",
            "target_hits_json",
        }
        fieldnames = set(reader.fieldnames or ())
        missing = sorted(required - fieldnames)
        if missing:
            raise ValueError(f"SE1 event table missing required fields: {missing}")
        for row in reader:
            if row.get("trigger_status") != "triggered":
                continue
            symbol = row.get("symbol", "")
            if symbol not in ALL_SYMBOLS:
                raise ValueError(f"SE2 ALL9 discovery encountered unexpected symbol: {symbol}")
            features = CausalFeatures(
                symbol=symbol,
                touch_at=_parse_datetime(row, "touch_at"),
                min_adverse_depth_pct=_parse_float(row, "min_adverse_depth_pct"),
                rebound_confirmation_pct=_parse_float(row, "rebound_confirmation_pct"),
                zero_crossings_before_scale=_parse_int(row, "zero_crossings_before_scale"),
                seconds_touch_to_launch=_parse_float(row, "seconds_touch_to_launch"),
                seconds_launch_to_scale=_parse_float(row, "seconds_launch_to_scale"),
                seconds_touch_to_scale=_parse_float(row, "seconds_touch_to_scale"),
                launch_move_vs_main_pct=_parse_float(row, "launch_move_vs_main_pct"),
                scale_entry_move_vs_main_pct=_parse_float(
                    row, "scale_entry_move_vs_main_pct"
                ),
                structural_stop_distance_from_scale_pct=_parse_float(
                    row, "structural_stop_distance_from_scale_pct"
                ),
            )
            outcome = Outcome(
                secondary_exit_reason=row.get("secondary_exit_reason", ""),
                target_hits=_parse_target_hits(row.get("target_hits_json", "{}")),
                secondary_mfe_to_exit_pct=_parse_float(row, "secondary_mfe_to_exit_pct"),
                secondary_mae_to_exit_pct=_parse_float(row, "secondary_mae_to_exit_pct"),
                secondary_mfe_to_horizon_pct=_parse_float(
                    row, "secondary_mfe_to_horizon_pct"
                ),
                secondary_mae_to_horizon_pct=_parse_float(
                    row, "secondary_mae_to_horizon_pct"
                ),
            )
            events.append(Event(features=features, outcome=outcome))
    if not events:
        raise ValueError(f"no triggered SE1 events found: {path}")
    return events


def candidate_grid() -> tuple[Candidate, ...]:
    candidates: list[Candidate] = []
    for adverse in BASE_MIN_ADVERSE_PCT:
        for rebound in BASE_REBOUND_PCT:
            candidates.append(Candidate("BASE", adverse, rebound))
            for zmax in ZERO_CROSSING_MAX:
                candidates.append(Candidate("Z", adverse, rebound, max_zero_crossings=zmax))
            for minutes in TOUCH_TO_SCALE_MAX_MIN:
                candidates.append(
                    Candidate("T", adverse, rebound, max_touch_to_scale_min=minutes)
                )
            for minutes in LAUNCH_TO_SCALE_MAX_MIN:
                candidates.append(
                    Candidate("L", adverse, rebound, max_launch_to_scale_min=minutes)
                )
            for speed in REBOUND_SPEED_MIN_PCT_PER_MIN:
                candidates.append(
                    Candidate(
                        "V",
                        adverse,
                        rebound,
                        min_rebound_speed_pct_per_min=speed,
                    )
                )
            for zmax in ZERO_CROSSING_MAX:
                for minutes in TOUCH_TO_SCALE_MAX_MIN:
                    candidates.append(
                        Candidate(
                            "ZT",
                            adverse,
                            rebound,
                            max_zero_crossings=zmax,
                            max_touch_to_scale_min=minutes,
                        )
                    )
                for minutes in LAUNCH_TO_SCALE_MAX_MIN:
                    candidates.append(
                        Candidate(
                            "ZL",
                            adverse,
                            rebound,
                            max_zero_crossings=zmax,
                            max_launch_to_scale_min=minutes,
                        )
                    )
                for speed in REBOUND_SPEED_MIN_PCT_PER_MIN:
                    candidates.append(
                        Candidate(
                            "ZV",
                            adverse,
                            rebound,
                            max_zero_crossings=zmax,
                            min_rebound_speed_pct_per_min=speed,
                        )
                    )
    ids = [candidate.candidate_id for candidate in candidates]
    if len(ids) != len(set(ids)):
        raise AssertionError("candidate IDs are not unique")
    return tuple(candidates)


def _resolved_pnl(
    event: Event,
    economics: Economics,
    *,
    target_pct: float = PRIMARY_TARGET_PCT,
    cost_pct_notional: float | None = None,
) -> float | None:
    cost = (
        economics.primary_cost_pct_notional
        if cost_pct_notional is None
        else cost_pct_notional
    )
    if event.outcome.hit(target_pct):
        return economics.winner_pnl(target_pct, cost)
    if event.outcome.secondary_exit_reason == "structural_stop":
        return economics.loser_pnl(
            event.features.structural_stop_distance_from_scale_pct,
            cost,
        )
    return None


def _profit_factor(pnls: Sequence[float]) -> float | None:
    positive = sum(value for value in pnls if value > 0)
    negative = -sum(value for value in pnls if value < 0)
    if negative <= 0:
        return None
    return positive / negative


def _max_loss_streak(events: Sequence[Event]) -> int:
    ordered = sorted(events, key=lambda item: item.features.touch_at)
    current = 0
    maximum = 0
    for event in ordered:
        if event.outcome.hit(PRIMARY_TARGET_PCT):
            current = 0
        elif event.outcome.secondary_exit_reason == "structural_stop":
            current += 1
            maximum = max(maximum, current)
    return maximum


def _subset_metrics(
    events: Sequence[Event],
    economics: Economics,
) -> tuple[int, int, float, float | None, float, float]:
    pnls = [
        pnl
        for event in events
        if (pnl := _resolved_pnl(event, economics)) is not None
    ]
    resolved = len(pnls)
    wins = sum(event.outcome.hit(PRIMARY_TARGET_PCT) for event in events)
    win_rate = 100.0 * wins / resolved if resolved else 0.0
    ev = statistics.fmean(pnls) if pnls else 0.0
    pf = _profit_factor(pnls)
    net = sum(pnls)
    return resolved, wins, win_rate, pf, ev, net


def _positive_symbol_counts(
    events: Sequence[Event],
    economics: Economics,
    protocol: RobustnessProtocol,
) -> tuple[int, int, float]:
    positive = 0
    evaluable = 0
    winner_counts: list[int] = []
    total_winners = 0
    for symbol in ALL_SYMBOLS:
        subset = [event for event in events if event.features.symbol == symbol]
        resolved, wins, _win_rate, _pf, ev, _net = _subset_metrics(subset, economics)
        if resolved >= protocol.min_symbol_resolved:
            evaluable += 1
            if ev > 0:
                positive += 1
        winner_counts.append(wins)
        total_winners += wins
    concentration = (
        100.0 * max(winner_counts) / total_winners if total_winners > 0 else 100.0
    )
    return positive, evaluable, concentration


def _temporal_fold_metrics(
    events: Sequence[Event],
    economics: Economics,
) -> tuple[int, int, float | None]:
    positive = 0
    evaluable = 0
    fold_evs: list[float] = []
    for _name, start, end in TEMPORAL_FOLDS:
        subset = [
            event
            for event in events
            if start <= event.features.touch_at < end
        ]
        resolved, _wins, _win_rate, _pf, ev, _net = _subset_metrics(subset, economics)
        if resolved >= 10:
            evaluable += 1
            fold_evs.append(ev)
            if ev > 0:
                positive += 1
    return positive, evaluable, min(fold_evs) if fold_evs else None


def _target_rate(events: Sequence[Event], target_pct: float) -> float:
    if not events:
        return 0.0
    return 100.0 * sum(event.outcome.hit(target_pct) for event in events) / len(events)


def evaluate_candidate(
    candidate: Candidate,
    events: Sequence[Event],
    economics: Economics,
    protocol: RobustnessProtocol,
    base_ev: float,
    base_triggered: int,
) -> Metrics:
    selected = [event for event in events if candidate.matches(event.features)]
    resolved, _wins, win_rate, pf, ev, net = _subset_metrics(selected, economics)
    positive_folds, evaluable_folds, min_fold_ev = _temporal_fold_metrics(
        selected, economics
    )
    positive_symbols, evaluable_symbols, concentration = _positive_symbol_counts(
        selected, economics, protocol
    )
    symbol_fraction = (
        positive_symbols / evaluable_symbols if evaluable_symbols > 0 else 0.0
    )
    retention = 100.0 * len(selected) / base_triggered if base_triggered else 0.0
    robust = (
        resolved >= protocol.min_resolved
        and ev > 0
        and pf is not None
        and pf >= protocol.min_profit_factor
        and ev - base_ev >= protocol.min_ev_improvement_vs_base_usd
        and positive_folds >= protocol.min_positive_temporal_folds
        and evaluable_folds >= protocol.min_evaluable_temporal_folds
        and evaluable_symbols >= protocol.min_evaluable_symbols
        and symbol_fraction >= protocol.min_positive_symbol_fraction
        and concentration <= protocol.max_top_symbol_winner_concentration_pct
    )
    return Metrics(
        candidate_id=candidate.candidate_id,
        family=candidate.family,
        complexity=candidate.complexity,
        min_adverse_pct=candidate.min_adverse_pct,
        rebound_pct=candidate.rebound_pct,
        max_zero_crossings=candidate.max_zero_crossings,
        max_touch_to_scale_min=candidate.max_touch_to_scale_min,
        max_launch_to_scale_min=candidate.max_launch_to_scale_min,
        min_rebound_speed_pct_per_min=candidate.min_rebound_speed_pct_per_min,
        triggered=len(selected),
        resolved=resolved,
        unresolved=len(selected) - resolved,
        retention_vs_base_pct=retention,
        target_0_50_pct=_target_rate(selected, 0.50),
        target_1_00_pct=_target_rate(selected, 1.00),
        target_1_10_pct=_target_rate(selected, 1.10),
        target_2_00_pct=_target_rate(selected, 2.00),
        target_3_00_pct=_target_rate(selected, 3.00),
        primary_win_rate_pct=win_rate,
        primary_ev_usd=ev,
        primary_profit_factor=pf,
        primary_net_usd=net,
        base_primary_ev_usd=base_ev,
        ev_improvement_vs_base_usd=ev - base_ev,
        positive_temporal_folds=positive_folds,
        evaluable_temporal_folds=evaluable_folds,
        min_temporal_fold_ev_usd=min_fold_ev,
        positive_symbols=positive_symbols,
        evaluable_symbols=evaluable_symbols,
        top_symbol_winner_concentration_pct=concentration,
        max_loss_streak=_max_loss_streak(selected),
        robustness_pass=robust,
    )


def _event_set_hash(candidate: Candidate, events: Sequence[Event]) -> str:
    payload = "\n".join(
        sorted(
            f"{event.features.symbol}|{event.features.touch_at.isoformat()}"
            for event in events
            if candidate.matches(event.features)
        )
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def select_nonredundant_candidates(
    metrics: Sequence[Metrics],
    candidates: dict[str, Candidate],
    events: Sequence[Event],
    *,
    limit: int = 5,
) -> list[Metrics]:
    passing = [metric for metric in metrics if metric.robustness_pass]
    passing.sort(
        key=lambda metric: (
            metric.complexity,
            -(metric.min_temporal_fold_ev_usd or -9999.0),
            -metric.primary_ev_usd,
            -(metric.primary_profit_factor or 0.0),
            -metric.resolved,
            metric.candidate_id,
        )
    )
    selected: list[Metrics] = []
    event_hashes: set[str] = set()
    for metric in passing:
        candidate = candidates[metric.candidate_id]
        event_hash = _event_set_hash(candidate, events)
        if event_hash in event_hashes:
            continue
        selected.append(metric)
        event_hashes.add(event_hash)
        if len(selected) >= limit:
            break
    return selected


def bootstrap_ev(
    candidate: Candidate,
    events: Sequence[Event],
    economics: Economics,
    *,
    iterations: int,
) -> BootstrapSummary:
    selected = [event for event in events if candidate.matches(event.features)]
    pnls = [
        pnl
        for event in selected
        if (pnl := _resolved_pnl(event, economics)) is not None
    ]
    if not pnls:
        raise ValueError(f"cannot bootstrap unresolved candidate: {candidate.candidate_id}")
    seed = int(hashlib.sha256(candidate.candidate_id.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    boot = []
    for _ in range(iterations):
        sample = [pnls[rng.randrange(len(pnls))] for _index in range(len(pnls))]
        boot.append(statistics.fmean(sample))
    boot.sort()

    def quantile(fraction: float) -> float:
        position = (len(boot) - 1) * fraction
        lower = math.floor(position)
        upper = min(lower + 1, len(boot) - 1)
        weight = position - lower
        return boot[lower] + (boot[upper] - boot[lower]) * weight

    return BootstrapSummary(
        candidate_id=candidate.candidate_id,
        samples=len(pnls),
        iterations=iterations,
        ev_p05=quantile(0.05),
        ev_p10=quantile(0.10),
        ev_median=quantile(0.50),
        ev_p90=quantile(0.90),
        ev_p95=quantile(0.95),
    )


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _jsonable_dataclass(value: Any) -> dict[str, Any]:
    return asdict(value)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _cost_sensitivity(
    candidate: Candidate,
    events: Sequence[Event],
    economics: Economics,
) -> list[dict[str, Any]]:
    selected = [event for event in events if candidate.matches(event.features)]
    rows: list[dict[str, Any]] = []
    for cost in (0.05, 0.075, 0.10, 0.15):
        pnls = [
            pnl
            for event in selected
            if (
                pnl := _resolved_pnl(
                    event,
                    economics,
                    cost_pct_notional=cost,
                )
            )
            is not None
        ]
        rows.append(
            {
                "candidate_id": candidate.candidate_id,
                "cost_pct_notional": cost,
                "resolved": len(pnls),
                "ev_usd": statistics.fmean(pnls) if pnls else 0.0,
                "net_usd": sum(pnls),
                "profit_factor": _profit_factor(pnls),
            }
        )
    return rows


def _scope_detail(
    candidate: Candidate,
    events: Sequence[Event],
    economics: Economics,
) -> list[dict[str, Any]]:
    selected = [event for event in events if candidate.matches(event.features)]
    rows: list[dict[str, Any]] = []
    scopes: list[tuple[str, set[str]]] = [
        ("ALL9", set(ALL_SYMBOLS)),
        ("DEV2", set(DEV_SYMBOLS)),
        ("HOLDOUT7", set(HOLDOUT_SYMBOLS)),
    ]
    scopes.extend((symbol, {symbol}) for symbol in ALL_SYMBOLS)
    for scope, symbols in scopes:
        subset = [event for event in selected if event.features.symbol in symbols]
        resolved, wins, win_rate, pf, ev, net = _subset_metrics(subset, economics)
        rows.append(
            {
                "candidate_id": candidate.candidate_id,
                "scope": scope,
                "triggered": len(subset),
                "resolved": resolved,
                "wins_plus_1_10": wins,
                "win_rate_pct": win_rate,
                "ev_usd": ev,
                "net_usd": net,
                "profit_factor": pf,
                "target_0_50_pct": _target_rate(subset, 0.50),
                "target_1_00_pct": _target_rate(subset, 1.00),
                "target_2_00_pct": _target_rate(subset, 2.00),
                "target_3_00_pct": _target_rate(subset, 3.00),
            }
        )
    for name, start, end in TEMPORAL_FOLDS:
        subset = [
            event
            for event in selected
            if start <= event.features.touch_at < end
        ]
        resolved, wins, win_rate, pf, ev, net = _subset_metrics(subset, economics)
        rows.append(
            {
                "candidate_id": candidate.candidate_id,
                "scope": name,
                "triggered": len(subset),
                "resolved": resolved,
                "wins_plus_1_10": wins,
                "win_rate_pct": win_rate,
                "ev_usd": ev,
                "net_usd": net,
                "profit_factor": pf,
                "target_0_50_pct": _target_rate(subset, 0.50),
                "target_1_00_pct": _target_rate(subset, 1.00),
                "target_2_00_pct": _target_rate(subset, 2.00),
                "target_3_00_pct": _target_rate(subset, 3.00),
            }
        )
    return rows


def _selected_events(
    candidate: Candidate,
    events: Sequence[Event],
    economics: Economics,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        if not candidate.matches(event.features):
            continue
        pnl = _resolved_pnl(event, economics)
        rows.append(
            {
                "candidate_id": candidate.candidate_id,
                "symbol": event.features.symbol,
                "touch_at": event.features.touch_at.isoformat(),
                "min_adverse_pct": event.features.min_adverse_depth_pct,
                "rebound_pct": event.features.rebound_confirmation_pct,
                "zero_crossings_before_scale": event.features.zero_crossings_before_scale,
                "seconds_touch_to_launch": event.features.seconds_touch_to_launch,
                "seconds_launch_to_scale": event.features.seconds_launch_to_scale,
                "seconds_touch_to_scale": event.features.seconds_touch_to_scale,
                "rebound_speed_pct_per_min": event.features.rebound_speed_pct_per_min,
                "launch_move_vs_main_pct": event.features.launch_move_vs_main_pct,
                "scale_entry_move_vs_main_pct": event.features.scale_entry_move_vs_main_pct,
                "structural_stop_distance_from_scale_pct": (
                    event.features.structural_stop_distance_from_scale_pct
                ),
                "hit_plus_0_50": event.outcome.hit(0.50),
                "hit_plus_1_00": event.outcome.hit(1.00),
                "hit_plus_1_10": event.outcome.hit(1.10),
                "hit_plus_2_00": event.outcome.hit(2.00),
                "hit_plus_3_00": event.outcome.hit(3.00),
                "secondary_exit_reason": event.outcome.secondary_exit_reason,
                "secondary_mfe_to_exit_pct": event.outcome.secondary_mfe_to_exit_pct,
                "secondary_mae_to_exit_pct": event.outcome.secondary_mae_to_exit_pct,
                "secondary_mfe_to_horizon_pct": event.outcome.secondary_mfe_to_horizon_pct,
                "secondary_mae_to_horizon_pct": event.outcome.secondary_mae_to_horizon_pct,
                "primary_benchmark_pnl_usd": pnl,
            }
        )
    return rows


def _distribution(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"p10": None, "p25": None, "median": None, "p75": None, "p90": None}
    ordered = sorted(values)

    def quantile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = math.floor(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] + (ordered[upper] - ordered[lower]) * weight

    return {
        "p10": quantile(0.10),
        "p25": quantile(0.25),
        "median": statistics.median(ordered),
        "p75": quantile(0.75),
        "p90": quantile(0.90),
    }


def _feature_anatomy(events: Sequence[Event]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    groups = {
        "PLUS_1_10_WIN": [event for event in events if event.outcome.hit(1.10)],
        "STRUCTURAL_STOP_NO_1_10": [
            event
            for event in events
            if not event.outcome.hit(1.10)
            and event.outcome.secondary_exit_reason == "structural_stop"
        ],
    }
    for label, subset in groups.items():
        features: dict[str, list[float]] = {
            "zero_crossings_before_scale": [
                float(event.features.zero_crossings_before_scale) for event in subset
            ],
            "seconds_touch_to_scale": [event.features.seconds_touch_to_scale for event in subset],
            "seconds_launch_to_scale": [
                event.features.seconds_launch_to_scale for event in subset
            ],
            "rebound_speed_pct_per_min": [
                event.features.rebound_speed_pct_per_min for event in subset
            ],
            "scale_entry_move_vs_main_pct": [
                event.features.scale_entry_move_vs_main_pct for event in subset
            ],
            "structural_stop_distance_from_scale_pct": [
                event.features.structural_stop_distance_from_scale_pct for event in subset
            ],
        }
        for name, values in features.items():
            finite = [value for value in values if math.isfinite(value)]
            dist = _distribution(finite)
            rows.append(
                {
                    "class": label,
                    "feature": name,
                    "count": len(finite),
                    **dist,
                }
            )
    return rows


def _summary_markdown(
    source_sha256: str,
    metrics: Sequence[Metrics],
    selected: Sequence[Metrics],
    bootstrap: Sequence[BootstrapSummary],
) -> str:
    passing = sum(metric.robustness_pass for metric in metrics)
    lines = [
        "# SE2 Secondary Entry — Clean Launch Discovery V1",
        "",
        "Research only. Downloads: DISABLED / fail-closed.",
        "",
        (
            "SE2 does not change Main Entry, the Main -1.00% structural stop, "
            "Exit, Risk, Execution, or live runtime."
        ),
        "It searches only causal features already known at the SE1 Secondary Entry timestamp.",
        "NEW5 is not read. This is ALL9 discovery, not confirmation.",
        "",
        f"SE1 events SHA256: `{source_sha256}`",
        f"Candidate grid: **{len(metrics)}** predefined candidates.",
        f"Robustness-pass candidates: **{passing}**.",
        "",
        "## Selected non-redundant discovery candidates",
        "",
        (
            "| Candidate | N | Win +1.10 | EV USD | PF | Min fold EV | "
            "Positive symbols | Max loss streak |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    if not selected:
        lines.append("| NONE | 0 | 0 | 0 | 0 | 0 | 0 | 0 |")
    for metric in selected:
        pf = "NA" if metric.primary_profit_factor is None else f"{metric.primary_profit_factor:.3f}"
        min_fold = (
            "NA"
            if metric.min_temporal_fold_ev_usd is None
            else f"{metric.min_temporal_fold_ev_usd:.3f}"
        )
        lines.append(
            "| "
            + " | ".join(
                (
                    metric.candidate_id,
                    str(metric.resolved),
                    f"{metric.primary_win_rate_pct:.2f}%",
                    f"{metric.primary_ev_usd:.3f}",
                    pf,
                    min_fold,
                    f"{metric.positive_symbols}/{metric.evaluable_symbols}",
                    str(metric.max_loss_streak),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Bootstrap EV uncertainty for selected candidates",
            "",
            "| Candidate | N | EV p05 | EV p10 | Median | EV p90 | EV p95 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    if not bootstrap:
        lines.append("| NONE | 0 | 0 | 0 | 0 | 0 | 0 |")
    for row in bootstrap:
        lines.append(
            "| "
            + " | ".join(
                (
                    row.candidate_id,
                    str(row.samples),
                    f"{row.ev_p05:.3f}",
                    f"{row.ev_p10:.3f}",
                    f"{row.ev_median:.3f}",
                    f"{row.ev_p90:.3f}",
                    f"{row.ev_p95:.3f}",
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Interpretation contract",
            "",
            (
                "A selected candidate is a discovery hypothesis only. "
                "It is not a production Scale rule."
            ),
            (
                "Do not retune this candidate on NEW5. Confirmation must apply "
                "a frozen candidate exactly as emitted."
            ),
            (
                "The primary benchmark is +1.10% from the Secondary fill before "
                "the SE1 structural stop, with USD 100 margin x10 and 0.10% "
                "notional cost reserve."
            ),
            (
                "Structural stop remains launch/reversal point minus 0.10 percentage "
                "points; leverage does not redefine it."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def _find_default_se1_events(project_root: Path) -> Path:
    candidates = (
        project_root
        / "reports"
        / "secondary_entry_se1"
        / "ALL9_SE1_WORKING"
        / "secondary_entry_events.csv",
    )
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "SE1 machine truth missing. Expected reports\\secondary_entry_se1\\"
        "ALL9_SE1_WORKING\\secondary_entry_events.csv"
    )


def _validate_se1_contract(source: Path) -> str:
    contract_path = source.parent / "run_contract.json"
    if not contract_path.is_file():
        raise FileNotFoundError(
            f"SE1 run_contract.json missing beside event table: {contract_path}"
        )
    payload: Any = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("SE1 run contract must be a JSON object")
    contract_sha = payload.get("contract_sha256")
    if contract_sha != EXPECTED_SE1_RUN_CONTRACT_SHA256:
        raise ValueError(
            "SE1 run contract mismatch: "
            f"expected {EXPECTED_SE1_RUN_CONTRACT_SHA256}, got {contract_sha}"
        )
    expected_counts = payload.get("expected_counts")
    if not isinstance(expected_counts, dict):
        raise ValueError("SE1 run contract expected_counts missing")
    total = 0
    for value in expected_counts.values():
        if not isinstance(value, int):
            raise ValueError("SE1 expected_counts must contain integers")
        total += value
    if total != EXPECTED_SIGNAL_COUNT:
        raise ValueError(
            f"SE1 signal count mismatch: expected {EXPECTED_SIGNAL_COUNT}, got {total}"
        )
    return cast(str, contract_sha)


def run(
    *,
    project_root: Path,
    output_dir: Path,
    se1_events_path: Path | None = None,
    bootstrap_iterations: int = 2000,
    progress_interval_seconds: float = 20.0,
) -> dict[str, Any]:
    source = se1_events_path or _find_default_se1_events(project_root)
    if not source.is_file():
        raise FileNotFoundError(source)
    source_sha256 = _hash_file(source)
    if source_sha256 != EXPECTED_SE1_EVENTS_SHA256:
        raise ValueError(
            "SE1 event table SHA256 mismatch: "
            f"expected {EXPECTED_SE1_EVENTS_SHA256}, got {source_sha256}"
        )
    se1_contract_sha256 = _validate_se1_contract(source)
    print(f"[SE2] source={source}", flush=True)
    print(f"[SE2] source_sha256={source_sha256}", flush=True)
    events = load_events(source)
    if len(events) != EXPECTED_TRIGGERED_SE1_ROWS:
        raise ValueError(
            f"SE1 triggered-row mismatch: expected {EXPECTED_TRIGGERED_SE1_ROWS}, got {len(events)}"
        )
    print(f"[SE2] triggered SE1 rows={len(events)}", flush=True)

    economics = Economics()
    protocol = RobustnessProtocol()
    grid = candidate_grid()
    by_id = {candidate.candidate_id: candidate for candidate in grid}
    print(f"[SE2] predefined candidates={len(grid)}", flush=True)

    base_stats: dict[tuple[float, float], tuple[float, int]] = {}
    for adverse in BASE_MIN_ADVERSE_PCT:
        for rebound in BASE_REBOUND_PCT:
            candidate = Candidate("BASE", adverse, rebound)
            matched_base_events = [
                event for event in events if candidate.matches(event.features)
            ]
            _resolved, _wins, _win_rate, _pf, ev, _net = _subset_metrics(
                matched_base_events, economics
            )
            base_stats[(adverse, rebound)] = (ev, len(matched_base_events))

    metrics: list[Metrics] = []
    started = time.monotonic()
    last_heartbeat = started
    for index, candidate in enumerate(grid, start=1):
        base_ev, base_triggered = base_stats[(candidate.min_adverse_pct, candidate.rebound_pct)]
        metrics.append(
            evaluate_candidate(
                candidate,
                events,
                economics,
                protocol,
                base_ev,
                base_triggered,
            )
        )
        now = time.monotonic()
        if now - last_heartbeat >= progress_interval_seconds:
            elapsed = now - started
            rate = index / elapsed if elapsed > 0 else 0.0
            remaining = (len(grid) - index) / rate if rate > 0 else 0.0
            print(
                f"[SE2] candidates {index}/{len(grid)} "
                f"({100.0 * index / len(grid):.1f}%) "
                f"elapsed={elapsed:.1f}s eta={remaining:.1f}s",
                flush=True,
            )
            last_heartbeat = now

    selected_metrics = select_nonredundant_candidates(metrics, by_id, events)
    bootstrap_rows = [
        bootstrap_ev(
            by_id[metric.candidate_id],
            events,
            economics,
            iterations=bootstrap_iterations,
        )
        for metric in selected_metrics
    ]
    bootstrap_by_id = {row.candidate_id: row for row in bootstrap_rows}
    preferred = [
        metric
        for metric in selected_metrics
        if bootstrap_by_id[metric.candidate_id].ev_p05 > 0
    ]

    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_rows = [_jsonable_dataclass(metric) for metric in metrics]
    metrics_rows.sort(
        key=lambda row: (
            not bool(row["robustness_pass"]),
            int(row["complexity"]),
            -float(row["primary_ev_usd"]),
            str(row["candidate_id"]),
        )
    )
    _write_csv(output_dir / "candidate_grid_results.csv", metrics_rows)
    _write_csv(
        output_dir / "prebootstrap_candidates.csv",
        [_jsonable_dataclass(metric) for metric in selected_metrics],
    )
    _write_csv(
        output_dir / "selected_candidates.csv",
        [_jsonable_dataclass(metric) for metric in preferred],
    )
    _write_csv(
        output_dir / "bootstrap_ev.csv",
        [_jsonable_dataclass(row) for row in bootstrap_rows],
    )

    detail_rows: list[dict[str, Any]] = []
    cost_rows: list[dict[str, Any]] = []
    selected_event_rows: list[dict[str, Any]] = []
    for metric in preferred:
        candidate = by_id[metric.candidate_id]
        detail_rows.extend(_scope_detail(candidate, events, economics))
        cost_rows.extend(_cost_sensitivity(candidate, events, economics))
        selected_event_rows.extend(_selected_events(candidate, events, economics))
    _write_csv(output_dir / "selected_candidate_scope_detail.csv", detail_rows)
    _write_csv(output_dir / "selected_candidate_cost_sensitivity.csv", cost_rows)
    _write_csv(output_dir / "selected_candidate_events.csv", selected_event_rows)
    _write_csv(output_dir / "clean_launch_feature_anatomy.csv", _feature_anatomy(events))

    selection_payload = {
        "research": RESEARCH_VERSION,
        "selection_status": (
            "DISCOVERY_CANDIDATES_FOUND" if preferred else "NO_ROBUST_CANDIDATE"
        ),
        "source_se1_events_sha256": source_sha256,
        "source_se1_run_contract_sha256": se1_contract_sha256,
        "candidate_grid_count": len(grid),
        "robustness_protocol": _jsonable_dataclass(protocol),
        "prebootstrap_candidates": [
            _jsonable_dataclass(metric) for metric in selected_metrics
        ],
        "selected_candidates": [_jsonable_dataclass(metric) for metric in preferred],
        "bootstrap_gate": "EV bootstrap p05 > 0",
        "new5_accessed": False,
        "production_rule_created": False,
    }
    selection_text = (
        json.dumps(selection_payload, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )
    selection_path = output_dir / "SE2_DISCOVERY_CANDIDATE_MANIFEST.json"
    selection_path.write_text(selection_text, encoding="utf-8")
    selection_hash = hashlib.sha256(selection_text.encode("utf-8")).hexdigest()
    (output_dir / "SE2_DISCOVERY_CANDIDATE_MANIFEST.sha256").write_text(
        selection_hash + "  SE2_DISCOVERY_CANDIDATE_MANIFEST.json\n",
        encoding="ascii",
    )

    provenance = {
        "research_version": RESEARCH_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "period": PERIOD_TAG,
        "symbols": list(ALL_SYMBOLS),
        "source_se1_events": str(source),
        "source_se1_events_sha256": source_sha256,
        "source_se1_run_contract_sha256": se1_contract_sha256,
        "triggered_se1_rows": len(events),
        "candidate_grid_count": len(grid),
        "prebootstrap_candidate_count": len(selected_metrics),
        "selected_candidate_count": len(preferred),
        "candidate_manifest_sha256": selection_hash,
        "downloads": "DISABLED",
        "new5_accessed": False,
        "main_entry_changed": False,
        "main_structural_stop_changed": False,
        "exit_risk_execution_changed": False,
        "live_changed": False,
        "economics": _jsonable_dataclass(economics),
        "robustness_protocol": _jsonable_dataclass(protocol),
    }
    (output_dir / "provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    summary = {
        "research": RESEARCH_VERSION,
        "status": selection_payload["selection_status"],
        "source_se1_events_sha256": source_sha256,
        "source_se1_run_contract_sha256": se1_contract_sha256,
        "candidate_grid_count": len(grid),
        "robustness_pass_count": sum(metric.robustness_pass for metric in metrics),
        "prebootstrap_candidate_count": len(selected_metrics),
        "selected_candidate_count": len(preferred),
        "selected_candidate_ids": [metric.candidate_id for metric in preferred],
        "candidate_manifest_sha256": selection_hash,
        "new5_accessed": False,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "summary.md").write_text(
        _summary_markdown(source_sha256, metrics, preferred, bootstrap_rows),
        encoding="utf-8",
    )
    print(
        f"[SE2] done status={summary['status']} "
        f"passes={summary['robustness_pass_count']} selected={len(preferred)}",
        flush=True,
    )
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=RESEARCH_VERSION)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--se1-events", type=Path)
    parser.add_argument("--bootstrap-iterations", type=int, default=2000)
    parser.add_argument("--progress-interval-seconds", type=float, default=20.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.bootstrap_iterations < 100:
        raise ValueError("bootstrap_iterations must be >= 100")
    if args.progress_interval_seconds <= 0:
        raise ValueError("progress_interval_seconds must be > 0")
    run(
        project_root=args.project_root.resolve(),
        output_dir=args.output_dir.resolve(),
        se1_events_path=args.se1_events.resolve() if args.se1_events else None,
        bootstrap_iterations=args.bootstrap_iterations,
        progress_interval_seconds=args.progress_interval_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
