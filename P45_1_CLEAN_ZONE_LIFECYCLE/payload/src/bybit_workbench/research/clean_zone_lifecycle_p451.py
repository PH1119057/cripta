from __future__ import annotations

import argparse
import csv
import math
import statistics
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from bybit_workbench.research.multi_touch_sr_p45 import (  # noqa: I001
    ATR_PERIOD,
    DEFAULT_SYMBOLS,
    DISPLAY_SYMBOLS,
    PIVOT_SPAN,
    ZONE_HALF_WIDTH_ATR,
    Candle,
    CoreSignal,
    Direction,
    FeatureThreshold,
    Outcome,
    Role,
    Segment,
    _is_pivot_high,
    _is_pivot_low,
    _linear_slope,
    _load_p44_context,
    _quantile,
    _sha256_file,
    _write_csv,
    _write_dataclass_csv,
    _write_json,
    classify_quartile,
    load_candles,
    load_core_signals,
    parse_datetime,
    resolve_frozen_dataset_dir,
    segment_for,
    validation_root,
    wilder_atr,
)

PhaseOrigin = Literal["pivot", "role_reversal"]
PhaseEndReason = Literal["confirmed_break", "age_expiry"]
TouchOutcome = Literal["bounce", "clean_break", "false_break_reclaim", "unresolved"]

REARM_DISTANCE_ATR = 1.0
BREAK_CONFIRM_CLOSES = 2
PHASE_MAX_AGE_HOURS = 168.0
TOUCH_OUTCOME_HORIZON_BARS = 96
APPROACH_BARS = 8
APPROACH_NEAR_DISTANCE_ATR = 1.0
NEAR_ZONE_ATR = 0.5
FRESH_PHASE_HOURS = 24.0


@dataclass(slots=True)
class ZonePhase:
    phase_id: int
    chain_id: int
    center: float
    half_width: float
    chain_origin_at: datetime
    phase_started_at: datetime
    confirmed_at: datetime
    origin_role: Role
    role: Role
    phase_origin: PhaseOrigin
    source_pivots: int
    support_pivots: int
    resistance_pivots: int
    prior_phase_id: int | None = None
    prior_phase_retests: int = 0
    retest_count: int = 0
    last_retest_at: datetime | None = None
    false_breaks: int = 0
    pending_break_closes: int = 0
    armed_for_retest: bool = False
    rejection_max_atr: float = 0.0
    active: bool = True
    ended_at: datetime | None = None
    end_reason: PhaseEndReason | None = None

    @property
    def lower(self) -> float:
        return self.center - self.half_width

    @property
    def upper(self) -> float:
        return self.center + self.half_width


def _distance_to_phase_band(price: float, phase: ZonePhase) -> float:
    if phase.lower <= price <= phase.upper:
        return 0.0
    return min(abs(price - phase.lower), abs(price - phase.upper))


@dataclass(frozen=True, slots=True)
class LifecycleTouchEvent:
    symbol: str
    phase_id: int
    chain_id: int
    role: Role
    phase_origin: PhaseOrigin
    event_at: datetime
    event_index: int
    test_ordinal: int
    source_pivots: int
    false_breaks_before: int
    prior_phase_retests: int
    center: float
    lower: float
    upper: float
    touch_atr: float
    outcome: TouchOutcome
    outcome_bars: int | None
    outcome_at: datetime | None


@dataclass(frozen=True, slots=True)
class CoreLifecycleFeature:
    symbol: str
    display_symbol: str
    direction: Direction
    touch_at: datetime
    segment: Segment
    entry_price: float
    outcome_05: Outcome
    outcome_10: Outcome
    phase_found: bool
    phase_id: int | None
    chain_id: int | None
    phase_role: Role | None
    phase_origin: PhaseOrigin | None
    chain_origin_role: Role | None
    phase_center: float | None
    phase_lower: float | None
    phase_upper: float | None
    phase_age_hours: float | None
    entry_distance_atr: float | None
    entry_inside_zone: bool
    independent_test_ready: bool
    prior_phase_retests: int | None
    current_test_ordinal: int | None
    hours_since_last_retest: float | None
    source_pivots: int | None
    false_breaks: int | None
    previous_rejection_atr: float | None
    role_reversal_phase: bool
    first_retest_after_break: bool
    near_zone_fraction_2h: float | None
    approach_slope_atr_per_bar: float | None
    approach_distance_range_atr: float | None
    p44_residual_15m_pct: float | None
    p44_residual_q1: bool | None


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    family: str
    predicate: Callable[[CoreLifecycleFeature], bool]


@dataclass(frozen=True, slots=True)
class TouchOutcomeMetrics:
    sample: int
    bounce: int
    clean_break: int
    false_break_reclaim: int
    unresolved: int
    bounce_pct: float | None
    clean_break_pct: float | None
    false_break_reclaim_pct: float | None
    unresolved_pct: float | None


