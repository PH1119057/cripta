from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from bybit_workbench.domain import (
    CancelEntryIntent,
    Candle,
    EnterIntent,
    Execution,
    NoOpIntent,
    UpdateProtectionIntent,
)
from bybit_workbench.domain.types import OrderType, PositionSide

from .base import (
    DataRequirements,
    IntentOutcome,
    IntentOutcomeStatus,
    ReadOnlyStrategyContext,
    StrategyMetadata,
    TradeIntent,
)
from .indicators import causal_channel, latest_wilder_atr, normalize_stop
from .state import (
    candles_from_state,
    candles_to_state,
    require_parameters_fingerprint,
    strategy_parameters_fingerprint,
)


class PowerState(StrEnum):
    WARMUP = "WARMUP"
    FLAT = "FLAT"
    TOUCH_CANDIDATE = "TOUCH_CANDIDATE"
    ENTRY_PENDING = "ENTRY_PENDING"
    PENDING_UNKNOWN = "PENDING_UNKNOWN"
    LONG = "LONG"
    SHORT = "SHORT"
    COOLDOWN = "COOLDOWN"


@dataclass(frozen=True, slots=True)
class PowerChannelSnapshot:
    touch_bar_closed_at: datetime
    range_high: Decimal
    range_low: Decimal
    atr: Decimal
    resistance_top: Decimal
    resistance_bottom: Decimal
    support_top: Decimal
    support_bottom: Decimal
    midline: Decimal
    direction: PositionSide
    bullish_share: Decimal | None
    bearish_share: Decimal | None


