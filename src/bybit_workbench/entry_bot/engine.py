from __future__ import annotations

import hashlib
import json
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import RLock

from bybit_workbench.domain.models import Candle
from bybit_workbench.strategies.indicators import true_ranges, wilder_atr

from .config import EntryBotConfig
from .models import (
    ArmedCandidate,
    AssetScanStatus,
    Direction,
    EntryBotAssetSnapshot,
    EntryBotAuditEvent,
    EntryBotCalibration,
    EntrySignalEvent,
    EntryZone,
    FlowFeatures,
    OiFeatures,
)


@dataclass(slots=True)
class TradeFlowBucket:
    opened_at: datetime
    buy_notional: Decimal = Decimal("0")
    sell_notional: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class OiPoint:
    timestamp: datetime
    open_interest: Decimal


@dataclass(slots=True)
class _TrackedOutcome:
    direction: Direction
    entry_price: Decimal
    touch_at: datetime
    expires_at: datetime


@dataclass(slots=True)
class _AuditTrackedOutcome:
    candidate_id: str
    direction: Direction
    entry_price: Decimal
    touch_at: datetime
    expires_at: datetime
    early_result: str | None = None
    hit_plus_050: bool = False
    hit_plus_100: bool = False
    hit_minus_100: bool = False
    hit_minus_300: bool = False
    recovered_entry_after_minus_100: bool = False
    recovered_plus_010_after_minus_100: bool = False
    first_minus_100_at: datetime | None = None
    max_favorable_pct: Decimal = Decimal("0")
    max_adverse_pct: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class _DistanceMarker:
    candidate_id: str
    band: str
    direction: Direction
    entry_price: Decimal
    distance_pct: Decimal
    prelimit_shadow_armed: bool


@dataclass(frozen=True, slots=True)
class CoreGateResult:
    allowed: bool
    oi_tail_danger: bool | None
    reason: str