class CleanZoneLifecycleDetector:
    def __init__(
        self,
        symbol: str,
        candles: tuple[Candle, ...],
        *,
        pivot_span: int = PIVOT_SPAN,
        atr_period: int = ATR_PERIOD,
        half_width_atr: float = ZONE_HALF_WIDTH_ATR,
        rearm_distance_atr: float = REARM_DISTANCE_ATR,
        break_confirm_closes: int = BREAK_CONFIRM_CLOSES,
        phase_max_age_hours: float = PHASE_MAX_AGE_HOURS,
    ) -> None:
        if pivot_span < 1:
            raise ValueError("pivot_span must be positive")
        if half_width_atr <= 0 or rearm_distance_atr <= 0:
            raise ValueError("zone width and rearm distance must be positive")
        if break_confirm_closes < 2:
            raise ValueError("break_confirm_closes must be at least two")
        if phase_max_age_hours <= 0:
            raise ValueError("phase_max_age_hours must be positive")
        self.symbol = symbol
        self.candles = candles
        self.atr = wilder_atr(candles, atr_period)
        self.pivot_span = pivot_span
        self.half_width_atr = half_width_atr
        self.rearm_distance_atr = rearm_distance_atr
        self.break_confirm_closes = break_confirm_closes
        self.phase_max_age_hours = phase_max_age_hours
        self.phases: list[ZonePhase] = []
        self._raw_touch_events: list[LifecycleTouchEvent] = []
        self.processed_index = -1
        self._next_phase_id = 1
        self._next_chain_id = 1

    def _active_phases(self) -> list[ZonePhase]:
        return [phase for phase in self.phases if phase.active]

    def _new_pivot_phase(
        self,
        *,
        price: float,
        half_width: float,
        role: Role,
        origin_at: datetime,
        confirmed_at: datetime,
    ) -> ZonePhase:
        phase = ZonePhase(
            phase_id=self._next_phase_id,
            chain_id=self._next_chain_id,
            center=price,
            half_width=half_width,
            chain_origin_at=origin_at,
            phase_started_at=confirmed_at,
            confirmed_at=confirmed_at,
            origin_role=role,
            role=role,
            phase_origin="pivot",
            source_pivots=1,
            support_pivots=1 if role == "support" else 0,
            resistance_pivots=1 if role == "resistance" else 0,
        )
        self._next_phase_id += 1
        self._next_chain_id += 1
        self.phases.append(phase)
        return phase

    def _new_reversal_phase(self, previous: ZonePhase, at: datetime) -> ZonePhase:
        role: Role = "resistance" if previous.role == "support" else "support"
        phase = ZonePhase(
            phase_id=self._next_phase_id,
            chain_id=previous.chain_id,
            center=previous.center,
            half_width=previous.half_width,
            chain_origin_at=previous.chain_origin_at,
            phase_started_at=at,
            confirmed_at=at,
            origin_role=previous.origin_role,
            role=role,
            phase_origin="role_reversal",
            source_pivots=0,
            support_pivots=0,
            resistance_pivots=0,
            prior_phase_id=previous.phase_id,
            prior_phase_retests=previous.retest_count,
        )
        self._next_phase_id += 1
        self.phases.append(phase)
        return phase

    def _end_phase(self, phase: ZonePhase, *, at: datetime, reason: PhaseEndReason) -> None:
        phase.active = False
        phase.ended_at = at
        phase.end_reason = reason
        phase.pending_break_closes = 0
        phase.armed_for_retest = False

    def _expire_old_phases(self, at: datetime) -> None:
        for phase in self._active_phases():
            age_hours = (at - phase.phase_started_at).total_seconds() / 3600.0
            if age_hours >= self.phase_max_age_hours:
                self._end_phase(phase, at=at, reason="age_expiry")

    def _merge_or_create(
        self,
        *,
        price: float,
        half_width: float,
        role: Role,
        origin_at: datetime,
        confirmed_at: datetime,
    ) -> None:
        candidates = [
            phase
            for phase in self._active_phases()
            if phase.role == role
            and abs(phase.center - price) <= phase.half_width + half_width
        ]
        if not candidates:
            self._new_pivot_phase(
                price=price,
                half_width=half_width,
                role=role,
                origin_at=origin_at,
                confirmed_at=confirmed_at,
            )
            return
        phase = min(
            candidates,
            key=lambda item: (
                abs(item.center - price),
                -item.phase_started_at.timestamp(),
                item.phase_id,
            ),
        )
        anchor_weight = max(1, phase.source_pivots)
        phase.center = (phase.center * anchor_weight + price) / (anchor_weight + 1)
        phase.half_width = (
            phase.half_width * anchor_weight + half_width
        ) / (anchor_weight + 1)
        phase.source_pivots += 1
        if role == "support":
            phase.support_pivots += 1
        else:
            phase.resistance_pivots += 1

    def _record_touch(self, phase: ZonePhase, candle: Candle, index: int, atr: float) -> None:
        phase.retest_count += 1
        phase.last_retest_at = candle.closed_at
        phase.armed_for_retest = False
        phase.rejection_max_atr = 0.0
        self._raw_touch_events.append(
            LifecycleTouchEvent(
                symbol=self.symbol,
                phase_id=phase.phase_id,
                chain_id=phase.chain_id,
                role=phase.role,
                phase_origin=phase.phase_origin,
                event_at=candle.closed_at,
                event_index=index,
                test_ordinal=phase.retest_count,
                source_pivots=phase.source_pivots,
                false_breaks_before=phase.false_breaks,
                prior_phase_retests=phase.prior_phase_retests,
                center=phase.center,
                lower=phase.lower,
                upper=phase.upper,
                touch_atr=atr,
                outcome="unresolved",
                outcome_bars=None,
                outcome_at=None,
            )
        )

    def _update_phase(self, phase: ZonePhase, candle: Candle, index: int, atr: float) -> None:
        intersects = candle.low <= phase.upper and candle.high >= phase.lower
        recorded_touch = intersects and phase.armed_for_retest
        if recorded_touch:
            self._record_touch(phase, candle, index, atr)

        if not phase.armed_for_retest and not recorded_touch:
            if phase.role == "support":
                excursion = max(0.0, (candle.close - phase.upper) / atr)
                phase.rejection_max_atr = max(phase.rejection_max_atr, excursion)
                if candle.close >= phase.upper + self.rearm_distance_atr * atr:
                    phase.armed_for_retest = True
            else:
                excursion = max(0.0, (phase.lower - candle.close) / atr)
                phase.rejection_max_atr = max(phase.rejection_max_atr, excursion)
                if candle.close <= phase.lower - self.rearm_distance_atr * atr:
                    phase.armed_for_retest = True

        beyond = (
            candle.close < phase.lower
            if phase.role == "support"
            else candle.close > phase.upper
        )
        if beyond:
            phase.pending_break_closes += 1
            if phase.pending_break_closes >= self.break_confirm_closes:
                self._end_phase(phase, at=candle.closed_at, reason="confirmed_break")
                self._new_reversal_phase(phase, candle.closed_at)
        elif phase.pending_break_closes:
            phase.false_breaks += 1
            phase.pending_break_closes = 0

    def _confirm_pivots(self, current_index: int) -> None:
        pivot_index = current_index - self.pivot_span
        if pivot_index < self.pivot_span:
            return
        pivot_atr = self.atr[pivot_index]
        if pivot_atr is None or pivot_atr <= 0:
            return
        pivot = self.candles[pivot_index]
        confirmed_at = self.candles[current_index].closed_at
        half_width = self.half_width_atr * pivot_atr
        if _is_pivot_low(self.candles, pivot_index, self.pivot_span):
            self._merge_or_create(
                price=pivot.low,
                half_width=half_width,
                role="support",
                origin_at=pivot.closed_at,
                confirmed_at=confirmed_at,
            )
        if _is_pivot_high(self.candles, pivot_index, self.pivot_span):
            self._merge_or_create(
                price=pivot.high,
                half_width=half_width,
                role="resistance",
                origin_at=pivot.closed_at,
                confirmed_at=confirmed_at,
            )

    def process_one(self, index: int) -> None:
        if index != self.processed_index + 1:
            raise ValueError("candles must be processed sequentially")
        candle = self.candles[index]
        self._expire_old_phases(candle.closed_at)
        current_atr = self.atr[index]
        if current_atr is not None and current_atr > 0:
            for phase in tuple(self._active_phases()):
                self._update_phase(phase, candle, index, current_atr)
        self._confirm_pivots(index)
        self.processed_index = index

    def advance_until(self, timestamp: datetime) -> None:
        while (
            self.processed_index + 1 < len(self.candles)
            and self.candles[self.processed_index + 1].closed_at < timestamp
        ):
            self.process_one(self.processed_index + 1)

    def process_through(self, timestamp: datetime) -> None:
        while (
            self.processed_index + 1 < len(self.candles)
            and self.candles[self.processed_index + 1].closed_at <= timestamp
        ):
            self.process_one(self.processed_index + 1)

    def current_atr(self) -> float | None:
        if self.processed_index < 0:
            return None
        return self.atr[self.processed_index]

    def nearest_aligned_phase(
        self, direction: Direction, entry_price: float
    ) -> ZonePhase | None:
        role: Role = "support" if direction == "Long" else "resistance"
        candidates = [phase for phase in self._active_phases() if phase.role == role]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda phase: (
                _distance_to_phase_band(entry_price, phase),
                -int(phase.armed_for_retest),
                -phase.source_pivots,
                -phase.phase_started_at.timestamp(),
                phase.phase_id,
            ),
        )

    def approach_features(
        self, phase: ZonePhase
    ) -> tuple[float | None, float | None, float | None]:
        atr = self.current_atr()
        if atr is None or atr <= 0 or self.processed_index < 2:
            return None, None, None
        start = max(0, self.processed_index - APPROACH_BARS + 1)
        selected = self.candles[start : self.processed_index + 1]
        distances = [_distance_to_phase_band(candle.close, phase) / atr for candle in selected]
        if not distances:
            return None, None, None
        near_fraction = (
            sum(value <= APPROACH_NEAR_DISTANCE_ATR for value in distances) / len(distances)
        )
        slope = _linear_slope(distances)
        distance_range = max(distances) - min(distances)
        return near_fraction, slope, distance_range

    def finalized_touch_events(self) -> list[LifecycleTouchEvent]:
        return [classify_touch_outcome(self.candles, event) for event in self._raw_touch_events]