class PowerChannelRejection:
    STATE_VERSION = "power-channel-state-v2"
    _EXECUTION_HISTORY_LIMIT = 512

    def __init__(self) -> None:
        self._state = PowerState.WARMUP
        self._candles: list[Candle] = []
        self._last_closed_at: datetime | None = None
        self._snapshot: PowerChannelSnapshot | None = None
        self._pending_age = 0
        self._cancel_requested = False
        self._cooldown_remaining = 0
        self._initial_risk: Decimal | None = None
        self._parameters_fingerprint: str | None = None
        self._reconciliation_required = False
        self._seen_execution_ids: list[str] = []
        self._seen_execution_id_set: set[str] = set()
        self._last_execution_at: datetime | None = None

    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata("user_algorithm_2", "0.2.0", "Power Channel Rejection")

    def required_data(self) -> DataRequirements:
        return DataRequirements(("60", "240"), 23)

    def default_parameters(self) -> Mapping[str, object]:
        return {
            "range_lookback": 130,
            "atr_period": 200,
            "zone_half_width_atr": Decimal("0.5"),
            "min_center_range_atr": Decimal("3.0"),
            "confirmation_bars": 1,
            "entry_valid_bars": 2,
            "stop_buffer_atr": Decimal("0.1"),
            "minimum_reward_risk": Decimal("1.0"),
            "trailing_activation_r": Decimal("1.0"),
            "cooldown_bars": 3,
            "requested_leverage": Decimal("1"),
            "direction_mode": "both",
            "take_profit_mode": "midline",
            "use_candle_power_filter": False,
            "minimum_power_share": Decimal("0.55"),
        }

    def warmup_bars(self, parameters: Mapping[str, object]) -> int:
        return max(
            int(str(parameters["range_lookback"])) + 2,
            int(str(parameters["atr_period"])) + 3,
        )

    async def on_start(self, context: ReadOnlyStrategyContext) -> None:
        self._bind_parameters(context.parameters)
        self._synchronize(context)

    async def on_bar_closed(
        self,
        context: ReadOnlyStrategyContext,
        bar: Candle,
    ) -> Sequence[TradeIntent]:
        self._bind_parameters(context.parameters)
        if self._reconciliation_required:
            raise PermissionError("strategy reconciliation is required before the next candle")
        if bar.symbol != context.symbol:
            raise ValueError("strategy candle symbol differs from context symbol")
        if not bar.is_closed:
            return (self._noop(context.symbol, bar, "open candle ignored"),)
        if self._last_closed_at is not None:
            if bar.closed_at == self._last_closed_at:
                return ()
            if bar.closed_at < self._last_closed_at:
                raise ValueError("out-of-order closed candle")
        self._last_closed_at = bar.closed_at
        self._candles.append(bar)
        warmup = self.warmup_bars(context.parameters)
        if len(self._candles) < warmup:
            self._state = PowerState.WARMUP
            return (self._noop(context.symbol, bar, f"warmup {len(self._candles)}/{warmup}"),)
        self._synchronize(context)
        if not context.health.healthy:
            return (self._noop(context.symbol, bar, "engine health blocks strategy intents"),)
        if self._state is PowerState.COOLDOWN:
            if self._cooldown_remaining > 0:
                self._cooldown_remaining -= 1
                return (self._noop(context.symbol, bar, "cooldown"),)
            self._state = PowerState.FLAT
        if self._state is PowerState.PENDING_UNKNOWN:
            raise PermissionError("unknown entry state must be reconciled before candle processing")
        if self._state is PowerState.TOUCH_CANDIDATE:
            return self._confirm_touch(context, bar)
        if self._state is PowerState.ENTRY_PENDING:
            return self._manage_pending(context, bar)
        if self._state in {PowerState.LONG, PowerState.SHORT}:
            return self._manage_position(context, bar)
        if not context.health.new_entries_allowed:
            return (self._noop(context.symbol, bar, "new entries are disabled"),)
        return self._detect_touch(context, bar)

    async def on_execution(
        self,
        context: ReadOnlyStrategyContext,
        execution: Execution,
    ) -> Sequence[TradeIntent]:
        self._bind_parameters(context.parameters)
        if execution.symbol != context.symbol:
            raise ValueError("strategy execution symbol differs from context symbol")
        if not self._accept_execution(execution):
            return ()
        if context.position.side is PositionSide.FLAT:
            if self._state in {PowerState.LONG, PowerState.SHORT}:
                self._enter_cooldown(context.parameters)
            return ()
        self._state = (
            PowerState.LONG if context.position.side is PositionSide.LONG else PowerState.SHORT
        )
        if self._snapshot is not None:
            entry = (
                self._snapshot.support_top
                if self._snapshot.direction is PositionSide.LONG
                else self._snapshot.resistance_bottom
            )
            stop = self._initial_stop(context.parameters)
            self._initial_risk = abs(entry - stop)
        if (
            context.pending_entry is not None
            and context.pending_entry.remaining_quantity > 0
            and not self._cancel_requested
        ):
            self._cancel_requested = True
            return (
                CancelEntryIntent(
                    self._intent_id(context.symbol, execution.executed_at, "partial-cancel"),
                    context.symbol,
                    "partial fill confirmed; cancel unfilled entry remainder",
                ),
            )
        return ()

    async def on_intent_outcome(
        self,
        context: ReadOnlyStrategyContext,
        outcome: IntentOutcome,
    ) -> Sequence[TradeIntent]:
        self._bind_parameters(context.parameters)
        if outcome.status is IntentOutcomeStatus.UNKNOWN:
            self._reconciliation_required = True
            if self._state in {PowerState.ENTRY_PENDING, PowerState.PENDING_UNKNOWN}:
                self._state = PowerState.PENDING_UNKNOWN
            return ()
        if outcome.status is IntentOutcomeStatus.REJECTED:
            if (
                self._state in {PowerState.ENTRY_PENDING, PowerState.PENDING_UNKNOWN}
                and context.position.side is PositionSide.FLAT
            ):
                self._reset_flat()
        elif outcome.status is IntentOutcomeStatus.CANCELLED:
            if context.position.side is PositionSide.FLAT:
                self._reset_flat()
        elif outcome.status is IntentOutcomeStatus.FILLED:
            self._synchronize(context)
        return ()

    async def on_reconcile(self, context: ReadOnlyStrategyContext) -> None:
        self._bind_parameters(context.parameters)
        was_unknown = self._reconciliation_required or self._state is PowerState.PENDING_UNKNOWN
        self._reconciliation_required = False
        if context.position.side is PositionSide.LONG:
            self._state = PowerState.LONG
        elif context.position.side is PositionSide.SHORT:
            self._state = PowerState.SHORT
        elif context.pending_entry is not None:
            self._state = PowerState.ENTRY_PENDING
            self._pending_age = max(self._pending_age, context.pending_entry.age_bars)
        elif was_unknown:
            self._reset_flat()
        else:
            self._synchronize(context)

    async def on_stop(self, reason: str) -> None:
        return None

    def snapshot_state(self) -> Mapping[str, Any]:
        frozen = None
        if self._snapshot is not None:
            frozen = {
                "touch_bar_closed_at": self._snapshot.touch_bar_closed_at.isoformat(),
                **{
                    name: str(getattr(self._snapshot, name))
                    for name in (
                        "range_high",
                        "range_low",
                        "atr",
                        "resistance_top",
                        "resistance_bottom",
                        "support_top",
                        "support_bottom",
                        "midline",
                    )
                },
                "direction": self._snapshot.direction.value,
                "bullish_share": (
                    None
                    if self._snapshot.bullish_share is None
                    else str(self._snapshot.bullish_share)
                ),
                "bearish_share": (
                    None
                    if self._snapshot.bearish_share is None
                    else str(self._snapshot.bearish_share)
                ),
            }
        return {
            "state_version": self.STATE_VERSION,
            "parameters_fingerprint": self._parameters_fingerprint,
            "reconciliation_required": self._reconciliation_required,
            "candles": candles_to_state(self._candles),
            "state": self._state.value,
            "last_closed_at": (
                None if self._last_closed_at is None else self._last_closed_at.isoformat()
            ),
            "snapshot": frozen,
            "pending_age": self._pending_age,
            "cancel_requested": self._cancel_requested,
            "cooldown_remaining": self._cooldown_remaining,
            "initial_risk": None if self._initial_risk is None else str(self._initial_risk),
            "seen_execution_ids": list(self._seen_execution_ids),
            "last_execution_at": (
                None if self._last_execution_at is None else self._last_execution_at.isoformat()
            ),
        }

    def restore_state(self, snapshot: Mapping[str, Any]) -> None:
        if snapshot.get("state_version") != self.STATE_VERSION:
            raise ValueError("unsupported Power Channel strategy state version")
        self._parameters_fingerprint = require_parameters_fingerprint(
            snapshot.get("parameters_fingerprint")
        )
        self._reconciliation_required = bool(snapshot.get("reconciliation_required", False))
        self._state = PowerState(str(snapshot["state"]))
        if self._state is PowerState.PENDING_UNKNOWN:
            self._reconciliation_required = True
        self._candles = candles_from_state(snapshot.get("candles"))
        last = snapshot.get("last_closed_at")
        self._last_closed_at = None if last is None else datetime.fromisoformat(str(last))
        raw = snapshot.get("snapshot")
        if raw is None:
            self._snapshot = None
        elif isinstance(raw, Mapping):
            self._snapshot = PowerChannelSnapshot(
                touch_bar_closed_at=datetime.fromisoformat(str(raw["touch_bar_closed_at"])),
                range_high=Decimal(str(raw["range_high"])),
                range_low=Decimal(str(raw["range_low"])),
                atr=Decimal(str(raw["atr"])),
                resistance_top=Decimal(str(raw["resistance_top"])),
                resistance_bottom=Decimal(str(raw["resistance_bottom"])),
                support_top=Decimal(str(raw["support_top"])),
                support_bottom=Decimal(str(raw["support_bottom"])),
                midline=Decimal(str(raw["midline"])),
                direction=PositionSide(str(raw["direction"])),
                bullish_share=(
                    None if raw.get("bullish_share") is None else Decimal(str(raw["bullish_share"]))
                ),
                bearish_share=(
                    None if raw.get("bearish_share") is None else Decimal(str(raw["bearish_share"]))
                ),
            )
        else:
            raise ValueError("invalid Power Channel frozen snapshot")
        self._pending_age = int(snapshot.get("pending_age", 0))
        self._cancel_requested = bool(snapshot.get("cancel_requested", False))
        self._cooldown_remaining = int(snapshot.get("cooldown_remaining", 0))
        risk = snapshot.get("initial_risk")
        self._initial_risk = None if risk is None else Decimal(str(risk))
        self._restore_execution_cursor(snapshot)

    def _channel_snapshot(
        self,
        parameters: Mapping[str, object],
        bar: Candle,
    ) -> PowerChannelSnapshot | None:
        previous = self._candles[:-1]
        lookback = int(str(parameters["range_lookback"]))
        atr = latest_wilder_atr(previous, int(str(parameters["atr_period"])))
        if atr is None or atr <= 0:
            return None
        range_high, range_low = causal_channel(previous, lookback)
        width = Decimal(str(parameters["zone_half_width_atr"])) * atr
        resistance_top = range_high + width
        resistance_bottom = range_high - width
        support_top = range_low + width
        support_bottom = range_low - width
        if support_top >= resistance_bottom:
            return None
        if (range_high - range_low) / atr < Decimal(str(parameters["min_center_range_atr"])):
            return None
        window = previous[-lookback:]
        bull = sum(item.close > item.open for item in window)
        bear = sum(item.close < item.open for item in window)
        directional = bull + bear
        bull_share = None if directional == 0 else Decimal(bull) / Decimal(directional)
        bear_share = None if directional == 0 else Decimal(bear) / Decimal(directional)
        long_touch = bar.low <= support_top and bar.close >= support_bottom
        short_touch = bar.high >= resistance_bottom and bar.close <= resistance_top
        # An ambiguous candle touching both zones is discarded instead of choosing a side.
        if long_touch == short_touch:
            return None
        direction = PositionSide.LONG if long_touch else PositionSide.SHORT
        mode = str(parameters["direction_mode"])
        if (direction is PositionSide.LONG and mode not in {"long", "both"}) or (
            direction is PositionSide.SHORT and mode not in {"short", "both"}
        ):
            return None
        if bool(parameters["use_candle_power_filter"]):
            required = Decimal(str(parameters["minimum_power_share"]))
            actual = bull_share if direction is PositionSide.LONG else bear_share
            if actual is None or actual < required:
                return None
        return PowerChannelSnapshot(
            bar.closed_at,
            range_high,
            range_low,
            atr,
            resistance_top,
            resistance_bottom,
            support_top,
            support_bottom,
            (range_high + range_low) / Decimal("2"),
            direction,
            bull_share,
            bear_share,
        )

    def _detect_touch(self, context: ReadOnlyStrategyContext, bar: Candle) -> Sequence[TradeIntent]:
        snapshot = self._channel_snapshot(context.parameters, bar)
        if snapshot is None:
            return ()
        self._snapshot = snapshot
        self._state = PowerState.TOUCH_CANDIDATE
        return (
            self._noop(context.symbol, bar, f"frozen {snapshot.direction.value} touch candidate"),
        )

    def _confirm_touch(
        self, context: ReadOnlyStrategyContext, bar: Candle
    ) -> Sequence[TradeIntent]:
        frozen = self._snapshot
        if frozen is None:
            self._reset_flat()
            return ()
        confirmed = (
            bar.low > frozen.support_top
            if frozen.direction is PositionSide.LONG
            else bar.high < frozen.resistance_bottom
        )
        if not confirmed:
            self._reset_flat()
            return (self._noop(context.symbol, bar, "touch candidate was not confirmed"),)
        entry = (
            frozen.support_top
            if frozen.direction is PositionSide.LONG
            else frozen.resistance_bottom
        )
        stop = self._initial_stop(context.parameters)
        take_profit = (
            frozen.midline if context.parameters["take_profit_mode"] == "midline" else None
        )
        risk = abs(entry - stop)
        if risk <= 0:
            self._reset_flat()
            return (self._noop(context.symbol, bar, "initial risk is zero"),)
        if take_profit is not None:
            valid_side = (frozen.direction is PositionSide.LONG and take_profit > entry) or (
                frozen.direction is PositionSide.SHORT and take_profit < entry
            )
            reward = abs(take_profit - entry)
            if not valid_side or reward / risk < Decimal(
                str(context.parameters["minimum_reward_risk"])
            ):
                self._reset_flat()
                return (
                    self._noop(context.symbol, bar, "frozen midline reward/risk is insufficient"),
                )
        self._state = PowerState.ENTRY_PENDING
        self._pending_age = 0
        self._cancel_requested = False
        self._initial_risk = risk
        return (
            EnterIntent(
                self._intent_id(context.symbol, bar.closed_at, "entry"),
                context.symbol,
                frozen.direction,
                OrderType.LIMIT,
                entry,
                stop,
                Decimal(str(context.parameters["requested_leverage"])),
                "confirmed frozen power-channel rejection; limit retest",
                take_profit,
            ),
        )

    def _initial_stop(self, parameters: Mapping[str, object]) -> Decimal:
        if self._snapshot is None:
            raise RuntimeError("power-channel snapshot is missing")
        buffer = Decimal(str(parameters["stop_buffer_atr"])) * self._snapshot.atr
        if self._snapshot.direction is PositionSide.LONG:
            return self._snapshot.support_bottom - buffer
        return self._snapshot.resistance_top + buffer

    def _manage_pending(
        self, context: ReadOnlyStrategyContext, bar: Candle
    ) -> Sequence[TradeIntent]:
        if context.position.side is not PositionSide.FLAT:
            self._synchronize(context)
            return ()
        frozen = self._snapshot
        if frozen is None or self._cancel_requested:
            return ()
        self._pending_age += 1
        invalidated = (
            frozen.direction is PositionSide.LONG and bar.close < frozen.support_bottom
        ) or (frozen.direction is PositionSide.SHORT and bar.close > frozen.resistance_top)
        expired = self._pending_age >= int(str(context.parameters["entry_valid_bars"]))
        if invalidated or expired:
            self._cancel_requested = True
            reason = (
                "frozen power-channel entry invalidated"
                if invalidated
                else "limit retest entry expired"
            )
            return (
                CancelEntryIntent(
                    self._intent_id(context.symbol, bar.closed_at, "cancel"),
                    context.symbol,
                    reason,
                ),
            )
        return ()

    def _manage_position(
        self, context: ReadOnlyStrategyContext, bar: Candle
    ) -> Sequence[TradeIntent]:
        side = context.position.side
        if side is PositionSide.FLAT:
            self._enter_cooldown(context.parameters)
            return ()
        current = context.protection.confirmed_stop
        entry = context.position.average_price
        if current is None or entry is None or self._initial_risk is None:
            return ()
        mark = context.mark_price or context.latest_price
        favorable = mark - entry if side is PositionSide.LONG else entry - mark
        if (
            favorable
            < Decimal(str(context.parameters["trailing_activation_r"])) * self._initial_risk
        ):
            return ()
        previous = self._candles[:-1]
        atr = latest_wilder_atr(previous, int(str(context.parameters["atr_period"])))
        if atr is None or atr <= 0:
            return ()
        range_high, range_low = causal_channel(
            previous, int(str(context.parameters["range_lookback"]))
        )
        width = Decimal(str(context.parameters["zone_half_width_atr"])) * atr
        buffer = Decimal(str(context.parameters["stop_buffer_atr"])) * atr
        candidate = (
            range_low - width - buffer if side is PositionSide.LONG else range_high + width + buffer
        )
        proposed = max(current, candidate) if side is PositionSide.LONG else min(current, candidate)
        proposed = normalize_stop(proposed, side, context.tick_size)
        protective_side = proposed < mark if side is PositionSide.LONG else proposed > mark
        improves = proposed > current if side is PositionSide.LONG else proposed < current
        if not protective_side or not improves:
            return ()
        return (
            UpdateProtectionIntent(
                self._intent_id(context.symbol, bar.closed_at, "trail"),
                context.symbol,
                "monotonic causal structural trailing stop",
                stop_price=proposed,
            ),
        )

    def _synchronize(self, context: ReadOnlyStrategyContext) -> None:
        if context.position.side is PositionSide.LONG:
            self._state = PowerState.LONG
        elif context.position.side is PositionSide.SHORT:
            self._state = PowerState.SHORT
        elif context.pending_entry is not None:
            self._state = PowerState.ENTRY_PENDING
            self._pending_age = max(self._pending_age, context.pending_entry.age_bars)
        elif self._state in {PowerState.LONG, PowerState.SHORT}:
            self._enter_cooldown(context.parameters)
        elif self._state not in {
            PowerState.COOLDOWN,
            PowerState.TOUCH_CANDIDATE,
            PowerState.ENTRY_PENDING,
            PowerState.PENDING_UNKNOWN,
        }:
            self._state = PowerState.FLAT

    def _bind_parameters(self, parameters: Mapping[str, object]) -> None:
        fingerprint = strategy_parameters_fingerprint(parameters)
        if self._parameters_fingerprint is None:
            self._parameters_fingerprint = fingerprint
        elif self._parameters_fingerprint != fingerprint:
            raise ValueError("strategy parameters differ from the persisted state fingerprint")

    def _accept_execution(self, execution: Execution) -> bool:
        if execution.execution_id in self._seen_execution_id_set:
            return False
        if self._last_execution_at is not None and execution.executed_at < self._last_execution_at:
            raise ValueError("out-of-order strategy execution")
        self._last_execution_at = execution.executed_at
        self._seen_execution_ids.append(execution.execution_id)
        self._seen_execution_id_set.add(execution.execution_id)
        if len(self._seen_execution_ids) > self._EXECUTION_HISTORY_LIMIT:
            expired = self._seen_execution_ids.pop(0)
            self._seen_execution_id_set.remove(expired)
        return True

    def _restore_execution_cursor(self, snapshot: Mapping[str, Any]) -> None:
        raw_ids = snapshot.get("seen_execution_ids", [])
        if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (str, bytes)):
            raise ValueError("strategy execution id state must be a sequence")
        selected = [str(item) for item in raw_ids]
        if len(selected) != len(set(selected)):
            raise ValueError("strategy execution id state contains duplicates")
        selected = selected[-self._EXECUTION_HISTORY_LIMIT :]
        self._seen_execution_ids = selected
        self._seen_execution_id_set = set(selected)
        raw_time = snapshot.get("last_execution_at")
        self._last_execution_at = (
            None if raw_time is None else datetime.fromisoformat(str(raw_time))
        )

    def _enter_cooldown(self, parameters: Mapping[str, object]) -> None:
        self._state = PowerState.COOLDOWN
        self._cooldown_remaining = int(str(parameters["cooldown_bars"]))
        self._snapshot = None
        self._pending_age = 0
        self._cancel_requested = False
        self._initial_risk = None

    def _reset_flat(self) -> None:
        self._state = PowerState.FLAT
        self._snapshot = None
        self._pending_age = 0
        self._cancel_requested = False
        self._initial_risk = None

    def _noop(self, symbol: str, bar: Candle, reason: str) -> NoOpIntent:
        return NoOpIntent(self._intent_id(symbol, bar.closed_at, "noop"), symbol, reason)

    def _intent_id(self, symbol: str, observed_at: datetime, purpose: str) -> str:
        fingerprint = self._parameters_fingerprint
        if fingerprint is None:
            raise RuntimeError("strategy parameters are not bound")
        raw = (
            f"{self.metadata().strategy_id}|{self.metadata().version}|{fingerprint}|{symbol}|"
            f"{observed_at.isoformat()}|{purpose}"
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
        return f"a2-{purpose[:8]}-{digest}"[:36]
