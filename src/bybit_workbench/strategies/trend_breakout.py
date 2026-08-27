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
    ExitIntent,
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


class TrendState(StrEnum):
    WARMUP = "WARMUP"
    FLAT = "FLAT"
    ENTRY_PENDING = "ENTRY_PENDING"
    PENDING_UNKNOWN = "PENDING_UNKNOWN"
    LONG = "LONG"
    SHORT = "SHORT"
    COOLDOWN = "COOLDOWN"


@dataclass(frozen=True, slots=True)
class BreakoutSnapshot:
    signal_bar_closed_at: datetime
    upper: Decimal
    lower: Decimal
    atr: Decimal
    entry_price: Decimal
    stop_price: Decimal
    take_profit: Decimal | None
    direction: PositionSide


class TrendBreakoutRetest:
    STATE_VERSION = "trend-breakout-state-v2"
    _EXECUTION_HISTORY_LIMIT = 512

    def __init__(self) -> None:
        self._state = TrendState.WARMUP
        self._candles: list[Candle] = []
        self._last_closed_at: datetime | None = None
        self._snapshot: BreakoutSnapshot | None = None
        self._pending_age = 0
        self._cancel_requested = False
        self._cooldown_remaining = 0
        self._highest_since_entry: Decimal | None = None
        self._lowest_since_entry: Decimal | None = None
        self._parameters_fingerprint: str | None = None
        self._reconciliation_required = False
        self._seen_execution_ids: list[str] = []
        self._seen_execution_id_set: set[str] = set()
        self._last_execution_at: datetime | None = None

    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata("user_algorithm_1", "0.2.0", "Trend Breakout Retest")

    def required_data(self) -> DataRequirements:
        return DataRequirements(("60", "240"), 22)

    def default_parameters(self) -> Mapping[str, object]:
        return {
            "entry_lookback": 55,
            "atr_period": 20,
            "initial_stop_atr": Decimal("2.0"),
            "trailing_stop_atr": Decimal("3.0"),
            "entry_valid_bars": 2,
            "cooldown_bars": 1,
            "requested_leverage": Decimal("1"),
            "direction_mode": "both",
            "take_profit_r": Decimal("0"),
            "exit_on_opposite_breakout": True,
        }

    def warmup_bars(self, parameters: Mapping[str, object]) -> int:
        return max(
            int(str(parameters["entry_lookback"])) + 1,
            int(str(parameters["atr_period"])) + 2,
        )

    async def on_start(self, context: ReadOnlyStrategyContext) -> None:
        self._bind_parameters(context.parameters)
        self._synchronize_exchange_state(context)

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
        parameters = context.parameters
        warmup = self.warmup_bars(parameters)
        if len(self._candles) < warmup:
            self._state = TrendState.WARMUP
            return (self._noop(context.symbol, bar, f"warmup {len(self._candles)}/{warmup}"),)

        self._synchronize_exchange_state(context)
        if not context.health.healthy:
            return (self._noop(context.symbol, bar, "engine health blocks strategy intents"),)
        if self._state is TrendState.COOLDOWN:
            if self._cooldown_remaining > 0:
                self._cooldown_remaining -= 1
                return (self._noop(context.symbol, bar, "cooldown"),)
            self._state = TrendState.FLAT
        if self._state is TrendState.PENDING_UNKNOWN:
            raise PermissionError("unknown entry state must be reconciled before candle processing")
        if self._state is TrendState.ENTRY_PENDING:
            return self._manage_pending(context, bar)
        if self._state in {TrendState.LONG, TrendState.SHORT}:
            return self._manage_position(context, bar)
        if not context.health.new_entries_allowed:
            return (self._noop(context.symbol, bar, "new entries are disabled"),)
        return self._evaluate_entry(context, bar)

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
            if self._state in {TrendState.LONG, TrendState.SHORT}:
                self._enter_cooldown(context.parameters)
            return ()
        self._state = (
            TrendState.LONG if context.position.side is PositionSide.LONG else TrendState.SHORT
        )
        self._highest_since_entry = max(
            self._highest_since_entry or execution.price, execution.price
        )
        self._lowest_since_entry = min(self._lowest_since_entry or execution.price, execution.price)
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
            if self._state in {TrendState.ENTRY_PENDING, TrendState.PENDING_UNKNOWN}:
                self._state = TrendState.PENDING_UNKNOWN
            return ()
        if outcome.status is IntentOutcomeStatus.REJECTED:
            if (
                self._state in {TrendState.ENTRY_PENDING, TrendState.PENDING_UNKNOWN}
                and context.position.side is PositionSide.FLAT
            ):
                self._reset_flat()
        elif outcome.status is IntentOutcomeStatus.CANCELLED:
            if context.position.side is PositionSide.FLAT:
                self._reset_flat()
        elif outcome.status is IntentOutcomeStatus.FILLED:
            self._synchronize_exchange_state(context)
        return ()

    async def on_reconcile(self, context: ReadOnlyStrategyContext) -> None:
        self._bind_parameters(context.parameters)
        was_unknown = self._reconciliation_required or self._state is TrendState.PENDING_UNKNOWN
        self._reconciliation_required = False
        if context.position.side is PositionSide.LONG:
            self._state = TrendState.LONG
        elif context.position.side is PositionSide.SHORT:
            self._state = TrendState.SHORT
        elif context.pending_entry is not None:
            self._state = TrendState.ENTRY_PENDING
            self._pending_age = max(self._pending_age, context.pending_entry.age_bars)
        elif was_unknown:
            self._reset_flat()
        else:
            self._synchronize_exchange_state(context)

    async def on_stop(self, reason: str) -> None:
        return None

    def snapshot_state(self) -> Mapping[str, Any]:
        snapshot = None
        if self._snapshot is not None:
            snapshot = {
                "signal_bar_closed_at": self._snapshot.signal_bar_closed_at.isoformat(),
                "upper": str(self._snapshot.upper),
                "lower": str(self._snapshot.lower),
                "atr": str(self._snapshot.atr),
                "entry_price": str(self._snapshot.entry_price),
                "stop_price": str(self._snapshot.stop_price),
                "take_profit": (
                    None if self._snapshot.take_profit is None else str(self._snapshot.take_profit)
                ),
                "direction": self._snapshot.direction.value,
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
            "snapshot": snapshot,
            "pending_age": self._pending_age,
            "cancel_requested": self._cancel_requested,
            "cooldown_remaining": self._cooldown_remaining,
            "highest_since_entry": (
                None if self._highest_since_entry is None else str(self._highest_since_entry)
            ),
            "lowest_since_entry": (
                None if self._lowest_since_entry is None else str(self._lowest_since_entry)
            ),
            "seen_execution_ids": list(self._seen_execution_ids),
            "last_execution_at": (
                None if self._last_execution_at is None else self._last_execution_at.isoformat()
            ),
        }

    def restore_state(self, snapshot: Mapping[str, Any]) -> None:
        if snapshot.get("state_version") != self.STATE_VERSION:
            raise ValueError("unsupported Trend Breakout strategy state version")
        self._parameters_fingerprint = require_parameters_fingerprint(
            snapshot.get("parameters_fingerprint")
        )
        self._reconciliation_required = bool(snapshot.get("reconciliation_required", False))
        self._state = TrendState(str(snapshot["state"]))
        if self._state is TrendState.PENDING_UNKNOWN:
            self._reconciliation_required = True
        self._candles = candles_from_state(snapshot.get("candles"))
        last = snapshot.get("last_closed_at")
        self._last_closed_at = None if last is None else datetime.fromisoformat(str(last))
        raw = snapshot.get("snapshot")
        if raw is None:
            self._snapshot = None
        elif isinstance(raw, Mapping):
            self._snapshot = BreakoutSnapshot(
                datetime.fromisoformat(str(raw["signal_bar_closed_at"])),
                Decimal(str(raw["upper"])),
                Decimal(str(raw["lower"])),
                Decimal(str(raw["atr"])),
                Decimal(str(raw["entry_price"])),
                Decimal(str(raw["stop_price"])),
                None if raw.get("take_profit") is None else Decimal(str(raw["take_profit"])),
                PositionSide(str(raw["direction"])),
            )
        else:
            raise ValueError("invalid Trend Breakout frozen snapshot")
        self._pending_age = int(snapshot.get("pending_age", 0))
        self._cancel_requested = bool(snapshot.get("cancel_requested", False))
        self._cooldown_remaining = int(snapshot.get("cooldown_remaining", 0))
        high = snapshot.get("highest_since_entry")
        low = snapshot.get("lowest_since_entry")
        self._highest_since_entry = None if high is None else Decimal(str(high))
        self._lowest_since_entry = None if low is None else Decimal(str(low))
        self._restore_execution_cursor(snapshot)

    def _evaluate_entry(
        self,
        context: ReadOnlyStrategyContext,
        bar: Candle,
    ) -> Sequence[TradeIntent]:
        lookback = int(str(context.parameters["entry_lookback"]))
        atr_period = int(str(context.parameters["atr_period"]))
        previous = self._candles[:-1]
        upper, lower = causal_channel(previous, lookback)
        atr = latest_wilder_atr(self._candles, atr_period)
        if atr is None or atr <= 0:
            return (self._noop(context.symbol, bar, "ATR is unavailable or zero"),)
        direction_mode = str(context.parameters["direction_mode"])
        direction: PositionSide | None = None
        entry = Decimal("0")
        if bar.close > upper and direction_mode in {"long", "both"}:
            direction, entry = PositionSide.LONG, upper
        elif bar.close < lower and direction_mode in {"short", "both"}:
            direction, entry = PositionSide.SHORT, lower
        if direction is None:
            return ()
        distance = Decimal(str(context.parameters["initial_stop_atr"])) * atr
        stop = entry - distance if direction is PositionSide.LONG else entry + distance
        take_profit_r = Decimal(str(context.parameters["take_profit_r"]))
        take_profit = None
        if take_profit_r > 0:
            take_profit = (
                entry + take_profit_r * distance
                if direction is PositionSide.LONG
                else entry - take_profit_r * distance
            )
        self._snapshot = BreakoutSnapshot(
            bar.closed_at, upper, lower, atr, entry, stop, take_profit, direction
        )
        self._state = TrendState.ENTRY_PENDING
        self._pending_age = 0
        self._cancel_requested = False
        return (
            EnterIntent(
                self._intent_id(context.symbol, bar.closed_at, "entry"),
                context.symbol,
                direction,
                OrderType.LIMIT,
                entry,
                stop,
                Decimal(str(context.parameters["requested_leverage"])),
                "closed-candle causal channel breakout; limit retest",
                take_profit,
            ),
        )

    def _manage_pending(
        self,
        context: ReadOnlyStrategyContext,
        bar: Candle,
    ) -> Sequence[TradeIntent]:
        if context.position.side is not PositionSide.FLAT:
            self._synchronize_exchange_state(context)
            return ()
        self._pending_age += 1
        if self._cancel_requested:
            return ()
        if self._pending_age >= int(str(context.parameters["entry_valid_bars"])):
            self._cancel_requested = True
            return (
                CancelEntryIntent(
                    self._intent_id(context.symbol, bar.closed_at, "expiry"),
                    context.symbol,
                    "limit retest entry expired",
                ),
            )
        return ()

    def _manage_position(
        self,
        context: ReadOnlyStrategyContext,
        bar: Candle,
    ) -> Sequence[TradeIntent]:
        side = context.position.side
        if side is PositionSide.FLAT:
            self._enter_cooldown(context.parameters)
            return ()
        self._highest_since_entry = max(self._highest_since_entry or bar.high, bar.high)
        self._lowest_since_entry = min(self._lowest_since_entry or bar.low, bar.low)
        atr = latest_wilder_atr(self._candles, int(str(context.parameters["atr_period"])))
        if atr is None or context.protection.confirmed_stop is None:
            return ()
        intents: list[TradeIntent] = []
        if bool(context.parameters["exit_on_opposite_breakout"]):
            upper, lower = causal_channel(
                self._candles[:-1], int(str(context.parameters["entry_lookback"]))
            )
            opposite = (side is PositionSide.LONG and bar.close < lower) or (
                side is PositionSide.SHORT and bar.close > upper
            )
            if opposite:
                return (
                    ExitIntent(
                        self._intent_id(context.symbol, bar.closed_at, "opposite-exit"),
                        context.symbol,
                        "closed opposite channel breakout",
                    ),
                )
        multiplier = Decimal(str(context.parameters["trailing_stop_atr"]))
        current = context.protection.confirmed_stop
        if side is PositionSide.LONG:
            candidate = (self._highest_since_entry or bar.high) - multiplier * atr
            proposed = max(current, candidate)
        else:
            candidate = (self._lowest_since_entry or bar.low) + multiplier * atr
            proposed = min(current, candidate)
        proposed = normalize_stop(proposed, side, context.tick_size)
        if (side is PositionSide.LONG and proposed > current) or (
            side is PositionSide.SHORT and proposed < current
        ):
            intents.append(
                UpdateProtectionIntent(
                    self._intent_id(context.symbol, bar.closed_at, "trail"),
                    context.symbol,
                    "monotonic ATR chandelier trailing stop",
                    stop_price=proposed,
                )
            )
        return tuple(intents)

    def _synchronize_exchange_state(self, context: ReadOnlyStrategyContext) -> None:
        if context.position.side is PositionSide.LONG:
            self._state = TrendState.LONG
        elif context.position.side is PositionSide.SHORT:
            self._state = TrendState.SHORT
        elif context.pending_entry is not None:
            self._state = TrendState.ENTRY_PENDING
            self._pending_age = max(self._pending_age, context.pending_entry.age_bars)
        elif self._state in {TrendState.LONG, TrendState.SHORT}:
            self._enter_cooldown(context.parameters)
        elif self._state not in {
            TrendState.COOLDOWN,
            TrendState.ENTRY_PENDING,
            TrendState.PENDING_UNKNOWN,
        }:
            self._state = TrendState.FLAT

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
        self._state = TrendState.COOLDOWN
        self._cooldown_remaining = int(str(parameters["cooldown_bars"]))
        self._snapshot = None
        self._pending_age = 0
        self._cancel_requested = False

    def _reset_flat(self) -> None:
        self._state = TrendState.FLAT
        self._snapshot = None
        self._pending_age = 0
        self._cancel_requested = False

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
        return f"a1-{purpose[:8]}-{digest}"[:36]
