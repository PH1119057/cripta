"""P90L90 LEGACY causal public-market Entry/Exit math, for SHADOW parity only.

Exact research reference: P90L90 90d v2, PREV7D, TRAIL035, RAW L5-3 TP.
This module creates NO exchange order, DB record, live admission or fill claim.
Account binding, SECOND48 cross-Strategy ownership and execution terms are
intentionally outside the model until separate owner-approved contracts exist.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from statistics import median
from typing import Literal

from bybit_workbench.domain.models import Candle
from bybit_workbench.strategies.indicators import wilder_atr

Side = Literal["LONG", "SHORT"]
ExitReason = Literal["HARD_STOP", "PROTECTED_STOP", "RESTING_TP", "MARKETABLE_TP"]
D0 = Decimal("0")
D05 = Decimal("0.5")
D035 = Decimal("0.35")
D100 = Decimal("100")
FIVE_MINUTES = timedelta(minutes=5)
FIFTEEN_MINUTES = timedelta(minutes=15)


def _closed_bars(
    bars: tuple[Candle, ...], *, timeframe: str, effective_at: datetime
) -> tuple[Candle, ...] | None:
    if effective_at.tzinfo is None:
        raise ValueError("effective_at must have timezone")
    if not bars:
        return None
    delta = FIVE_MINUTES if timeframe == "5" else FIFTEEN_MINUTES
    previous: datetime | None = None
    for bar in bars:
        if not bar.is_closed or bar.timeframe != timeframe:
            return None
        if bar.closed_at > effective_at:
            return None  # no future or unfinished candle
        if bar.opened_at.astimezone(UTC) + delta != bar.closed_at.astimezone(UTC):
            return None
        if previous is not None and bar.closed_at - previous != delta:
            return None  # missing candles cannot be filled by interpolation
        previous = bar.closed_at
    return bars


@dataclass(frozen=True, slots=True)
class P90L90EntryState:
    """Frozen, causal five-minute candidate state, no implied exchange fill."""

    effective_at: datetime
    l53_low_raw: Decimal
    l53_high_raw: Decimal
    h9_5m_low_raw: Decimal
    h9_5m_high_raw: Decimal
    h9_15m_low_raw: Decimal
    h9_15m_high_raw: Decimal
    raw_h9_union_span: Decimal
    working_width_pct: Decimal
    previous_seven_day_median: Decimal
    long_depth_pct: Decimal | None
    short_depth_pct: Decimal | None

    def entry_edge(self, side: Side) -> Decimal | None:
        if side == "LONG":
            return self.l53_low_raw if self.long_depth_pct is not None else None
        return self.l53_high_raw if self.short_depth_pct is not None else None

    def opposite_tp(self, side: Side) -> Decimal:
        return self.l53_high_raw if side == "LONG" else self.l53_low_raw


def causal_entry_state(
    candles_5m: tuple[Candle, ...],
    candles_15m: tuple[Candle, ...],
    *,
    effective_at: datetime,
) -> P90L90EntryState | None:
    # Historical P90L90 v2 computes with 50-digit Decimal precision.
    with localcontext(prec=50):
        return _causal_entry_state_precise(
            candles_5m, candles_15m, effective_at=effective_at
        )


def _causal_entry_state_precise(
    candles_5m: tuple[Candle, ...],
    candles_15m: tuple[Candle, ...],
    *,
    effective_at: datetime,
) -> P90L90EntryState | None:
    """Compute PREV7D and H9 geometry using only completed continuous bars.

    The PREV7D baseline is 7 × 288 L5-3 widths from seven *previous*
    complete UTC calendar days, not the current partial day or a FIXED90
    median. ATR200 uses the exact Wilder seed and prior close of the source.
    A missing bar, day, ATR warmup, or stale 5m state means NOT_READY.
    """
    if effective_at.tzinfo is None:
        raise ValueError("effective_at must be aware")
    when = effective_at.astimezone(UTC)
    if when.second or when.microsecond or when.minute % 5:
        raise ValueError("P90L90 state requires exact five-minute boundary")
    fives = _closed_bars(candles_5m, timeframe="5", effective_at=when)
    fifteens = _closed_bars(candles_15m, timeframe="15", effective_at=when)
    if fives is None or fifteens is None:
        return None
    last_fifteen_boundary = when.replace(
        minute=(when.minute // 15) * 15,
    )
    if (
        fives[-1].closed_at != when
        or fifteens[-1].closed_at != last_fifteen_boundary
        or len(fives) < 200
        or len(fifteens) < 200
    ):
        return None
    # Wilder warmup of the 15m geometry must be independently complete.
    if wilder_atr(fifteens, 200)[-1] is None:
        return None
    atrs = wilder_atr(fives, 200)
    if atrs[-1] is None or atrs[-1] <= 0:
        return None

    current_day = when.date()
    expected_days = {current_day - timedelta(days=k) for k in range(1, 8)}
    widths: dict[date, list[Decimal]] = {day: [] for day in expected_days}
    for idx, bar in enumerate(fives):
        day = bar.closed_at.astimezone(UTC).date()
        if day not in widths:
            continue
        # The research reference indexes by state_effective_ts, which is
        # the *closed bar end timestamp* in UTC (including midnight).
        atr = atrs[idx]
        if idx < 35 or atr is None or atr <= 0:
            return None
        group = fives[idx - 35 : idx + 1]
        raw_low = min(item.low for item in group)
        raw_high = max(item.high for item in group)
        lower = raw_low + atr * D05
        upper = raw_high - atr * D05
        if lower <= 0:
            return None
        widths[day].append((upper - lower) / lower * D100)
    if any(len(widths[day]) != 288 for day in expected_days):
        return None
    median_width = median(value for day in expected_days for value in widths[day])

    last_36 = fives[-36:]
    last_108 = fives[-108:]
    last_36_15 = fifteens[-36:]
    lo53, hi53 = min(x.low for x in last_36), max(x.high for x in last_36)
    lo5, hi5 = min(x.low for x in last_108), max(x.high for x in last_108)
    lo15, hi15 = min(x.low for x in last_36_15), max(x.high for x in last_36_15)
    span = max(hi5, hi15) - min(lo5, lo15)
    lower = lo53 + atrs[-1] * D05
    upper = hi53 - atrs[-1] * D05
    if span <= 0 or lower <= 0:
        return None
    current_width = (upper - lower) / lower * D100
    width_ok = abs(current_width - median_width) <= D05
    long_depth = (lo53 - lo5) * D100 / span
    short_depth = (hi5 - hi53) * D100 / span
    return P90L90EntryState(
        effective_at=when,
        l53_low_raw=lo53,
        l53_high_raw=hi53,
        h9_5m_low_raw=lo5,
        h9_5m_high_raw=hi5,
        h9_15m_low_raw=lo15,
        h9_15m_high_raw=hi15,
        raw_h9_union_span=span,
        working_width_pct=current_width,
        previous_seven_day_median=median_width,
        long_depth_pct=long_depth if (
            width_ok and lo5 == lo15 and D0 <= long_depth <= Decimal("15")
        ) else None,
        short_depth_pct=short_depth if (
            width_ok and hi5 == hi15 and D0 <= short_depth <= Decimal("15")
        ) else None,
    )


@dataclass(frozen=True, slots=True)
class P90L90Crossing:
    side: Side
    event_at: datetime
    effective_at: datetime
    current_price: Decimal
    previous_price: Decimal
    boundary: Decimal
    depth_pct: Decimal
    modeled_exchange_fill: bool = False


@dataclass(slots=True)
class P90L90CausalEntry:
    """Observe native ordered ticks and emit first RAW crossing per 5m/side."""

    state: P90L90EntryState | None = None
    previous_price: Decimal | None = None
    previous_at: datetime | None = None
    seen: set[Side] = field(default_factory=set)

    def update_geometry(self, state: P90L90EntryState) -> None:
        if self.state is not None and state.effective_at <= self.state.effective_at:
            raise ValueError("non-monotone P90L90 geometry")
        self.state = state
        self.seen.clear()

    def on_trade(self, *, price: Decimal, event_at: datetime) -> tuple[P90L90Crossing, ...]:
        if price <= 0 or not price.is_finite() or event_at.tzinfo is None:
            raise ValueError("invalid ordered native trade")
        when = event_at.astimezone(UTC)
        if self.previous_at is not None and when < self.previous_at:
            raise ValueError("non-monotone native trade time")
        previous = self.previous_price
        self.previous_price, self.previous_at = price, when
        state = self.state
        if previous is None or state is None:
            return ()
        if not state.effective_at <= when < state.effective_at + FIVE_MINUTES:
            return ()  # no stale geometry or future trade
        out = []
        for side in ("LONG", "SHORT"):
            if side in self.seen:
                continue
            edge = state.entry_edge(side)
            if edge is None:
                continue
            crossed = (previous > edge >= price) if side == "LONG" else (
                previous < edge <= price
            )
            if crossed:
                self.seen.add(side)
                depth = state.long_depth_pct if side == "LONG" else state.short_depth_pct
                assert depth is not None
                out.append(P90L90Crossing(
                    side=side, event_at=when, effective_at=state.effective_at,
                    current_price=price, previous_price=previous,
                    boundary=edge, depth_pct=depth,
                ))
        return tuple(out)


@dataclass(frozen=True, slots=True)
class P90L90PaperClose:
    reason: ExitReason
    observed_price: Decimal
    modeled_price: Decimal
    resting_maker_proxy: bool
    modeled_exchange_fill: bool = False


@dataclass(slots=True)
class P90L90CausalExit:
    """Native-trade-order paper Exit; not a Bybit stop/TP order implementation."""

    side: Side
    entry_price: Decimal
    entry_sequence: int
    state_bucket: int
    stop: Decimal = field(init=False)
    best_favorable_pct: Decimal = D0
    protected: bool = False
    target: Decimal | None = None
    target_live_after_sequence: int = -1
    last_sequence: int = field(init=False)
    closed: bool = False

    def __post_init__(self) -> None:
        if self.side not in {"LONG", "SHORT"} or self.entry_price <= 0:
            raise ValueError("invalid paper Entry")
        self.stop = self._threshold(Decimal("-10"))
        self.last_sequence = self.entry_sequence

    def snapshot(self) -> dict[str, object]:
        """Exact JSON-safe persisted paper state, independent of process memory."""
        return {
            "model": "P90L90_TRAIL035_RAWTP_V1",
            "side": self.side,
            "entry_price": str(self.entry_price),
            "entry_sequence": self.entry_sequence,
            "state_bucket": self.state_bucket,
            "stop": str(self.stop),
            "best_favorable_pct": str(self.best_favorable_pct),
            "protected": self.protected,
            "target": None if self.target is None else str(self.target),
            "target_live_after_sequence": self.target_live_after_sequence,
            "last_sequence": self.last_sequence,
            "closed": self.closed,
        }

    @classmethod
    def restore(cls, raw: Mapping[str, object]) -> P90L90CausalExit:
        """Fail closed on incompatible, incomplete or inconsistent restart data."""
        if set(raw) != {
            "model", "side", "entry_price", "entry_sequence",
            "state_bucket", "stop", "best_favorable_pct", "protected",
            "target", "target_live_after_sequence", "last_sequence", "closed",
        } or raw["model"] != "P90L90_TRAIL035_RAWTP_V1":
            raise ValueError("P90L90 restart snapshot schema/version mismatch")
        side = raw["side"]
        if side not in ("LONG", "SHORT"):
            raise ValueError("P90L90 snapshot direction invalid")
        if any(
            type(raw[key]) is not int
            for key in (
                "entry_sequence", "state_bucket",
                "target_live_after_sequence", "last_sequence",
            )
        ) or any(type(raw[key]) is not bool for key in ("protected", "closed")):
            raise ValueError("P90L90 snapshot type mismatch")
        entry = Decimal(str(raw["entry_price"]))
        stop = Decimal(str(raw["stop"]))
        best = Decimal(str(raw["best_favorable_pct"]))
        target = None if raw["target"] is None else Decimal(str(raw["target"]))
        if not (
            all(v.is_finite() for v in (entry, stop, best))
            and entry > 0 and stop > 0 and best >= 0
            and (target is None or (target.is_finite() and target > 0))
        ):
            raise ValueError("P90L90 snapshot price/return invalid")
        state = cls(
            side=side,  # type: ignore[arg-type]
            entry_price=entry,
            entry_sequence=raw["entry_sequence"],
            state_bucket=raw["state_bucket"],
        )
        if raw["last_sequence"] < state.entry_sequence:
            raise ValueError("P90L90 snapshot sequence regressed")
        if raw["target_live_after_sequence"] > raw["last_sequence"]:
            raise ValueError("P90L90 snapshot future TP activation")
        initial_stop = state.stop
        if side == "LONG" and stop < initial_stop or (
            side == "SHORT" and stop > initial_stop
        ):
            raise ValueError("P90L90 snapshot stop loosened")
        if raw["protected"] != (
            stop > initial_stop if side == "LONG" else stop < initial_stop
        ):
            raise ValueError("P90L90 snapshot protection invariant")
        if raw["protected"] and best < D05:
            raise ValueError("P90L90 snapshot early trailing protection")
        state.stop = stop
        state.best_favorable_pct = best
        state.protected = raw["protected"]
        state.target = target
        state.target_live_after_sequence = raw["target_live_after_sequence"]
        state.last_sequence = raw["last_sequence"]
        state.closed = raw["closed"]
        return state

    def _threshold(self, pct: Decimal) -> Decimal:
        return self.entry_price * (1 + pct / D100) if self.side == "LONG" else (
            self.entry_price * (1 - pct / D100)
        )

    def _favorable(self, px: Decimal) -> Decimal:
        return ((px / self.entry_price - 1) if self.side == "LONG" else (
            1 - px / self.entry_price
        )) * D100

    def _hit_stop(self, px: Decimal) -> bool:
        return px <= self.stop if self.side == "LONG" else px >= self.stop

    def _hit_target(self, px: Decimal, target: Decimal) -> bool:
        return px >= target if self.side == "LONG" else px <= target

    def on_trade(
        self, *, price: Decimal, sequence: int, bucket: int,
        candidate_opposite_raw_tp: Decimal | None,
    ) -> P90L90PaperClose | None:
        if self.closed:
            raise ValueError("cannot evaluate closed paper position")
        if price <= 0 or not price.is_finite() or sequence <= self.last_sequence:
            raise ValueError("invalid native trade order")
        if bucket < self.state_bucket:
            raise ValueError("non-monotone five-minute state")
        self.last_sequence = sequence
        if self._hit_stop(price):
            self.closed = True
            return P90L90PaperClose(
                "PROTECTED_STOP" if self.protected else "HARD_STOP",
                price, price, False,
            )
        if (self.target is not None and sequence > self.target_live_after_sequence
                and self._hit_target(price, self.target)):
            self.closed = True
            return P90L90PaperClose("RESTING_TP", price, self.target, True)
        if bucket != self.state_bucket:
            self.state_bucket = bucket
            if (candidate_opposite_raw_tp is not None
                    and self._favorable(candidate_opposite_raw_tp) >= D05):
                if self._hit_target(price, candidate_opposite_raw_tp):
                    self.closed = True
                    return P90L90PaperClose("MARKETABLE_TP", price, price, False)
                self.target = candidate_opposite_raw_tp
                self.target_live_after_sequence = sequence
        favorable = self._favorable(price)
        if favorable > self.best_favorable_pct:
            self.best_favorable_pct = favorable
        if self.best_favorable_pct >= D05:
            proposed = self._threshold(self.best_favorable_pct - D035)
            tighter = proposed > self.stop if self.side == "LONG" else proposed < self.stop
            if tighter:
                self.stop = proposed
                self.protected = True
        return None


@dataclass(frozen=True, slots=True)
class P90L90ModeledPnl:
    gross_usdt: Decimal
    entry_fee_usdt: Decimal
    exit_fee_usdt: Decimal
    net_usdt: Decimal
    modeled_exchange_fill: bool = False


def modeled_pnl_after_fees(
    *,
    side: Side,
    entry_price: Decimal,
    exit_price: Decimal,
    entry_notional_usdt: Decimal,
    maker_exit: bool,
) -> P90L90ModeledPnl:
    """Historical research fee proxy, never evidence of actual exchange fills."""
    if side not in {"LONG", "SHORT"} or min(
        entry_price, exit_price, entry_notional_usdt
    ) <= 0:
        raise ValueError("invalid modeled P90L90 trade economics")
    quantum = Decimal("0.000000000001")
    taker_rate = Decimal("0.00055")
    maker_rate = Decimal("0.0002")
    ratio = exit_price / entry_price
    gross = entry_notional_usdt * (ratio - 1)
    if side == "SHORT":
        gross = -gross
    gross = gross.quantize(quantum, rounding=ROUND_HALF_EVEN)
    entry_fee = (entry_notional_usdt * taker_rate).quantize(
        quantum, rounding=ROUND_HALF_EVEN
    )
    exit_fee = (
        entry_notional_usdt * ratio * (maker_rate if maker_exit else taker_rate)
    ).quantize(quantum, rounding=ROUND_HALF_EVEN)
    return P90L90ModeledPnl(
        gross_usdt=gross,
        entry_fee_usdt=entry_fee,
        exit_fee_usdt=exit_fee,
        net_usdt=gross - entry_fee - exit_fee,
    )


@dataclass(slots=True)
class P90L90Second48PaperEmbargo:
    """Research-equivalent entry-only hardstop pause for one EXPLICIT domain.

    The caller MUST choose domain identity; this class never silently
    merges MAIN/SUB or splits the historical five-symbol portfolio.
    No real arm, no liquidation, no close of existing positions.
    """

    domain_id: str
    hardstop_events: deque[datetime] = field(default_factory=deque)
    pause_until: datetime | None = None
    last_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.domain_id.strip():
            raise ValueError("P90L90 SECOND48 requires an explicit portfolio domain")

    def can_enter(self, at: datetime) -> bool:
        if at.tzinfo is None:
            raise ValueError("SECOND48 event requires aware UTC time")
        return self.pause_until is None or at.astimezone(UTC) >= self.pause_until

    def record_exit(self, *, reason: ExitReason, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("SECOND48 event requires aware UTC time")
        when = at.astimezone(UTC)
        if self.last_at is not None and when < self.last_at:
            raise ValueError("SECOND48 cannot process noncausal exit order")
        self.last_at = when
        if reason != "HARD_STOP":
            return
        self.hardstop_events.append(when)
        while self.hardstop_events and (
            when - self.hardstop_events[0] > timedelta(hours=48)
        ):
            self.hardstop_events.popleft()
        if len(self.hardstop_events) >= 2:
            until = when + timedelta(hours=48)
            if self.pause_until is None or until > self.pause_until:
                self.pause_until = until