def floor_time(timestamp: datetime, minutes: int) -> datetime:
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    if minutes <= 0:
        raise ValueError("minutes must be positive")
    utc = timestamp.astimezone(UTC)
    minute = (utc.minute // minutes) * minutes
    return utc.replace(minute=minute, second=0, microsecond=0)


def _zone_gap_percent(
    first_low: Decimal,
    first_high: Decimal,
    second_low: Decimal,
    second_high: Decimal,
    reference: Decimal,
) -> Decimal:
    if reference <= 0:
        raise ValueError("reference price must be positive")
    if max(first_low, second_low) <= min(first_high, second_high):
        return Decimal("0")
    gap = second_low - first_high if first_high < second_low else first_low - second_high
    return abs(gap) / reference * Decimal("100")


def compute_latest_zone(
    candles: tuple[Candle, ...],
    *,
    timeframe: str,
    lookback: int,
    atr_period: int,
    width_atr: Decimal,
    shock_atr_period: int,
    shock_atr_multiple: Decimal,
    minimum_regime_bars: int,
) -> EntryZone | None:
    """Production copy of the frozen P30 causal post-shock zone calculation."""

    count = len(candles)
    minimum_history = max(lookback, atr_period)
    if count < minimum_history:
        return None
    ranges = true_ranges(candles)
    atr_values = wilder_atr(candles, atr_period)
    atr = atr_values[-1]
    if atr is None or atr <= 0:
        return None

    shock_flags = [False] * count
    if shock_atr_period > 0 and count > shock_atr_period:
        rolling = sum(ranges[:shock_atr_period], Decimal("0"))
        for index in range(shock_atr_period, count):
            baseline = rolling / Decimal(shock_atr_period)
            shock_flags[index] = baseline > 0 and ranges[index] >= shock_atr_multiple * baseline
            rolling += ranges[index] - ranges[index - shock_atr_period]

    history_len = count
    last_index = history_len - 1
    window_start = history_len - lookback
    reset_index: int | None = None
    for index in range(last_index, max(window_start, shock_atr_period) - 1, -1):
        if shock_flags[index]:
            reset_index = index
            break
    selected_start = window_start
    reset_at: datetime | None = None
    if reset_index is not None:
        selected_start = reset_index + 1
        if history_len - selected_start < minimum_regime_bars:
            return None
        reset_at = candles[reset_index].closed_at
    selected = candles[selected_start:history_len]
    if not selected:
        return None
    range_high = max(item.high for item in selected)
    range_low = min(item.low for item in selected)
    width = width_atr * atr
    support_top = range_low + width
    support_bottom = range_low - width
    resistance_top = range_high + width
    resistance_bottom = range_high - width
    if support_top >= resistance_bottom:
        return None
    return EntryZone(
        timeframe=timeframe,
        observed_at=candles[last_index].closed_at,
        range_high=range_high,
        range_low=range_low,
        atr=atr,
        resistance_top=resistance_top,
        resistance_bottom=resistance_bottom,
        support_top=support_top,
        support_bottom=support_bottom,
        effective_lookback=len(selected),
        regime_reset_at=reset_at,
    )


def directional_delta_pct(direction: Direction, buy: Decimal, sell: Decimal) -> Decimal:
    total = buy + sell
    if total <= 0:
        return Decimal("0")
    raw = (buy - sell) / total * Decimal("100")
    return raw if direction == "Long" else -raw


def flow_features(
    direction: Direction,
    touch_at: datetime,
    buckets: dict[datetime, TradeFlowBucket],
) -> FlowFeatures:
    normalized = floor_time(touch_at, 1)
    reversal = buckets.get(normalized - timedelta(minutes=1))
    reversal_buy = Decimal("0") if reversal is None else reversal.buy_notional
    reversal_sell = Decimal("0") if reversal is None else reversal.sell_notional

    pressure_buy = Decimal("0")
    pressure_sell = Decimal("0")
    for offset in range(2, 6):
        bucket = buckets.get(normalized - timedelta(minutes=offset))
        if bucket is None:
            continue
        pressure_buy += bucket.buy_notional
        pressure_sell += bucket.sell_notional

    pressure = directional_delta_pct(direction, pressure_buy, pressure_sell)
    reversal_delta = directional_delta_pct(direction, reversal_buy, reversal_sell)
    if pressure < 0 and reversal_delta > 0:
        state = "pressure_then_reversal"
    elif pressure < 0 and reversal_delta <= 0:
        state = "pressure_continues"
    elif pressure >= 0 and reversal_delta > 0:
        state = "already_favorable"
    elif pressure > 0 and reversal_delta < 0:
        state = "favorable_then_fades"
    else:
        state = "neutral_or_mixed"
    return FlowFeatures(
        pressure_directional_delta_pct=pressure,
        reversal_directional_delta_pct=reversal_delta,
        pressure_total_notional=pressure_buy + pressure_sell,
        reversal_total_notional=reversal_buy + reversal_sell,
        state=state,
    )


def percent_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous <= 0:
        return None
    return (current / previous - Decimal("1")) * Decimal("100")


def oi_features_at(points: tuple[OiPoint, ...], anchor_at: datetime) -> OiFeatures | None:
    if not points:
        return None
    ordered = tuple(sorted(points, key=lambda item: item.timestamp))

    def at_or_before(timestamp: datetime) -> OiPoint | None:
        for item in reversed(ordered):
            if item.timestamp <= timestamp:
                return item
        return None

    current = at_or_before(anchor_at)
    five = at_or_before(anchor_at - timedelta(minutes=5))
    sixty = at_or_before(anchor_at - timedelta(minutes=60))
    if current is None or five is None or sixty is None:
        return None
    change_5m = percent_change(current.open_interest, five.open_interest)
    change_60m = percent_change(current.open_interest, sixty.open_interest)
    if change_5m is None or change_60m is None:
        return None
    return OiFeatures(
        change_5m_pct=change_5m,
        change_60m_pct=change_60m,
        acceleration_5_vs_60=change_5m - change_60m / Decimal("12"),
        anchor_at=current.timestamp,
    )


def evaluate_core_gate(
    *,
    flow: FlowFeatures,
    oi: OiFeatures | None,
    calibration: EntryBotCalibration | None,
    accepted_after_failure_embargo: bool,
    require_oi_calibration: bool = True,
) -> CoreGateResult:
    if not accepted_after_failure_embargo:
        return CoreGateResult(False, None, "60m failure embargo")
    if flow.state != "pressure_then_reversal":
        return CoreGateResult(False, None, f"flow={flow.state}")
    if calibration is None and require_oi_calibration:
        return CoreGateResult(False, None, "missing frozen OI calibration")
    if oi is None and require_oi_calibration:
        return CoreGateResult(False, None, "OI history is not ready")
    if calibration is None or oi is None:
        return CoreGateResult(True, None, "ENTRY V1 core; OI is observation only")
    danger = (
        oi.change_60m_pct >= calibration.high_oi_change_60m_pct
        or oi.acceleration_5_vs_60 <= calibration.low_oi_acceleration_5_vs_60
    )
    if danger:
        return CoreGateResult(False, True, "OI tail danger")
    return CoreGateResult(True, False, "ENTRY V1 core")


class EntrySymbolEngine:
    """Causal per-symbol scanner. It owns no authenticated/write transport."""

    def __init__(
        self,
        symbol: str,
        config: EntryBotConfig,
        calibration: EntryBotCalibration | None,
    ) -> None:
        selected = symbol.strip().upper()
        if selected not in config.working_symbols:
            raise ValueError(f"{selected} is not in the frozen bot universe")
        self.symbol = selected
        self.config = config
        self.calibration = calibration
        self._lock = RLock()
        self._candles: dict[str, deque[Candle]] = {
            "5": deque(maxlen=config.history_limit),
            "15": deque(maxlen=config.history_limit),
            "60": deque(maxlen=config.history_limit),
        }
        self._oi: deque[OiPoint] = deque(maxlen=400)
        self._flow: dict[datetime, TradeFlowBucket] = {}
        self._current_bar_open: datetime | None = None
        self._bar_reference_price: Decimal | None = None
        self._candidate: ArmedCandidate | None = None
        self._candidate_cooldown_until: datetime | None = None
        self._failure_embargo_until: datetime | None = None
        self._hourly_swing_blocked = False
        self._hourly_swing_pct: Decimal | None = None
        self._outcomes: list[_TrackedOutcome] = []
        self._audit_outcomes: list[_AuditTrackedOutcome] = []
        self._audit_events: deque[EntryBotAuditEvent] = deque(maxlen=4000)
        self._distance_marker: _DistanceMarker | None = None
        self._last_price: Decimal | None = None
        self._last_update: datetime | None = None
        self._last_flow_state = "—"
        self._last_oi_state = "—"
        self._last_signal: EntrySignalEvent | None = None
        self._error: str | None = None
        self._history_ready = False

    def set_calibration(self, calibration: EntryBotCalibration | None) -> None:
        with self._lock:
            self.calibration = calibration

    def drain_audit_events(self) -> tuple[EntryBotAuditEvent, ...]:
        with self._lock:
            rows = tuple(self._audit_events)
            self._audit_events.clear()
            return rows

    def export_production_state(self, now: datetime) -> dict[str, str | None]:
        observed = now.astimezone(UTC)
        with self._lock:
            if self._failure_embargo_until is not None and observed >= self._failure_embargo_until:
                self._failure_embargo_until = None
            return {
                "failure_embargo_until": (
                    self._failure_embargo_until.isoformat()
                    if self._failure_embargo_until is not None
                    else None
                )
            }

    def restore_production_state(
        self, payload: dict[str, object], now: datetime
    ) -> None:
        raw = payload.get("failure_embargo_until")
        restored = datetime.fromisoformat(str(raw)).astimezone(UTC) if raw else None
        observed = now.astimezone(UTC)
        with self._lock:
            self._failure_embargo_until = (
                restored if restored is not None and restored > observed else None
            )

    def _emit_audit(
        self,
        *,
        occurred_at: datetime,
        event_type: str,
        status: str,
        reason: str,
        candidate_id: str | None = None,
        direction: Direction | None = None,
        candidate_bar_at: datetime | None = None,
        entry_price: Decimal | None = None,
        last_price: Decimal | None = None,
        distance_pct: Decimal | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        payload_json = json.dumps(
            payload or {},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        self._audit_events.append(
            EntryBotAuditEvent(
                event_id=uuid.uuid4().hex,
                occurred_at=occurred_at.astimezone(UTC),
                symbol=self.symbol,
                event_type=event_type,
                status=status,
                candidate_id=candidate_id,
                direction=direction,
                candidate_bar_at=candidate_bar_at,
                entry_price=entry_price,
                last_price=last_price,
                distance_pct=distance_pct,
                flow_state=self._last_flow_state,
                oi_state=self._last_oi_state,
                reason=reason,
                payload_json=payload_json,
            )
        )

    def load_history(
        self,
        candles: dict[str, tuple[Candle, ...]],
        oi_points: tuple[OiPoint, ...],
        *,
        observed_at: datetime,
    ) -> None:
        with self._lock:
            for timeframe in ("5", "15", "60"):
                rows = candles.get(timeframe, ())
                target = self._candles[timeframe]
                target.clear()
                for candle in sorted(rows, key=lambda item: item.opened_at):
                    if candle.symbol != self.symbol or candle.timeframe != timeframe:
                        raise ValueError("warmup candle symbol/timeframe mismatch")
                    if candle.is_closed:
                        target.append(candle)
            self._oi.clear()
            self._oi.extend(sorted(oi_points, key=lambda item: item.timestamp))
            self._flow.clear()
            self._last_update = observed_at
            self._error = None
            self._history_ready = True
            self._ensure_candidate(floor_time(observed_at, 5))

    def mark_warmup_error(self, observed_at: datetime, detail: str) -> None:
        with self._lock:
            self._history_ready = False
            self._set_candidate(None, observed_at=observed_at, reason="warm-up error")
            self._flow.clear()
            self._error = detail
            self._last_update = observed_at.astimezone(UTC)
            self._emit_audit(
                occurred_at=observed_at,
                event_type="WARMUP_ERROR",
                status="ERROR",
                reason=detail,
            )

    def mark_stream_gap(self, observed_at: datetime, detail: str) -> None:
        with self._lock:
            self._flow.clear()
            self._set_candidate(None, observed_at=observed_at, reason="public stream gap")
            self._error = detail
            self._last_update = observed_at
            self._emit_audit(
                occurred_at=observed_at,
                event_type="STREAM_GAP",
                status="WARMUP",
                reason=detail,
            )

    def _flow_window_progress(self, observed_at: datetime) -> tuple[int, int]:
        normalized = floor_time(observed_at.astimezone(UTC), 1)
        required = self.config.public_trade_flow_warmup_minutes
        present = sum(
            1
            for offset in range(1, required + 1)
            if normalized - timedelta(minutes=offset) in self._flow
        )
        return present, required

    def _flow_window_ready(self, observed_at: datetime) -> bool:
        present, required = self._flow_window_progress(observed_at)
        return present == required

    def on_closed_candle(self, candle: Candle) -> None:
        if not candle.is_closed:
            return
        if candle.symbol != self.symbol or candle.timeframe not in self._candles:
            raise ValueError("closed candle does not belong to this scanner")
        with self._lock:
            if not self._history_ready:
                return
            target = self._candles[candle.timeframe]
            if target and target[-1].opened_at == candle.opened_at:
                target[-1] = candle
            elif not target or target[-1].opened_at < candle.opened_at:
                target.append(candle)
            else:
                merged = {item.opened_at: item for item in target}
                merged[candle.opened_at] = candle
                target.clear()
                target.extend(sorted(merged.values(), key=lambda item: item.opened_at))
            self._last_update = candle.closed_at
            self._error = None
            current = self._current_bar_open
            if candle.timeframe == "5" and (current is None or candle.closed_at > current):
                self._current_bar_open = candle.closed_at
                self._bar_reference_price = candle.close
                current = candle.closed_at
            if current is None or candle.closed_at >= current:
                self._ensure_candidate(max(candle.closed_at, current or candle.closed_at))

    def on_current_five_minute_open(
        self,
        opened_at: datetime,
        open_price: Decimal,
        observed_at: datetime,
    ) -> None:
        if open_price <= 0:
            return
        with self._lock:
            if not self._history_ready:
                return
            self._current_bar_open = opened_at.astimezone(UTC)
            self._bar_reference_price = open_price
            self._last_update = observed_at
            self._ensure_candidate(self._current_bar_open)

    def on_open_interest(self, value: Decimal, observed_at: datetime) -> None:
        if value <= 0:
            return
        point = OiPoint(observed_at.astimezone(UTC), value)
        with self._lock:
            if not self._history_ready:
                return
            if self._oi and point.timestamp <= self._oi[-1].timestamp:
                return
            self._oi.append(point)
            self._last_update = point.timestamp

    def on_trade(
        self,
        *,
        price: Decimal,
        size: Decimal,
        taker_side: str,
        traded_at: datetime,
    ) -> EntrySignalEvent | None:
        if price <= 0 or size < 0:
            raise ValueError("trade price/size is invalid")
        if taker_side not in {"Buy", "Sell"}:
            raise ValueError(f"unsupported taker side: {taker_side!r}")
        observed = traded_at.astimezone(UTC)
        with self._lock:
            self._last_price = price
            self._last_update = observed
            if not self._history_ready:
                return None
            self._error = None
            self._record_flow(price, size, taker_side, observed)
            self._update_outcomes(price, observed)
            self._update_audit_outcomes(price, observed)
            bar_open = floor_time(observed, 5)
            if self._current_bar_open != bar_open:
                self._current_bar_open = bar_open
                self._bar_reference_price = price
                self._ensure_candidate(bar_open)
            self._audit_distance_transition(observed)
            candidate = self._candidate
            if candidate is None or candidate.bar_opened_at != bar_open:
                return None
            direction: Direction | None = None
            entry: Decimal | None = None
            gap: Decimal | None = None
            if candidate.long_entry is not None and price <= candidate.long_entry:
                direction = "Long"
                entry = candidate.long_entry
                gap = candidate.long_gap_pct
            elif candidate.short_entry is not None and price >= candidate.short_entry:
                direction = "Short"
                entry = candidate.short_entry
                gap = candidate.short_gap_pct
            if direction is None or entry is None or gap is None:
                return None

            candidate_id = self._candidate_id(candidate, direction, entry)
            marker = self._distance_marker
            if (
                marker is not None
                and marker.candidate_id == candidate_id
                and marker.prelimit_shadow_armed
            ):
                self._emit_audit(
                    occurred_at=observed,
                    event_type="PRELIMIT_TOUCH_SHADOW",
                    status="APPROACH",
                    reason="candidate price touched; shadow pre-limit would have been fillable",
                    candidate_id=candidate_id,
                    direction=direction,
                    candidate_bar_at=candidate.bar_opened_at,
                    entry_price=entry,
                    last_price=price,
                    distance_pct=Decimal("0"),
                    payload={"order_sent": False},
                )
            self._set_candidate(None, observed_at=observed, reason="touch")
            self._candidate_cooldown_until = bar_open + timedelta(
                minutes=self.config.candidate_cooldown_minutes
            )
            accepted = (
                self._failure_embargo_until is None or observed >= self._failure_embargo_until
            )
            self._start_audit_outcome(
                candidate_id=candidate_id,
                direction=direction,
                entry_price=entry,
                touch_at=observed,
            )

            flow = flow_features(direction, observed, self._flow)
            self._last_flow_state = flow.state
            oi = candidate.oi_features
            self._last_oi_state = self._oi_state(oi)
            if not self._flow_window_ready(observed):
                self._emit_audit(
                    occurred_at=observed,
                    event_type="TOUCH_BLOCKED",
                    status="BLOCKED",
                    reason="live tape 4+1 window incomplete at exact touch",
                    candidate_id=candidate_id,
                    direction=direction,
                    candidate_bar_at=bar_open,
                    entry_price=entry,
                    last_price=price,
                    payload={"flow_state": flow.state, "accepted_after_embargo": accepted},
                )
                return None
            gate = evaluate_core_gate(
                flow=flow,
                oi=oi,
                calibration=self.calibration,
                accepted_after_failure_embargo=accepted,
                require_oi_calibration=self.config.require_oi_calibration,
            )
            if not gate.allowed:
                self._emit_audit(
                    occurred_at=observed,
                    event_type="TOUCH_VETO",
                    status="BLOCKED",
                    reason=gate.reason if oi is not None else "open interest unavailable",
                    candidate_id=candidate_id,
                    direction=direction,
                    candidate_bar_at=bar_open,
                    entry_price=entry,
                    last_price=price,
                    payload={
                        "flow_state": flow.state,
                        "oi_state": self._last_oi_state,
                        "accepted_after_embargo": accepted,
                    },
                )
                return None
            self._outcomes.append(
                _TrackedOutcome(
                    direction=direction,
                    entry_price=entry,
                    touch_at=observed,
                    expires_at=observed
                    + timedelta(minutes=self.config.candidate_outcome_horizon_minutes),
                )
            )
            signal = EntrySignalEvent(
                signal_id=_signal_id(self.symbol, direction, bar_open, entry),
                strategy_id="entry_v1_core",
                strategy_version="1.0-live-first-touch",
                symbol=self.symbol,
                direction=direction,
                candidate_bar_at=bar_open,
                touch_at=observed,
                entry_price=entry,
                flow=flow,
                oi=oi,
                zone_gap_pct=gap,
            )
            self._last_signal = signal
            self._emit_audit(
                occurred_at=observed,
                event_type="CORE_SIGNAL",
                status="SIGNAL",
                reason=gate.reason,
                candidate_id=candidate_id,
                direction=direction,
                candidate_bar_at=bar_open,
                entry_price=entry,
                last_price=price,
                payload={
                    "signal_id": signal.signal_id,
                    "flow_state": flow.state,
                    "oi_state": self._last_oi_state,
                    "zone_gap_pct": str(gap),
                },
            )
            return signal

    def snapshot(self, now: datetime) -> EntryBotAssetSnapshot:
        observed = now.astimezone(UTC)
        with self._lock:
            if self._error is not None:
                status = AssetScanStatus.ERROR
                detail = self._error
            elif self.calibration is None and self.config.require_oi_calibration:
                status = AssetScanStatus.NO_CALIBRATION
                detail = "OI calibration missing; entry is fail-closed"
            elif self._failure_embargo_until is not None and observed < self._failure_embargo_until:
                status = AssetScanStatus.COOLDOWN
                detail = f"failure embargo until {self._failure_embargo_until.isoformat()}"
            elif self._hourly_swing_blocked:
                status = AssetScanStatus.COOLDOWN
                detail = f"previous 60m swing {self._hourly_swing_pct}% exceeds limit"
            elif (
                self._candidate_cooldown_until is not None
                and observed < self._candidate_cooldown_until
            ):
                status = AssetScanStatus.COOLDOWN
                detail = f"candidate cooldown until {self._candidate_cooldown_until.isoformat()}"
            elif not self._flow_window_ready(observed):
                status = AssetScanStatus.WARMUP
                flow_present, flow_required = self._flow_window_progress(observed)
                detail = (
                    f"live tape warm-up: {flow_present}/{flow_required} completed minute buckets; "
                    "Entry remains fail-closed until all 4+1 causal minutes are present"
                )
            elif (
                self._last_signal is not None
                and observed - self._last_signal.touch_at < timedelta(minutes=2)
            ):
                status = AssetScanStatus.SIGNAL
                detail = f"CORE {self._last_signal.direction} · {self._last_signal.signal_id}"
            else:
                distance, candidate_side, candidate_entry = self._nearest_candidate()
                if distance is None:
                    status = AssetScanStatus.WAITING
                    detail = "waiting for 5m+15m causal confluence"
                elif distance <= self.config.approach_display_percent:
                    status = AssetScanStatus.APPROACH
                    detail = (
                        "price is approaching armed Entry V1 level; "
                        "shadow pre-limit intent is audited, no order is sent"
                    )
                elif distance <= self.config.watch_display_percent:
                    status = AssetScanStatus.WATCH
                    detail = "armed confluence is within watch distance"
                else:
                    status = AssetScanStatus.WAITING
                    detail = "armed confluence exists"
                return EntryBotAssetSnapshot(
                    symbol=self.symbol,
                    status=status,
                    side=candidate_side,
                    last_price=self._last_price,
                    entry_price=candidate_entry,
                    distance_pct=distance,
                    flow_state=self._last_flow_state,
                    oi_state=self._last_oi_state,
                    updated_at=self._last_update,
                    detail=detail,
                    last_signal_id=(
                        None if self._last_signal is None else self._last_signal.signal_id
                    ),
                )
            side: Direction | None = None
            entry_price: Decimal | None = None
            distance_pct: Decimal | None = None
            if self._last_signal is not None and status is AssetScanStatus.SIGNAL:
                side = self._last_signal.direction
                entry_price = self._last_signal.entry_price
            visible_flow_state = self._last_flow_state
            if status is AssetScanStatus.WARMUP:
                flow_present, flow_required = self._flow_window_progress(observed)
                visible_flow_state = f"TAPE {flow_present}/{flow_required}"
            return EntryBotAssetSnapshot(
                symbol=self.symbol,
                status=status,
                side=side,
                last_price=self._last_price,
                entry_price=entry_price,
                distance_pct=distance_pct,
                flow_state=visible_flow_state,
                oi_state=self._last_oi_state,
                updated_at=self._last_update,
                detail=detail,
                last_signal_id=None if self._last_signal is None else self._last_signal.signal_id,
            )

    def _candidate_id(
        self,
        candidate: ArmedCandidate,
        direction: Direction,
        entry_price: Decimal,
    ) -> str:
        raw = (
            f"{self.symbol}|{candidate.bar_opened_at.isoformat()}|"
            f"{direction}|{entry_price}"
        ).encode()
        return hashlib.sha256(raw).hexdigest()[:24]

    def _set_candidate(
        self,
        candidate: ArmedCandidate | None,
        *,
        observed_at: datetime,
        reason: str,
    ) -> None:
        previous = self._candidate
        if previous == candidate:
            self._candidate = candidate
            return
        marker = self._distance_marker
        if marker is not None and marker.prelimit_shadow_armed and reason != "touch":
            self._emit_audit(
                occurred_at=observed_at,
                event_type="PRELIMIT_CANCEL_SHADOW",
                status="APPROACH",
                reason=reason,
                candidate_id=marker.candidate_id,
                direction=marker.direction,
                entry_price=marker.entry_price,
                last_price=self._last_price,
                distance_pct=marker.distance_pct,
                payload={"order_sent": False},
            )
        self._distance_marker = None
        self._candidate = candidate
        if previous is not None and candidate is None:
            self._emit_audit(
                occurred_at=observed_at,
                event_type="CANDIDATE_CLEARED",
                status="WAITING",
                reason=reason,
                candidate_bar_at=previous.bar_opened_at,
                last_price=self._last_price,
            )
        if candidate is None:
            return
        candidate_sides: tuple[
            tuple[Direction, Decimal | None, Decimal | None], ...
        ] = (
            ("Long", candidate.long_entry, candidate.long_gap_pct),
            ("Short", candidate.short_entry, candidate.short_gap_pct),
        )
        for direction, entry_price, gap in candidate_sides:
            if entry_price is None:
                continue
            candidate_id = self._candidate_id(candidate, direction, entry_price)
            self._emit_audit(
                occurred_at=observed_at,
                event_type="CANDIDATE_ARMED",
                status="WAITING",
                reason=reason,
                candidate_id=candidate_id,
                direction=direction,
                candidate_bar_at=candidate.bar_opened_at,
                entry_price=entry_price,
                last_price=self._last_price,
                payload={
                    "zone_gap_pct": None if gap is None else str(gap),
                    "bar_reference_price": str(candidate.bar_reference_price),
                },
            )

    def _distance_band(self, distance_pct: Decimal) -> str:
        if distance_pct <= self.config.approach_display_percent:
            return "APPROACH"
        if distance_pct <= self.config.watch_display_percent:
            return "WATCH"
        return "FAR"

    def _audit_distance_transition(self, observed_at: datetime) -> None:
        distance, direction, entry_price = self._nearest_candidate()
        candidate = self._candidate
        if (
            distance is None
            or direction is None
            or entry_price is None
            or candidate is None
        ):
            self._distance_marker = None
            return
        candidate_id = self._candidate_id(candidate, direction, entry_price)
        band = self._distance_band(distance)
        shadow_ready = (
            band == "APPROACH"
            and self._flow_window_ready(observed_at)
            and self.calibration is not None
            and (
                self._failure_embargo_until is None
                or observed_at >= self._failure_embargo_until
            )
        )
        marker = _DistanceMarker(
            candidate_id, band, direction, entry_price, distance, shadow_ready
        )
        previous = self._distance_marker
        if previous == marker:
            return
        if previous is not None and previous.prelimit_shadow_armed and (
            previous.candidate_id != candidate_id or not shadow_ready
        ):
            self._emit_audit(
                occurred_at=observed_at,
                event_type="PRELIMIT_CANCEL_SHADOW",
                status="APPROACH",
                reason="green shadow pre-limit eligibility ended",
                candidate_id=previous.candidate_id,
                direction=previous.direction,
                entry_price=previous.entry_price,
                last_price=self._last_price,
                distance_pct=previous.distance_pct,
                payload={"order_sent": False},
            )
        if previous is None or previous.candidate_id != candidate_id or previous.band != band:
            display_status = "WAITING" if band == "FAR" else band
            self._emit_audit(
                occurred_at=observed_at,
                event_type="DISTANCE_BAND",
                status=display_status,
                reason=f"distance band entered: {band}",
                candidate_id=candidate_id,
                direction=direction,
                candidate_bar_at=candidate.bar_opened_at,
                entry_price=entry_price,
                last_price=self._last_price,
                distance_pct=distance,
                payload={"band": band},
            )
        if shadow_ready and (
            previous is None
            or previous.candidate_id != candidate_id
            or not previous.prelimit_shadow_armed
        ):
            self._emit_audit(
                occurred_at=observed_at,
                event_type="PRELIMIT_ARM_SHADOW",
                status="APPROACH",
                reason="green approach band; shadow intent only",
                candidate_id=candidate_id,
                direction=direction,
                candidate_bar_at=candidate.bar_opened_at,
                entry_price=entry_price,
                last_price=self._last_price,
                distance_pct=distance,
                payload={"order_sent": False, "limit_price": str(entry_price)},
            )
        self._distance_marker = marker

    def _start_audit_outcome(
        self,
        *,
        candidate_id: str,
        direction: Direction,
        entry_price: Decimal,
        touch_at: datetime,
    ) -> None:
        self._audit_outcomes.append(
            _AuditTrackedOutcome(
                candidate_id=candidate_id,
                direction=direction,
                entry_price=entry_price,
                touch_at=touch_at,
                expires_at=touch_at
                + timedelta(minutes=self.config.candidate_outcome_horizon_minutes),
            )
        )

    def _update_audit_outcomes(self, price: Decimal, observed: datetime) -> None:
        remaining: list[_AuditTrackedOutcome] = []
        for outcome in self._audit_outcomes:
            if observed > outcome.expires_at:
                self._emit_audit(
                    occurred_at=observed,
                    event_type="OUTCOME_EXPIRED",
                    status="AUDIT",
                    reason="diagnostic outcome horizon completed",
                    candidate_id=outcome.candidate_id,
                    direction=outcome.direction,
                    entry_price=outcome.entry_price,
                    last_price=price,
                    payload={
                        "early_result": outcome.early_result,
                        "max_favorable_pct": str(outcome.max_favorable_pct),
                        "max_adverse_pct": str(outcome.max_adverse_pct),
                        "horizon_minutes": self.config.candidate_outcome_horizon_minutes,
                    },
                )
                continue
            if outcome.direction == "Long":
                move_pct = (price - outcome.entry_price) / outcome.entry_price * Decimal("100")
            else:
                move_pct = (outcome.entry_price - price) / outcome.entry_price * Decimal("100")
            outcome.max_favorable_pct = max(outcome.max_favorable_pct, move_pct)
            outcome.max_adverse_pct = min(outcome.max_adverse_pct, move_pct)
            if outcome.early_result is None:
                if move_pct >= Decimal("0.10"):
                    outcome.early_result = "PLUS_0_10_FIRST"
                    self._emit_audit(
                        occurred_at=observed,
                        event_type="EARLY_DIRECTION_CONFIRMED",
                        status="OUTCOME",
                        reason="+0.10% reached before -1.00%",
                        candidate_id=outcome.candidate_id,
                        direction=outcome.direction,
                        entry_price=outcome.entry_price,
                        last_price=price,
                        payload={"move_pct": str(move_pct)},
                    )
                elif move_pct <= Decimal("-1.00"):
                    outcome.early_result = "MINUS_1_00_FIRST"
                    self._emit_audit(
                        occurred_at=observed,
                        event_type="EARLY_FAILURE",
                        status="OUTCOME",
                        reason="-1.00% reached before +0.10%",
                        candidate_id=outcome.candidate_id,
                        direction=outcome.direction,
                        entry_price=outcome.entry_price,
                        last_price=price,
                        payload={"move_pct": str(move_pct)},
                    )
            if not outcome.hit_plus_050 and move_pct >= Decimal("0.50"):
                outcome.hit_plus_050 = True
                self._emit_audit(
                    occurred_at=observed,
                    event_type="MILESTONE_PLUS_0_50",
                    status="OUTCOME",
                    reason="favorable +0.50% reached",
                    candidate_id=outcome.candidate_id,
                    direction=outcome.direction,
                    entry_price=outcome.entry_price,
                    last_price=price,
                    payload={"move_pct": str(move_pct)},
                )
            if not outcome.hit_plus_100 and move_pct >= Decimal("1.00"):
                outcome.hit_plus_100 = True
                self._emit_audit(
                    occurred_at=observed,
                    event_type="MILESTONE_PLUS_1_00",
                    status="OUTCOME",
                    reason="favorable +1.00% reached",
                    candidate_id=outcome.candidate_id,
                    direction=outcome.direction,
                    entry_price=outcome.entry_price,
                    last_price=price,
                    payload={"move_pct": str(move_pct)},
                )
            if not outcome.hit_minus_100 and move_pct <= Decimal("-1.00"):
                outcome.hit_minus_100 = True
                outcome.first_minus_100_at = observed
                self._emit_audit(
                    occurred_at=observed,
                    event_type="MILESTONE_MINUS_1_00",
                    status="OUTCOME",
                    reason="adverse -1.00% reached",
                    candidate_id=outcome.candidate_id,
                    direction=outcome.direction,
                    entry_price=outcome.entry_price,
                    last_price=price,
                    payload={"move_pct": str(move_pct)},
                )
            if not outcome.hit_minus_300 and move_pct <= Decimal("-3.00"):
                outcome.hit_minus_300 = True
                self._emit_audit(
                    occurred_at=observed,
                    event_type="MILESTONE_MINUS_3_00",
                    status="OUTCOME",
                    reason="adverse -3.00% reached",
                    candidate_id=outcome.candidate_id,
                    direction=outcome.direction,
                    entry_price=outcome.entry_price,
                    last_price=price,
                    payload={"move_pct": str(move_pct)},
                )
            if (
                outcome.hit_minus_100
                and not outcome.recovered_entry_after_minus_100
                and move_pct >= Decimal("0")
            ):
                outcome.recovered_entry_after_minus_100 = True
                elapsed = (
                    None
                    if outcome.first_minus_100_at is None
                    else int((observed - outcome.first_minus_100_at).total_seconds())
                )
                self._emit_audit(
                    occurred_at=observed,
                    event_type="RECOVERED_ENTRY_AFTER_MINUS_1",
                    status="OUTCOME",
                    reason="price recovered to entry after -1.00% adverse move",
                    candidate_id=outcome.candidate_id,
                    direction=outcome.direction,
                    entry_price=outcome.entry_price,
                    last_price=price,
                    payload={"seconds_after_minus_1": elapsed, "move_pct": str(move_pct)},
                )
            if (
                outcome.hit_minus_100
                and not outcome.recovered_plus_010_after_minus_100
                and move_pct >= Decimal("0.10")
            ):
                outcome.recovered_plus_010_after_minus_100 = True
                self._emit_audit(
                    occurred_at=observed,
                    event_type="RECOVERED_PLUS_0_10_AFTER_MINUS_1",
                    status="OUTCOME",
                    reason="price recovered to +0.10% after -1.00% adverse move",
                    candidate_id=outcome.candidate_id,
                    direction=outcome.direction,
                    entry_price=outcome.entry_price,
                    last_price=price,
                    payload={"move_pct": str(move_pct)},
                )
            remaining.append(outcome)
        self._audit_outcomes = remaining

    def _record_flow(
        self,
        price: Decimal,
        size: Decimal,
        taker_side: str,
        observed: datetime,
    ) -> None:
        minute = floor_time(observed, 1)
        bucket = self._flow.get(minute)
        if bucket is None:
            bucket = TradeFlowBucket(minute)
            self._flow[minute] = bucket
        notional = price * size
        if taker_side == "Buy":
            bucket.buy_notional += notional
        else:
            bucket.sell_notional += notional
        cutoff = minute - timedelta(minutes=10)
        stale = [key for key in self._flow if key < cutoff]
        for key in stale:
            del self._flow[key]

    def _update_outcomes(self, price: Decimal, observed: datetime) -> None:
        remaining: list[_TrackedOutcome] = []
        for outcome in self._outcomes:
            if observed > outcome.expires_at:
                continue
            if outcome.direction == "Long":
                favorable = price >= outcome.entry_price * Decimal("1.005")
                adverse = price <= outcome.entry_price * Decimal("0.99")
            else:
                favorable = price <= outcome.entry_price * Decimal("0.995")
                adverse = price >= outcome.entry_price * Decimal("1.01")
            if favorable:
                continue
            if adverse:
                self._failure_embargo_until = observed + timedelta(
                    minutes=self.config.failure_embargo_minutes
                )
                continue
            remaining.append(outcome)
        self._outcomes = remaining

    def _ensure_candidate(self, bar_open: datetime) -> None:
        bar_open = bar_open.astimezone(UTC)
        observed_at = self._last_update or bar_open
        if self._candidate_cooldown_until is not None and bar_open < self._candidate_cooldown_until:
            self._set_candidate(
                None, observed_at=observed_at, reason="candidate cooldown active"
            )
            return
        expected = {
            "5": bar_open,
            "15": floor_time(bar_open, 15),
            "60": floor_time(bar_open, 60),
        }
        for timeframe, expected_close in expected.items():
            rows = self._candles[timeframe]
            if not rows or rows[-1].closed_at < expected_close:
                self._set_candidate(
                    None, observed_at=observed_at, reason=f"{timeframe}m history not closed"
                )
                return
        five = tuple(self._candles["5"])
        fifteen = tuple(self._candles["15"])
        config = self.config
        recent_hour = five[-12:]
        self._hourly_swing_blocked = False
        self._hourly_swing_pct = None
        if len(recent_hour) == 12:
            hour_low = min(item.low for item in recent_hour)
            hour_high = max(item.high for item in recent_hour)
            if hour_low > 0:
                self._hourly_swing_pct = (hour_high / hour_low - Decimal("1")) * Decimal("100")
                self._hourly_swing_blocked = (
                    self._hourly_swing_pct >= config.hourly_swing_pause_percent
                )
        if self._hourly_swing_blocked:
            self._set_candidate(
                None,
                observed_at=observed_at,
                reason=(
                    f"previous 60m swing {self._hourly_swing_pct:.3f}% >= "
                    f"{config.hourly_swing_pause_percent}%"
                ),
            )
            return
        five_zone = compute_latest_zone(
            five,
            timeframe="5",
            lookback=config.five_minute_lookback,
            atr_period=config.atr_period,
            width_atr=config.zone_half_width_atr,
            shock_atr_period=config.shock_atr_period,
            shock_atr_multiple=config.shock_atr_multiple,
            minimum_regime_bars=max(1, config.embargo_minutes_after_shock // 5),
        )
        fifteen_zone = compute_latest_zone(
            fifteen,
            timeframe="15",
            lookback=config.fifteen_minute_lookback,
            atr_period=config.atr_period,
            width_atr=config.zone_half_width_atr,
            shock_atr_period=config.shock_atr_period,
            shock_atr_multiple=config.shock_atr_multiple,
            minimum_regime_bars=max(1, config.embargo_minutes_after_shock // 15),
        )
        if five_zone is None or fifteen_zone is None:
            self._set_candidate(
                None, observed_at=observed_at, reason="causal zone unavailable"
            )
            return
        reference = self._bar_reference_price
        if reference is None or reference <= 0:
            reference = five[-1].close
        long_gap = _zone_gap_percent(
            fifteen_zone.support_bottom,
            fifteen_zone.support_top,
            five_zone.support_bottom,
            five_zone.support_top,
            reference,
        )
        short_gap = _zone_gap_percent(
            fifteen_zone.resistance_bottom,
            fifteen_zone.resistance_top,
            five_zone.resistance_bottom,
            five_zone.resistance_top,
            reference,
        )
        long_entry = (
            five_zone.support_top
            if long_gap <= config.confluence_max_gap_percent
            else None
        )
        short_entry = (
            five_zone.resistance_bottom
            if short_gap <= config.confluence_max_gap_percent
            else None
        )
        if long_entry is None and short_entry is None:
            self._set_candidate(
                None, observed_at=observed_at, reason="5m+15m confluence absent"
            )
            return
        oi = oi_features_at(tuple(self._oi), bar_open)
        self._current_bar_open = bar_open
        new_candidate = ArmedCandidate(
            symbol=self.symbol,
            bar_opened_at=bar_open,
            bar_reference_price=reference,
            long_entry=long_entry,
            short_entry=short_entry,
            long_gap_pct=long_gap if long_entry is not None else None,
            short_gap_pct=short_gap if short_entry is not None else None,
            oi_features=oi,
        )
        self._last_oi_state = self._oi_state(oi)
        self._set_candidate(
            new_candidate,
            observed_at=observed_at,
            reason="5m+15m causal confluence armed",
        )

    def _nearest_candidate(self) -> tuple[Decimal | None, Direction | None, Decimal | None]:
        candidate = self._candidate
        price = self._last_price
        if candidate is None or price is None or price <= 0:
            return None, None, None
        choices: list[tuple[Decimal, Direction, Decimal]] = []
        if candidate.long_entry is not None:
            choices.append(
                (
                    abs(price - candidate.long_entry) / price * Decimal("100"),
                    "Long",
                    candidate.long_entry,
                )
            )
        if candidate.short_entry is not None:
            choices.append(
                (
                    abs(price - candidate.short_entry) / price * Decimal("100"),
                    "Short",
                    candidate.short_entry,
                )
            )
        if not choices:
            return None, None, None
        return min(choices, key=lambda item: item[0])

    def _oi_state(self, oi: OiFeatures | None) -> str:
        if oi is None:
            return "missing"
        calibration = self.calibration
        if calibration is None:
            return "uncalibrated"
        danger = (
            oi.change_60m_pct >= calibration.high_oi_change_60m_pct
            or oi.acceleration_5_vs_60 <= calibration.low_oi_acceleration_5_vs_60
        )
        return "TAIL" if danger else "OK"


def _signal_id(
    symbol: str,
    direction: Direction,
    candidate_bar_at: datetime,
    entry_price: Decimal,
) -> str:
    raw = "|".join(
        (symbol, direction, candidate_bar_at.astimezone(UTC).isoformat(), str(entry_price))
    )
    return "entryv1-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