def classify_touch_outcome(
    candles: Sequence[Candle],
    event: LifecycleTouchEvent,
    *,
    horizon_bars: int = TOUCH_OUTCOME_HORIZON_BARS,
) -> LifecycleTouchEvent:
    if horizon_bars <= 0:
        raise ValueError("horizon_bars must be positive")
    end_index = min(len(candles), event.event_index + 1 + horizon_bars)
    pending_break = 0
    for index in range(event.event_index + 1, end_index):
        candle = candles[index]
        if event.role == "support":
            beyond = candle.close < event.lower
            bounced = candle.close >= event.upper + REARM_DISTANCE_ATR * event.touch_atr
        else:
            beyond = candle.close > event.upper
            bounced = candle.close <= event.lower - REARM_DISTANCE_ATR * event.touch_atr

        if beyond:
            pending_break += 1
            if pending_break >= BREAK_CONFIRM_CLOSES:
                return _with_touch_outcome(event, "clean_break", index, candle.closed_at)
            continue
        if pending_break:
            return _with_touch_outcome(event, "false_break_reclaim", index, candle.closed_at)
        if bounced:
            return _with_touch_outcome(event, "bounce", index, candle.closed_at)
    return event


def _with_touch_outcome(
    event: LifecycleTouchEvent,
    outcome: TouchOutcome,
    index: int,
    at: datetime,
) -> LifecycleTouchEvent:
    return LifecycleTouchEvent(
        symbol=event.symbol,
        phase_id=event.phase_id,
        chain_id=event.chain_id,
        role=event.role,
        phase_origin=event.phase_origin,
        event_at=event.event_at,
        event_index=event.event_index,
        test_ordinal=event.test_ordinal,
        source_pivots=event.source_pivots,
        false_breaks_before=event.false_breaks_before,
        prior_phase_retests=event.prior_phase_retests,
        center=event.center,
        lower=event.lower,
        upper=event.upper,
        touch_atr=event.touch_atr,
        outcome=outcome,
        outcome_bars=index - event.event_index,
        outcome_at=at,
    )


def build_core_feature(
    signal: CoreSignal,
    *,
    detector: CleanZoneLifecycleDetector,
    start: datetime,
    calibration_days: int,
    p44_values: dict[tuple[str, str, str], float],
    p44_q25: dict[str, float],
) -> CoreLifecycleFeature:
    detector.advance_until(signal.touch_at)
    atr = detector.current_atr()
    phase = detector.nearest_aligned_phase(signal.direction, signal.entry_price)
    p44_value = p44_values.get(
        (signal.symbol, signal.direction, signal.touch_at.isoformat())
    )
    p44_q1: bool | None = None
    threshold = p44_q25.get(signal.symbol)
    if p44_value is not None and threshold is not None:
        p44_q1 = p44_value <= threshold

    if phase is None or atr is None or atr <= 0:
        return CoreLifecycleFeature(
            symbol=signal.symbol,
            display_symbol=DISPLAY_SYMBOLS.get(signal.symbol, signal.symbol.replace("USDT", "")),
            direction=signal.direction,
            touch_at=signal.touch_at,
            segment=segment_for(signal.touch_at, start, calibration_days),
            entry_price=signal.entry_price,
            outcome_05=signal.outcome_05,
            outcome_10=signal.outcome_10,
            phase_found=False,
            phase_id=None,
            chain_id=None,
            phase_role=None,
            phase_origin=None,
            chain_origin_role=None,
            phase_center=None,
            phase_lower=None,
            phase_upper=None,
            phase_age_hours=None,
            entry_distance_atr=None,
            entry_inside_zone=False,
            independent_test_ready=False,
            prior_phase_retests=None,
            current_test_ordinal=None,
            hours_since_last_retest=None,
            source_pivots=None,
            false_breaks=None,
            previous_rejection_atr=None,
            role_reversal_phase=False,
            first_retest_after_break=False,
            near_zone_fraction_2h=None,
            approach_slope_atr_per_bar=None,
            approach_distance_range_atr=None,
            p44_residual_15m_pct=p44_value,
            p44_residual_q1=p44_q1,
        )

    distance_atr = _distance_to_phase_band(signal.entry_price, phase) / atr
    near_fraction, slope, distance_range = detector.approach_features(phase)
    hours_since_last: float | None = None
    if phase.last_retest_at is not None:
        hours_since_last = (signal.touch_at - phase.last_retest_at).total_seconds() / 3600.0
    current_ordinal = phase.retest_count + 1 if phase.armed_for_retest else None
    return CoreLifecycleFeature(
        symbol=signal.symbol,
        display_symbol=DISPLAY_SYMBOLS.get(signal.symbol, signal.symbol.replace("USDT", "")),
        direction=signal.direction,
        touch_at=signal.touch_at,
        segment=segment_for(signal.touch_at, start, calibration_days),
        entry_price=signal.entry_price,
        outcome_05=signal.outcome_05,
        outcome_10=signal.outcome_10,
        phase_found=True,
        phase_id=phase.phase_id,
        chain_id=phase.chain_id,
        phase_role=phase.role,
        phase_origin=phase.phase_origin,
        chain_origin_role=phase.origin_role,
        phase_center=phase.center,
        phase_lower=phase.lower,
        phase_upper=phase.upper,
        phase_age_hours=(signal.touch_at - phase.phase_started_at).total_seconds() / 3600.0,
        entry_distance_atr=distance_atr,
        entry_inside_zone=distance_atr == 0.0,
        independent_test_ready=phase.armed_for_retest,
        prior_phase_retests=phase.prior_phase_retests,
        current_test_ordinal=current_ordinal,
        hours_since_last_retest=hours_since_last,
        source_pivots=phase.source_pivots,
        false_breaks=phase.false_breaks,
        previous_rejection_atr=(
            phase.rejection_max_atr if phase.last_retest_at is not None else None
        ),
        role_reversal_phase=phase.phase_origin == "role_reversal",
        first_retest_after_break=(
            phase.phase_origin == "role_reversal" and current_ordinal == 1
        ),
        near_zone_fraction_2h=near_fraction,
        approach_slope_atr_per_bar=slope,
        approach_distance_range_atr=distance_range,
        p44_residual_15m_pct=p44_value,
        p44_residual_q1=p44_q1,
    )


