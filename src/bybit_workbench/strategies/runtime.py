from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.domain.intents import CancelEntryIntent, EnterIntent
from bybit_workbench.domain.models import Candle, Execution
from bybit_workbench.domain.types import AppMode, AppState, ExecutionMode

from .arming import ArmedStrategy
from .base import (
    IntentOutcome,
    IntentOutcomeStatus,
    ReadOnlyStrategyContext,
    Strategy,
    TradeIntent,
)

IntentSink = Callable[
    [TradeIntent, ReadOnlyStrategyContext],
    Awaitable[IntentOutcome],
]
StateSink = Callable[[Mapping[str, Any]], None]
ShadowIntentRecorder = Callable[[TradeIntent, ReadOnlyStrategyContext, datetime], None]


@dataclass(frozen=True, slots=True)
class AutomaticRuntimeDecision:
    observed_at: datetime
    intents: tuple[TradeIntent, ...]
    outcomes: tuple[IntentOutcome, ...]
    state_snapshot: Mapping[str, Any]


class AutomaticStrategyRuntime:
    """One strategy host shared by Replay, Mainnet Shadow and armed execution."""

    def __init__(
        self,
        mode: AppMode,
        armed: ArmedStrategy,
        strategy: Strategy,
        state_machine: AppStateMachine,
        *,
        intent_sink: IntentSink | None = None,
        state_sink: StateSink | None = None,
        restored_state: Mapping[str, Any] | None = None,
        execution_mode: ExecutionMode = ExecutionMode.SHADOW,
        shadow_intent_recorder: ShadowIntentRecorder | None = None,
    ) -> None:
        if mode is AppMode.DEMO:
            raise PermissionError("automatic strategy runtime is unavailable in Demo")
        if execution_mode is not ExecutionMode.SHADOW and mode is not AppMode.LIVE:
            raise PermissionError("Micro-Live/Live execution requires the Mainnet profile")
        if execution_mode is not ExecutionMode.SHADOW and intent_sink is None:
            raise PermissionError("armed Mainnet execution requires an intent sink")
        if mode is AppMode.TESTNET and intent_sink is None:
            raise PermissionError("legacy Testnet automatic runtime requires an intent sink")
        metadata = strategy.metadata()
        if metadata.strategy_id != armed.strategy_id or metadata.version != armed.strategy_version:
            raise ValueError("armed strategy identity differs from implementation")
        if not armed.historical_gate.allowed:
            raise PermissionError("automatic runtime requires a passed historical gate")
        self.mode = mode
        self.armed = armed
        self.strategy = strategy
        self.state_machine = state_machine
        self.intent_sink = intent_sink
        self.execution_mode = execution_mode
        self.shadow_intent_recorder = shadow_intent_recorder
        self.state_sink = state_sink
        self._started = False
        self._initialized = restored_state is not None
        self._last_closed_at: datetime | None = None
        self._reconciliation_required = bool(
            restored_state is not None and restored_state.get("reconciliation_required", False)
        )
        if restored_state is not None:
            strategy.restore_state(restored_state)

    @property
    def reconciliation_required(self) -> bool:
        return self._reconciliation_required

    async def warm_up(
        self,
        context: ReadOnlyStrategyContext,
        bars: Sequence[Candle],
    ) -> None:
        """Prime the exact strategy implementation while entries are technically disabled."""

        if self._reconciliation_required:
            raise PermissionError("restored strategy state requires reconciliation before warm-up")
        if self._started:
            raise RuntimeError("cannot warm up a running strategy")
        if self.state_machine.state is not AppState.ARMED:
            raise PermissionError("engine must be ARMED before strategy warm-up")
        required = self.strategy.warmup_bars(self.armed.parameters)
        if len(bars) < required:
            raise ValueError(f"strategy requires at least {required} closed warm-up bars")
        if context.position.quantity != 0:
            raise PermissionError("strategy warm-up requires a reconciled flat position")
        if not self._initialized:
            await self.strategy.on_start(context)
            self._initialized = True
        safe_context = replace(
            context,
            health=replace(
                context.health,
                new_entries_allowed=False,
                detail="historical warm-up; intents are disabled",
            ),
        )
        previous: datetime | None = None
        for bar in bars:
            if not bar.is_closed:
                raise ValueError("warm-up accepts closed candles only")
            if previous is not None and bar.closed_at <= previous:
                raise ValueError("warm-up bars must be strictly chronological")
            previous = bar.closed_at
            intents = await self.strategy.on_bar_closed(safe_context, bar)
            for intent in intents:
                outcome = IntentOutcome(
                    intent.intent_id,
                    IntentOutcomeStatus.REJECTED,
                    bar.closed_at,
                    "warm-up intent suppressed",
                )
                await self.strategy.on_intent_outcome(safe_context, outcome)
        self._last_closed_at = previous
        self._persist_state()

    async def start(self, context: ReadOnlyStrategyContext) -> None:
        if self._started:
            return
        if self.state_machine.state is not AppState.ARMED:
            raise PermissionError("engine must be ARMED before automatic runtime starts")
        if not self._initialized:
            await self.strategy.on_start(context)
            self._initialized = True
        self.state_machine.transition(AppState.RUNNING, "automatic strategy runtime started")
        self._started = True
        self._persist_state()

    async def process_closed_bar(
        self,
        context: ReadOnlyStrategyContext,
        bar: Candle,
    ) -> AutomaticRuntimeDecision:
        self._require_started()
        if self._reconciliation_required:
            raise PermissionError("strategy reconciliation is required before the next candle")
        if not bar.is_closed:
            raise ValueError("automatic runtime accepts closed candles only")
        if self._last_closed_at is not None:
            if bar.closed_at == self._last_closed_at:
                return AutomaticRuntimeDecision(
                    bar.closed_at,
                    (),
                    (),
                    self.strategy.snapshot_state(),
                )
            if bar.closed_at < self._last_closed_at:
                raise ValueError("automatic runtime received an out-of-order candle")
        self._last_closed_at = bar.closed_at
        intents = tuple(await self.strategy.on_bar_closed(context, bar))
        outcomes = await self._dispatch(context, intents, bar.closed_at)
        snapshot = self._persist_state()
        return AutomaticRuntimeDecision(bar.closed_at, intents, outcomes, snapshot)

    async def process_execution(
        self,
        context: ReadOnlyStrategyContext,
        execution: Execution,
    ) -> AutomaticRuntimeDecision:
        self._require_started()
        intents = tuple(await self.strategy.on_execution(context, execution))
        outcomes = await self._dispatch(context, intents, execution.executed_at)
        snapshot = self._persist_state()
        return AutomaticRuntimeDecision(execution.executed_at, intents, outcomes, snapshot)

    async def reconcile(self, context: ReadOnlyStrategyContext) -> Mapping[str, Any]:
        self._require_started()
        await self.strategy.on_reconcile(context)
        self._reconciliation_required = False
        return self._persist_state()

    async def stop(self, reason: str) -> None:
        if not self._started:
            return
        await self.strategy.on_stop(reason)
        self._persist_state()
        if self.state_machine.state is AppState.RUNNING:
            self.state_machine.transition(AppState.PAUSED, reason)
        self._started = False

    async def _dispatch(
        self,
        context: ReadOnlyStrategyContext,
        initial: Sequence[TradeIntent],
        observed_at: datetime,
    ) -> tuple[IntentOutcome, ...]:
        queue = list(initial)
        outcomes: list[IntentOutcome] = []
        dispatched = 0
        while queue:
            intent = queue.pop(0)
            dispatched += 1
            if dispatched > 32:
                raise RuntimeError("strategy intent follow-up limit exceeded")
            if self.execution_mode is ExecutionMode.SHADOW:
                if self.shadow_intent_recorder is not None:
                    self.shadow_intent_recorder(intent, context, observed_at)
                detail = (
                    "Replay shadow: intent recorded without write execution"
                    if self.mode is AppMode.REPLAY
                    else "Mainnet Shadow: virtual intent journalled without Bybit execution"
                )
                shadow_status = IntentOutcomeStatus.APPROVED
                if self.mode is AppMode.LIVE and isinstance(intent, EnterIntent):
                    shadow_status = IntentOutcomeStatus.SUBMITTED
                elif self.mode is AppMode.LIVE and isinstance(intent, CancelEntryIntent):
                    shadow_status = IntentOutcomeStatus.CANCELLED
                outcome = IntentOutcome(
                    intent.intent_id,
                    shadow_status,
                    observed_at,
                    detail,
                )
            else:
                if self.intent_sink is None:
                    raise PermissionError("armed execution lost its intent sink")
                outcome = await self.intent_sink(intent, context)
                if outcome.intent_id != intent.intent_id:
                    raise ValueError("intent sink returned an outcome for a different intent")
            outcomes.append(outcome)
            followups = await self.strategy.on_intent_outcome(context, outcome)
            if outcome.status is IntentOutcomeStatus.UNKNOWN:
                self._reconciliation_required = True
            queue.extend(followups)
        return tuple(outcomes)

    def _persist_state(self) -> Mapping[str, Any]:
        snapshot = self.strategy.snapshot_state()
        if self.state_sink is not None:
            self.state_sink(snapshot)
        return snapshot

    def _require_started(self) -> None:
        if not self._started or self.state_machine.state is not AppState.RUNNING:
            raise PermissionError("automatic strategy runtime is not RUNNING")
