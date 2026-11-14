from __future__ import annotations

import hashlib
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
) -> CoreGateResult:
    if not accepted_after_failure_embargo:
        return CoreGateResult(False, None, "60m failure embargo")
    if flow.state != "pressure_then_reversal":
        return CoreGateResult(False, None, f"flow={flow.state}")
    if calibration is None:
        return CoreGateResult(False, None, "missing frozen OI calibration")
    if oi is None:
        return CoreGateResult(False, None, "OI history is not ready")
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
        self._flow_ready_after: datetime | None = None
        self._current_bar_open: datetime | None = None
        self._bar_reference_price: Decimal | None = None
        self._candidate: ArmedCandidate | None = None
        self._candidate_cooldown_until: datetime | None = None
        self._failure_embargo_until: datetime | None = None
        self._outcomes: list[_TrackedOutcome] = []
        self._last_price: Decimal | None = None
        self._last_update: datetime | None = None
        self._last_flow_state = "—"
        self._last_oi_state = "—"
        self._last_signal: EntrySignalEvent | None = None
        self._error: str | None = None

    def set_calibration(self, calibration: EntryBotCalibration | None) -> None:
        with self._lock:
            self.calibration = calibration

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
            start_minute = floor_time(observed_at, 1)
            self._flow_ready_after = start_minute + timedelta(
                minutes=self.config.public_trade_flow_warmup_minutes
            )
            self._last_update = observed_at
            self._error = None
            self._ensure_candidate(floor_time(observed_at, 5))

    def mark_stream_gap(self, observed_at: datetime, detail: str) -> None:
        with self._lock:
            self._flow.clear()
            self._flow_ready_after = floor_time(observed_at, 1) + timedelta(
                minutes=self.config.public_trade_flow_warmup_minutes
            )
            self._candidate = None
            self._error = detail
            self._last_update = observed_at

    def on_closed_candle(self, candle: Candle) -> None:
        if not candle.is_closed:
            return
        if candle.symbol != self.symbol or candle.timeframe not in self._candles:
            raise ValueError("closed candle does not belong to this scanner")
        with self._lock:
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
            self._current_bar_open = opened_at.astimezone(UTC)
            self._bar_reference_price = open_price
            self._last_update = observed_at
            self._ensure_candidate(self._current_bar_open)

    def on_open_interest(self, value: Decimal, observed_at: datetime) -> None:
        if value <= 0:
            return
        point = OiPoint(observed_at.astimezone(UTC), value)
        with self._lock:
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
            self._error = None
            self._record_flow(price, size, taker_side, observed)
            self._update_outcomes(price, observed)
            bar_open = floor_time(observed, 5)
            if self._current_bar_open != bar_open:
                self._current_bar_open = bar_open
                self._bar_reference_price = price
                self._ensure_candidate(bar_open)
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

            self._candidate = None
            self._candidate_cooldown_until = bar_open + timedelta(
                minutes=self.config.candidate_cooldown_minutes
            )
            accepted = (
                self._failure_embargo_until is None or observed >= self._failure_embargo_until
            )
            if accepted:
                self._outcomes.append(
                    _TrackedOutcome(
                        direction=direction,
                        entry_price=entry,
                        touch_at=observed,
                        expires_at=observed
                        + timedelta(minutes=self.config.candidate_outcome_horizon_minutes),
                    )
                )

            flow = flow_features(direction, observed, self._flow)
            self._last_flow_state = flow.state
            oi = candidate.oi_features
            self._last_oi_state = self._oi_state(oi)
            flow_ready = self._flow_ready_after is not None and observed >= self._flow_ready_after
            if not flow_ready:
                return None
            gate = evaluate_core_gate(
                flow=flow,
                oi=oi,
                calibration=self.calibration,
                accepted_after_failure_embargo=accepted,
            )
            if not gate.allowed or oi is None:
                return None
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
            return signal

    def snapshot(self, now: datetime) -> EntryBotAssetSnapshot:
        observed = now.astimezone(UTC)
        with self._lock:
            if self._error is not None:
                status = AssetScanStatus.ERROR
                detail = self._error
            elif self.calibration is None:
                status = AssetScanStatus.NO_CALIBRATION
                detail = "OI calibration missing; entry is fail-closed"
            elif self._failure_embargo_until is not None and observed < self._failure_embargo_until:
                status = AssetScanStatus.COOLDOWN
                detail = f"failure embargo until {self._failure_embargo_until.isoformat()}"
            elif (
                self._candidate_cooldown_until is not None
                and observed < self._candidate_cooldown_until
            ):
                status = AssetScanStatus.COOLDOWN
                detail = f"candidate cooldown until {self._candidate_cooldown_until.isoformat()}"
            elif self._flow_ready_after is None or observed < self._flow_ready_after:
                status = AssetScanStatus.WARMUP
                detail = "collecting 4+1 completed minutes of public trades"
            elif (
                self._last_signal is not None
                and observed - self._last_signal.touch_at < timedelta(minutes=2)
            ):
                status = AssetScanStatus.SIGNAL
                detail = f"CORE {self._last_signal.direction} · {self._last_signal.signal_id}"
            else:
                distance, side, entry = self._nearest_candidate()
                if distance is None:
                    status = AssetScanStatus.WAITING
                    detail = "waiting for 5m+15m causal confluence"
                elif distance <= self.config.approach_display_percent:
                    status = AssetScanStatus.APPROACH
                    detail = "price is approaching armed Entry V1 level"
                elif distance <= self.config.watch_display_percent:
                    status = AssetScanStatus.WATCH
                    detail = "armed confluence is within watch distance"
                else:
                    status = AssetScanStatus.WAITING
                    detail = "armed confluence exists"
                return EntryBotAssetSnapshot(
                    symbol=self.symbol,
                    status=status,
                    side=side,
                    last_price=self._last_price,
                    entry_price=entry,
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
            return EntryBotAssetSnapshot(
                symbol=self.symbol,
                status=status,
                side=side,
                last_price=self._last_price,
                entry_price=entry_price,
                distance_pct=distance_pct,
                flow_state=self._last_flow_state,
                oi_state=self._last_oi_state,
                updated_at=self._last_update,
                detail=detail,
                last_signal_id=None if self._last_signal is None else self._last_signal.signal_id,
            )

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
        if self._candidate_cooldown_until is not None and bar_open < self._candidate_cooldown_until:
            self._candidate = None
            return
        expected = {
            "5": bar_open,
            "15": floor_time(bar_open, 15),
            "60": floor_time(bar_open, 60),
        }
        for timeframe, expected_close in expected.items():
            rows = self._candles[timeframe]
            if not rows or rows[-1].closed_at < expected_close:
                self._candidate = None
                return
        five = tuple(self._candles["5"])
        fifteen = tuple(self._candles["15"])
        config = self.config
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
            self._candidate = None
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
            self._candidate = None
            return
        oi = oi_features_at(tuple(self._oi), bar_open)
        self._current_bar_open = bar_open
        self._candidate = ArmedCandidate(
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