def _near(row: CoreLifecycleFeature, distance: float = NEAR_ZONE_ATR) -> bool:
    return (
        row.phase_found
        and row.entry_distance_atr is not None
        and row.entry_distance_atr <= distance
    )


def _is_test(row: CoreLifecycleFeature, ordinal: int) -> bool:
    return _near(row) and row.current_test_ordinal == ordinal


def frozen_rules() -> tuple[Rule, ...]:
    return (
        Rule("baseline", "baseline", lambda row: True),
        Rule("near_aligned_0_50atr", "distance", _near),
        Rule(
            "near_0_50_independent_retest_ready",
            "lifecycle",
            lambda row: _near(row) and row.independent_test_ready,
        ),
        Rule("near_0_50_test_1", "touch_ordinal", lambda row: _is_test(row, 1)),
        Rule("near_0_50_test_2", "touch_ordinal", lambda row: _is_test(row, 2)),
        Rule("near_0_50_test_3", "touch_ordinal", lambda row: _is_test(row, 3)),
        Rule(
            "near_0_50_test_4plus",
            "touch_ordinal",
            lambda row: _near(row) and (row.current_test_ordinal or 0) >= 4,
        ),
        Rule(
            "near_0_50_role_reversal_phase",
            "role_reversal",
            lambda row: _near(row) and row.role_reversal_phase,
        ),
        Rule(
            "near_0_50_first_retest_after_break",
            "role_reversal",
            lambda row: _near(row) and row.first_retest_after_break,
        ),
        Rule(
            "near_0_50_phase_lt24h",
            "phase_age",
            lambda row: _near(row)
            and row.phase_age_hours is not None
            and row.phase_age_hours < FRESH_PHASE_HOURS,
        ),
        Rule(
            "near_0_50_approach_toward",
            "approach",
            lambda row: _near(row)
            and row.approach_slope_atr_per_bar is not None
            and row.approach_slope_atr_per_bar < 0.0,
        ),
        Rule(
            "near_0_50_low_time_near",
            "approach",
            lambda row: _near(row)
            and row.near_zone_fraction_2h is not None
            and row.near_zone_fraction_2h < 0.5,
        ),
        Rule(
            "near_0_50_fresh_approach_fixed",
            "approach",
            lambda row: _near(row)
            and row.approach_slope_atr_per_bar is not None
            and row.approach_slope_atr_per_bar < 0.0
            and row.near_zone_fraction_2h is not None
            and row.near_zone_fraction_2h < 0.5,
        ),
        Rule("p44_residual_q1", "p44_exploratory", lambda row: row.p44_residual_q1 is True),
        Rule(
            "p44_q1_and_first_retest_after_break",
            "p44_exploratory",
            lambda row: row.p44_residual_q1 is True
            and _near(row)
            and row.first_retest_after_break,
        ),
        Rule(
            "p44_q1_and_fresh_approach_fixed",
            "p44_exploratory",
            lambda row: row.p44_residual_q1 is True
            and _near(row)
            and row.approach_slope_atr_per_bar is not None
            and row.approach_slope_atr_per_bar < 0.0
            and row.near_zone_fraction_2h is not None
            and row.near_zone_fraction_2h < 0.5,
        ),
    )


def _outcome_metrics(rows: Sequence[CoreLifecycleFeature]) -> dict[str, float | int | None]:
    count = len(rows)
    favorable05 = sum(row.outcome_05 == "favorable_first" for row in rows)
    adverse05 = sum(row.outcome_05 == "adverse_first" for row in rows)
    favorable10 = sum(row.outcome_10 == "favorable_first" for row in rows)
    adverse10 = sum(row.outcome_10 == "adverse_first" for row in rows)
    decisive05 = favorable05 + adverse05
    decisive10 = favorable10 + adverse10
    return {
        "sample": count,
        "favorable_05": favorable05,
        "adverse_05": adverse05,
        "neither_05": count - favorable05 - adverse05,
        "win_05_all_pct": None if count == 0 else 100.0 * favorable05 / count,
        "win_05_decisive_pct": None if decisive05 == 0 else 100.0 * favorable05 / decisive05,
        "favorable_10": favorable10,
        "adverse_10": adverse10,
        "neither_10": count - favorable10 - adverse10,
        "win_10_all_pct": None if count == 0 else 100.0 * favorable10 / count,
        "win_10_decisive_pct": None if decisive10 == 0 else 100.0 * favorable10 / decisive10,
    }


def _uplift(value: float | int | None, baseline: float | int | None) -> float | None:
    if not isinstance(value, (int, float)) or not isinstance(baseline, (int, float)):
        return None
    return float(value) - float(baseline)


def build_rule_matrix(
    rows: Sequence[CoreLifecycleFeature], symbols: Sequence[str]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for symbol in symbols:
        asset_rows = [row for row in rows if row.symbol == symbol]
        baseline = _outcome_metrics(asset_rows)
        for rule in frozen_rules():
            selected = [row for row in asset_rows if rule.predicate(row)]
            metrics = _outcome_metrics(selected)
            result.append(
                {
                    "symbol": symbol,
                    "rule": rule.name,
                    "family": rule.family,
                    **metrics,
                    "uplift_05_all_pp": _uplift(
                        metrics["win_05_all_pct"], baseline["win_05_all_pct"]
                    ),
                    "uplift_10_all_pp": _uplift(
                        metrics["win_10_all_pct"], baseline["win_10_all_pct"]
                    ),
                }
            )
    return result


def build_rule_transfer(matrix: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in matrix:
        grouped.setdefault(str(row["rule"]), []).append(row)
    result: list[dict[str, Any]] = []
    for rule_name, items in grouped.items():
        valid = [
            float(item["uplift_05_all_pp"])
            for item in items
            if isinstance(item.get("uplift_05_all_pp"), (int, float))
            and int(item.get("sample", 0)) > 0
        ]
        samples = [int(item.get("sample", 0)) for item in items]
        result.append(
            {
                "rule": rule_name,
                "family": str(items[0]["family"]),
                "assets_with_sample": len(valid),
                "assets_improved": sum(value > 0 for value in valid),
                "assets_worsened": sum(value < 0 for value in valid),
                "assets_equal": sum(value == 0 for value in valid),
                "median_uplift_05_all_pp": None if not valid else statistics.median(valid),
                "min_uplift_05_all_pp": None if not valid else min(valid),
                "max_uplift_05_all_pp": None if not valid else max(valid),
                "median_sample": None if not samples else statistics.median(samples),
                "total_sample": sum(samples),
            }
        )
    result.sort(key=lambda item: (str(item["family"]), str(item["rule"])))
    return result


def _ordinal_bucket(row: CoreLifecycleFeature) -> str:
    if not row.phase_found:
        return "no_zone"
    if not row.independent_test_ready or row.current_test_ordinal is None:
        return "not_rearmed"
    if row.current_test_ordinal == 1:
        return "first"
    if row.current_test_ordinal == 2:
        return "second"
    if row.current_test_ordinal == 3:
        return "third"
    return "fourth_plus"


def build_core_ordinal_matrix(rows: Sequence[CoreLifecycleFeature]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    buckets = ("not_rearmed", "first", "second", "third", "fourth_plus")
    for symbol in sorted({row.symbol for row in rows}):
        asset_rows = [row for row in rows if row.symbol == symbol]
        baseline = _outcome_metrics(asset_rows)
        near_rows = [row for row in asset_rows if _near(row)]
        for bucket in buckets:
            selected = [row for row in near_rows if _ordinal_bucket(row) == bucket]
            metrics = _outcome_metrics(selected)
            result.append(
                {
                    "symbol": symbol,
                    "touch_bucket": bucket,
                    **metrics,
                    "uplift_vs_asset_baseline_05_all_pp": _uplift(
                        metrics["win_05_all_pct"], baseline["win_05_all_pct"]
                    ),
                }
            )
    return result


def build_segment_rule_matrix(rows: Sequence[CoreLifecycleFeature]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for symbol in sorted({row.symbol for row in rows}):
        for segment in ("S1", "S2", "S3"):
            selected_segment = [
                row for row in rows if row.symbol == symbol and row.segment == segment
            ]
            baseline = _outcome_metrics(selected_segment)
            for rule in frozen_rules():
                selected = [row for row in selected_segment if rule.predicate(row)]
                metrics = _outcome_metrics(selected)
                result.append(
                    {
                        "symbol": symbol,
                        "segment": segment,
                        "rule": rule.name,
                        "family": rule.family,
                        **metrics,
                        "uplift_05_all_pp": _uplift(
                            metrics["win_05_all_pct"], baseline["win_05_all_pct"]
                        ),
                    }
                )
    return result


def build_direction_rule_matrix(rows: Sequence[CoreLifecycleFeature]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for symbol in sorted({row.symbol for row in rows}):
        for direction in ("Long", "Short"):
            selected_direction = [
                row for row in rows if row.symbol == symbol and row.direction == direction
            ]
            baseline = _outcome_metrics(selected_direction)
            for rule in frozen_rules():
                selected = [row for row in selected_direction if rule.predicate(row)]
                metrics = _outcome_metrics(selected)
                result.append(
                    {
                        "symbol": symbol,
                        "direction": direction,
                        "rule": rule.name,
                        "family": rule.family,
                        **metrics,
                        "uplift_05_all_pp": _uplift(
                            metrics["win_05_all_pct"], baseline["win_05_all_pct"]
                        ),
                    }
                )
    return result


QUARTILE_FEATURES: tuple[str, ...] = (
    "phase_age_hours",
    "entry_distance_atr",
    "hours_since_last_retest",
    "near_zone_fraction_2h",
    "approach_slope_atr_per_bar",
    "approach_distance_range_atr",
)


def _feature_value(row: CoreLifecycleFeature, feature: str) -> float | None:
    value = getattr(row, feature)
    return float(value) if isinstance(value, (int, float)) else None


def build_thresholds(rows: Sequence[CoreLifecycleFeature]) -> list[FeatureThreshold]:
    result: list[FeatureThreshold] = []
    for symbol in sorted({row.symbol for row in rows}):
        s1 = [row for row in rows if row.symbol == symbol and row.segment == "S1"]
        for feature in QUARTILE_FEATURES:
            values = sorted(
                value
                for row in s1
                if _near(row)
                for value in [_feature_value(row, feature)]
                if value is not None and math.isfinite(value)
            )
            if len(values) < 8:
                continue
            result.append(
                FeatureThreshold(
                    symbol=symbol,
                    feature=feature,
                    sample=len(values),
                    q25=_quantile(values, 0.25),
                    q50=_quantile(values, 0.50),
                    q75=_quantile(values, 0.75),
                )
            )
    return result


def build_quartile_oos(
    rows: Sequence[CoreLifecycleFeature], thresholds: Sequence[FeatureThreshold]
) -> list[dict[str, Any]]:
    lookup = {(item.symbol, item.feature): item for item in thresholds}
    result: list[dict[str, Any]] = []
    for symbol in sorted({row.symbol for row in rows}):
        oos = [
            row
            for row in rows
            if row.symbol == symbol and row.segment in {"S2", "S3"} and _near(row)
        ]
        baseline = _outcome_metrics(oos)
        for feature in QUARTILE_FEATURES:
            threshold = lookup.get((symbol, feature))
            if threshold is None:
                continue
            for quartile in ("Q1", "Q2", "Q3", "Q4"):
                selected = [
                    row
                    for row in oos
                    if (value := _feature_value(row, feature)) is not None
                    and classify_quartile(value, threshold) == quartile
                ]
                metrics = _outcome_metrics(selected)
                result.append(
                    {
                        "symbol": symbol,
                        "feature": feature,
                        "quartile": quartile,
                        **metrics,
                        "uplift_vs_near_zone_oos_05_all_pp": _uplift(
                            metrics["win_05_all_pct"], baseline["win_05_all_pct"]
                        ),
                    }
                )
    return result


def build_fresh_approach_oos(
    rows: Sequence[CoreLifecycleFeature], thresholds: Sequence[FeatureThreshold]
) -> list[dict[str, Any]]:
    lookup = {(item.symbol, item.feature): item for item in thresholds}
    result: list[dict[str, Any]] = []
    for symbol in sorted({row.symbol for row in rows}):
        slope_threshold = lookup.get((symbol, "approach_slope_atr_per_bar"))
        near_threshold = lookup.get((symbol, "near_zone_fraction_2h"))
        if slope_threshold is None or near_threshold is None:
            continue
        for segment in ("S2", "S3", "S2+S3"):
            selected_segments: tuple[Segment, ...]
            if segment == "S2+S3":
                selected_segments = ("S2", "S3")
            else:
                selected_segments = (cast(Segment, segment),)
            base = [
                row
                for row in rows
                if row.symbol == symbol and row.segment in selected_segments and _near(row)
            ]
            baseline = _outcome_metrics(base)
            selected = [
                row
                for row in base
                if row.approach_slope_atr_per_bar is not None
                and classify_quartile(
                    row.approach_slope_atr_per_bar, slope_threshold
                )
                == "Q1"
                and row.near_zone_fraction_2h is not None
                and classify_quartile(row.near_zone_fraction_2h, near_threshold) == "Q1"
            ]
            metrics = _outcome_metrics(selected)
            result.append(
                {
                    "symbol": symbol,
                    "segment": segment,
                    "candidate": "s1_q1_slope_and_q1_time_near",
                    **metrics,
                    "uplift_vs_near_zone_05_all_pp": _uplift(
                        metrics["win_05_all_pct"], baseline["win_05_all_pct"]
                    ),
                }
            )
    return result


def _touch_metrics(rows: Sequence[LifecycleTouchEvent]) -> TouchOutcomeMetrics:
    count = len(rows)
    bounce = sum(row.outcome == "bounce" for row in rows)
    clean_break = sum(row.outcome == "clean_break" for row in rows)
    false_break = sum(row.outcome == "false_break_reclaim" for row in rows)
    unresolved = count - bounce - clean_break - false_break
    return TouchOutcomeMetrics(
        sample=count,
        bounce=bounce,
        clean_break=clean_break,
        false_break_reclaim=false_break,
        unresolved=unresolved,
        bounce_pct=None if count == 0 else 100.0 * bounce / count,
        clean_break_pct=None if count == 0 else 100.0 * clean_break / count,
        false_break_reclaim_pct=None if count == 0 else 100.0 * false_break / count,
        unresolved_pct=None if count == 0 else 100.0 * unresolved / count,
    )


def _touch_bucket(event: LifecycleTouchEvent) -> str:
    if event.test_ordinal == 1:
        return "first"
    if event.test_ordinal == 2:
        return "second"
    if event.test_ordinal == 3:
        return "third"
    return "fourth_plus"


def build_touch_outcome_matrix(
    events: Sequence[LifecycleTouchEvent], symbols: Sequence[str]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for symbol in symbols:
        asset = [event for event in events if event.symbol == symbol]
        slices: list[tuple[str, list[LifecycleTouchEvent]]] = [("all", asset)]
        for bucket in ("first", "second", "third", "fourth_plus"):
            slices.append((bucket, [event for event in asset if _touch_bucket(event) == bucket]))
        slices.append(
            (
                "role_reversal_first",
                [
                    event
                    for event in asset
                    if event.phase_origin == "role_reversal" and event.test_ordinal == 1
                ],
            )
        )
        for label, selected in slices:
            metrics = _touch_metrics(selected)
            result.append({"symbol": symbol, "touch_bucket": label, **asdict(metrics)})
    return result


def build_touch_outcome_transfer(matrix: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for bucket in ("all", "first", "second", "third", "fourth_plus", "role_reversal_first"):
        items = [row for row in matrix if row["touch_bucket"] == bucket and int(row["sample"]) > 0]
        result.append(
            {
                "touch_bucket": bucket,
                "assets_with_sample": len(items),
                "total_sample": sum(int(item["sample"]) for item in items),
                "median_sample": None
                if not items
                else statistics.median(int(item["sample"]) for item in items),
                "median_bounce_pct": _median_numeric(items, "bounce_pct"),
                "median_clean_break_pct": _median_numeric(items, "clean_break_pct"),
                "median_false_break_reclaim_pct": _median_numeric(
                    items, "false_break_reclaim_pct"
                ),
                "median_unresolved_pct": _median_numeric(items, "unresolved_pct"),
            }
        )
    return result


def _median_numeric(rows: Sequence[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]
    return None if not values else statistics.median(values)


def build_phase_catalog(symbol: str, phases: Sequence[ZonePhase]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": symbol,
            "phase_id": phase.phase_id,
            "chain_id": phase.chain_id,
            "center": phase.center,
            "lower": phase.lower,
            "upper": phase.upper,
            "chain_origin_at": phase.chain_origin_at.isoformat(),
            "phase_started_at": phase.phase_started_at.isoformat(),
            "confirmed_at": phase.confirmed_at.isoformat(),
            "origin_role": phase.origin_role,
            "role": phase.role,
            "phase_origin": phase.phase_origin,
            "source_pivots": phase.source_pivots,
            "support_pivots": phase.support_pivots,
            "resistance_pivots": phase.resistance_pivots,
            "prior_phase_id": phase.prior_phase_id,
            "prior_phase_retests": phase.prior_phase_retests,
            "retest_count": phase.retest_count,
            "last_retest_at": None
            if phase.last_retest_at is None
            else phase.last_retest_at.isoformat(),
            "false_breaks": phase.false_breaks,
            "active": phase.active,
            "ended_at": None if phase.ended_at is None else phase.ended_at.isoformat(),
            "end_reason": phase.end_reason,
        }
        for phase in phases
    ]


def load_legacy_p45_ordinals(
    root: Path, *, start: datetime, end: datetime
) -> dict[tuple[str, str, str], int | None]:
    path = (
        root
        / "reports"
        / "multi_touch_sr_p45"
        / f"ENTRY_V1_{start:%Y%m%d}_{end:%Y%m%d}"
        / "core_zone_features.csv"
    )
    if not path.is_file():
        return {}
    result: dict[tuple[str, str, str], int | None] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"symbol", "direction", "touch_at", "current_test_ordinal"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            return {}
        for raw in reader:
            ordinal_text = raw["current_test_ordinal"].strip()
            ordinal = None if not ordinal_text else int(float(ordinal_text))
            key = (
                raw["symbol"],
                raw["direction"],
                parse_datetime(raw["touch_at"]).isoformat(),
            )
            result[key] = ordinal
    return result


def build_legacy_comparison(
    rows: Sequence[CoreLifecycleFeature], legacy: dict[tuple[str, str, str], int | None]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        key = (row.symbol, row.direction, row.touch_at.isoformat())
        if key not in legacy:
            continue
        old = legacy[key]
        new = row.current_test_ordinal
        result.append(
            {
                "symbol": row.symbol,
                "direction": row.direction,
                "touch_at": row.touch_at.isoformat(),
                "legacy_p45_test_ordinal": old,
                "clean_lifecycle_test_ordinal": new,
                "clean_independent_test_ready": row.independent_test_ready,
                "legacy_fourth_plus": old is not None and old >= 4,
                "clean_fourth_plus": new is not None and new >= 4,
            }
        )
    return result


def _summary_markdown(
    *,
    rows: Sequence[CoreLifecycleFeature],
    transfer: Sequence[dict[str, Any]],
    touch_transfer: Sequence[dict[str, Any]],
    phases: Sequence[dict[str, Any]],
    legacy_comparison: Sequence[dict[str, Any]],
) -> str:
    near_count = sum(_near(row) for row in rows)
    ready_count = sum(_near(row) and row.independent_test_ready for row in rows)
    role_reversal_first = sum(_near(row) and row.first_retest_after_break for row in rows)
    ended_break = sum(row.get("end_reason") == "confirmed_break" for row in phases)
    ended_age = sum(row.get("end_reason") == "age_expiry" for row in phases)
    legacy_old_fourth = sum(bool(row["legacy_fourth_plus"]) for row in legacy_comparison)
    legacy_new_fourth = sum(bool(row["clean_fourth_plus"]) for row in legacy_comparison)
    lines = [
        "# P45.1 Clean Zone Lifecycle",
        "",
        "Discovery on the same frozen 90-day interval. No live-rule promotion is allowed.",
        "",
        f"Core signals: **{len(rows)}**.",
        f"Near aligned active phase <=0.50 ATR: **{near_count}**.",
        f"Independent retest-ready near-phase signals: **{ready_count}**.",
        f"First retest after confirmed role reversal: **{role_reversal_first}**.",
        (
            f"Lifecycle phases: **{len(phases)}**; confirmed breaks={ended_break}; "
            f"age expiries={ended_age}."
        ),
        "",
        "## Frozen lifecycle semantics",
        "",
        "- Same causal 15m pivot 2+2 and ATR(200) geometry as P45.",
        "- Zone half-width = 0.50 ATR; independent retest rearms after 1.00 ATR excursion.",
        "- A phase ends after 2 consecutive closes through the far edge.",
        "- Confirmed break starts a NEW opposite-role phase with retest ordinal reset to zero.",
        "- A phase also expires after 7 days (168h) regardless of additional pivots.",
        "- New pivots merge only into active phases of the same role.",
        "- Core ordinal is assigned only if the phase is actually re-armed at exact touch_at.",
        "- Touch outcome horizon = 96 x 15m bars (24h).",
        "",
        "## Core Entry rule transfer (discovery)",
        "",
        "| Rule | improved | worsened | median uplift +0.5/-1 | total sample |",
        "|---|---:|---:|---:|---:|",
    ]
    for item in transfer:
        if item["rule"] == "baseline":
            continue
        uplift = item["median_uplift_05_all_pp"]
        uplift_text = "n/a" if uplift is None else f"{float(uplift):+.2f} pp"
        lines.append(
            f"| {item['rule']} | {item['assets_improved']} | {item['assets_worsened']} | "
            f"{uplift_text} | {item['total_sample']} |"
        )
    lines.extend(
        [
            "",
            "## Independent zone-test outcomes",
            "",
            "| Test | median bounce | clean break | false break+reclaim | total sample |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for item in touch_transfer:
        bounce = item["median_bounce_pct"]
        broken = item["median_clean_break_pct"]
        false_break = item["median_false_break_reclaim_pct"]
        lines.append(
            f"| {item['touch_bucket']} | {_pct(bounce)} | {_pct(broken)} | "
            f"{_pct(false_break)} | {item['total_sample']} |"
        )
    if legacy_comparison:
        lines.extend(
            [
                "",
                "## P45 -> P45.1 lifecycle reset diagnostic",
                "",
                f"Legacy P45 4th+ labels: **{legacy_old_fourth}**.",
                (
                    "Clean lifecycle 4th+ labels on the same matched signals: "
                    f"**{legacy_new_fourth}**."
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Guardrails",
            "",
            "- P45.1 is still discovery on the already inspected 90-day interval.",
            (
                "- The 7-day lifecycle boundary was already present in P45 as the "
                "frozen old-zone boundary."
            ),
            (
                "- Fresh-approach features are preserved because P45 discovered them; "
                "they are not confirmed."
            ),
            "- P44 residual interactions remain exploratory and require a new temporal holdout.",
            "- No market data is downloaded and no live trading / Exit / Risk logic is modified.",
            "",
        ]
    )
    return "\n".join(lines)


def _pct(value: object) -> str:
    return "n/a" if not isinstance(value, (int, float)) else f"{float(value):.2f}%"


def _zip_report(output_dir: Path) -> Path:
    zip_path = output_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output_dir.parent))
    return zip_path


def run_analysis(
    *,
    root: Path,
    start: datetime,
    end: datetime,
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    calibration_days: int = 30,
    force: bool = False,
) -> Path:
    if end <= start:
        raise ValueError("end must be after start")
    if calibration_days <= 0:
        raise ValueError("calibration_days must be positive")
    output_dir = (
        root
        / "reports"
        / "clean_zone_lifecycle_p451"
        / f"ENTRY_V1_{start:%Y%m%d}_{end:%Y%m%d}"
    )
    if output_dir.exists() and not force:
        raise FileExistsError(f"P45.1 output already exists; rerun with --force: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    print("P45.1 PRECHECK - frozen local 15m + completed P40 only; no network")
    dataset_dirs: dict[str, Path] = {}
    signal_paths: dict[str, Path] = {}
    source_manifest: list[dict[str, Any]] = []
    for symbol in symbols:
        dataset_dir, source = resolve_frozen_dataset_dir(
            root, symbol=symbol, start=start, end=end
        )
        candle_path = dataset_dir / "trade_15m.csv"
        signal_path = validation_root(root, symbol, start, end) / "p40" / "absorption_features.csv"
        if not candle_path.is_file():
            raise FileNotFoundError(f"missing frozen 15m data for {symbol}: {candle_path}")
        if not signal_path.is_file():
            raise FileNotFoundError(f"missing completed P40 core for {symbol}: {signal_path}")
        dataset_dirs[symbol] = dataset_dir
        signal_paths[symbol] = signal_path
        source_manifest.append(
            {
                "symbol": symbol,
                "dataset_source": source,
                "trade_15m": str(candle_path),
                "trade_15m_sha256": _sha256_file(candle_path),
                "p40_core": str(signal_path),
                "p40_core_sha256": _sha256_file(signal_path),
            }
        )
        print(f"  OK {symbol}: 15m [{source}] + P40 core")

    p44_values, p44_q25 = _load_p44_context(root, start=start, end=end)
    p44_available = bool(p44_values and p44_q25)
    print(f"P44 residual context: {'available' if p44_available else 'not found; join skipped'}")

    feature_rows: list[CoreLifecycleFeature] = []
    all_touch_events: list[LifecycleTouchEvent] = []
    phase_catalog: list[dict[str, Any]] = []
    for asset_index, symbol in enumerate(symbols, start=1):
        print(f"P45.1 ASSET {asset_index}/{len(symbols)}: {symbol}")
        candles = load_candles(dataset_dirs[symbol] / "trade_15m.csv")
        signals = load_core_signals(signal_paths[symbol], symbol=symbol, start=start, end=end)
        detector = CleanZoneLifecycleDetector(symbol, candles)
        for signal_index, signal in enumerate(signals, start=1):
            feature_rows.append(
                build_core_feature(
                    signal,
                    detector=detector,
                    start=start,
                    calibration_days=calibration_days,
                    p44_values=p44_values,
                    p44_q25=p44_q25,
                )
            )
            if signal_index % 50 == 0 or signal_index == len(signals):
                active = sum(phase.active for phase in detector.phases)
                print(
                    f"  signals {signal_index}/{len(signals)} phases={len(detector.phases)} "
                    f"active={active} retests={len(detector._raw_touch_events)}"
                )
        detector.process_through(end)
        events = detector.finalized_touch_events()
        all_touch_events.extend(events)
        phase_catalog.extend(build_phase_catalog(symbol, detector.phases))
        print(
            f"  lifecycle complete: phases={len(detector.phases)} "
            f"independent_touches={len(events)}"
        )

    feature_rows.sort(key=lambda row: (row.touch_at, row.symbol, row.direction))
    all_touch_events.sort(key=lambda row: (row.event_at, row.symbol, row.phase_id))
    rule_matrix = build_rule_matrix(feature_rows, symbols)
    transfer = build_rule_transfer(rule_matrix)
    ordinal_matrix = build_core_ordinal_matrix(feature_rows)
    segment_matrix = build_segment_rule_matrix(feature_rows)
    direction_matrix = build_direction_rule_matrix(feature_rows)
    thresholds = build_thresholds(feature_rows)
    quartile_oos = build_quartile_oos(feature_rows, thresholds)
    fresh_approach_oos = build_fresh_approach_oos(feature_rows, thresholds)
    touch_matrix = build_touch_outcome_matrix(all_touch_events, symbols)
    touch_transfer = build_touch_outcome_transfer(touch_matrix)
    legacy = load_legacy_p45_ordinals(root, start=start, end=end)
    legacy_comparison = build_legacy_comparison(feature_rows, legacy)

    _write_dataclass_csv(output_dir / "core_lifecycle_features.csv", feature_rows)
    _write_dataclass_csv(output_dir / "independent_zone_touch_outcomes.csv", all_touch_events)
    _write_csv(output_dir / "phase_catalog.csv", phase_catalog)
    _write_csv(output_dir / "asset_rule_matrix.csv", rule_matrix)
    _write_csv(output_dir / "cross_asset_rule_transfer.csv", transfer)
    _write_csv(output_dir / "core_touch_ordinal_matrix.csv", ordinal_matrix)
    _write_csv(output_dir / "segment_rule_matrix.csv", segment_matrix)
    _write_csv(output_dir / "direction_rule_matrix.csv", direction_matrix)
    _write_dataclass_csv(output_dir / "s1_feature_thresholds.csv", thresholds)
    _write_csv(output_dir / "feature_quartiles_oos.csv", quartile_oos)
    _write_csv(output_dir / "fresh_approach_combo_oos.csv", fresh_approach_oos)
    _write_csv(output_dir / "zone_touch_outcome_matrix.csv", touch_matrix)
    _write_csv(output_dir / "cross_asset_touch_outcome_transfer.csv", touch_transfer)
    _write_csv(output_dir / "legacy_p45_lifecycle_comparison.csv", legacy_comparison)
    _write_csv(output_dir / "source_manifest.csv", source_manifest)

    near_count = sum(_near(row) for row in feature_rows)
    ready_count = sum(_near(row) and row.independent_test_ready for row in feature_rows)
    summary = {
        "architecture": "p45_1_clean_zone_lifecycle",
        "evaluation_start": start.isoformat(),
        "evaluation_end": end.isoformat(),
        "calibration_days": calibration_days,
        "symbols": list(symbols),
        "core_signals": len(feature_rows),
        "near_active_phase_0_5atr": near_count,
        "independent_retest_ready_near_0_5atr": ready_count,
        "lifecycle_phases": len(phase_catalog),
        "independent_zone_touches": len(all_touch_events),
        "p44_context_joined": p44_available,
        "legacy_p45_comparison_rows": len(legacy_comparison),
        "frozen_parameters": {
            "pivot_span": PIVOT_SPAN,
            "atr_period": ATR_PERIOD,
            "zone_half_width_atr": ZONE_HALF_WIDTH_ATR,
            "rearm_distance_atr": REARM_DISTANCE_ATR,
            "break_confirm_closes": BREAK_CONFIRM_CLOSES,
            "phase_max_age_hours": PHASE_MAX_AGE_HOURS,
            "touch_outcome_horizon_bars": TOUCH_OUTCOME_HORIZON_BARS,
            "touch_outcome_horizon_hours": TOUCH_OUTCOME_HORIZON_BARS * 0.25,
            "approach_bars": APPROACH_BARS,
            "approach_near_distance_atr": APPROACH_NEAR_DISTANCE_ATR,
            "near_zone_atr": NEAR_ZONE_ATR,
        },
        "guardrails": [
            "Only 15m candles closed strictly before exact touch_at are visible to core features.",
            (
                "Confirmed break ends the phase and starts a new opposite-role phase "
                "with ordinal reset."
            ),
            "A phase expires after 168h even if new pivots continue to appear.",
            "A core touch ordinal exists only when the phase has re-armed by a 1 ATR excursion.",
            "P45.1 is discovery on the already inspected 90-day interval, not confirmatory OOS.",
            "Fresh-approach and P44 interactions require a new temporal holdout before promotion.",
            "No market data is downloaded.",
            "No live trading, Exit, Risk, leverage, or execution logic is modified.",
        ],
    }
    _write_json(output_dir / "summary.json", summary)
    (output_dir / "summary.md").write_text(
        _summary_markdown(
            rows=feature_rows,
            transfer=transfer,
            touch_transfer=touch_transfer,
            phases=phase_catalog,
            legacy_comparison=legacy_comparison,
        ),
        encoding="utf-8",
    )
    _write_json(
        output_dir / "RUN_COMPLETE.json",
        {
            "complete": True,
            "core_signals": len(feature_rows),
            "lifecycle_phases": len(phase_catalog),
            "independent_zone_touches": len(all_touch_events),
            "generated_at": datetime.now(UTC).isoformat(),
        },
    )
    zip_path = _zip_report(output_dir)
    print(
        f"P45.1 COMPLETE: core={len(feature_rows)} near_0.5ATR={near_count} "
        f"retest_ready={ready_count} touches={len(all_touch_events)}"
    )
    print(f"Summary: {output_dir / 'summary.md'}")
    print(f"Result ZIP: {zip_path}")
    return zip_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P45.1 clean S/R zone lifecycle research")
    parser.add_argument("--root", default="C:/cripta")
    parser.add_argument("--start", default="2026-05-18T00:00:00+00:00")
    parser.add_argument("--end", default="2026-08-16T00:00:00+00:00")
    parser.add_argument("--calibration-days", type=int, default=30)
    parser.add_argument("--symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_analysis(
        root=Path(args.root),
        start=parse_datetime(args.start),
        end=parse_datetime(args.end),
        symbols=tuple(args.symbols),
        calibration_days=int(args.calibration_days),
        force=bool(args.force),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
