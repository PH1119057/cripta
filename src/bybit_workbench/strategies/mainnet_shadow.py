from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.domain.models import Candle
from bybit_workbench.domain.types import AppMode, AppState, OrderRole
from bybit_workbench.exchange.bybit.models import BybitReadSnapshot
from bybit_workbench.exchange.bybit.rest import BybitReadOnlyAdapter
from bybit_workbench.execution.automatic_intents import ShadowIntentJournal

from .arming import ArmedStrategy
from .base import PendingEntrySnapshot, ProtectionSnapshot, ReadOnlyStrategyContext, Strategy
from .runtime import AutomaticRuntimeDecision, AutomaticStrategyRuntime


@dataclass(frozen=True, slots=True)
class MainnetShadowBootstrap:
    snapshot: BybitReadSnapshot
    warmup_bars: tuple[Candle, ...]


class MainnetShadowSession:
    """GET/stream data pump for AutomaticStrategyRuntime with no execution capability."""

    def __init__(
        self,
        adapter: BybitReadOnlyAdapter,
        armed: ArmedStrategy,
        strategy: Strategy,
        state_machine: AppStateMachine,
        journal: ShadowIntentJournal,
    ) -> None:
        self.adapter = adapter
        self.runtime = AutomaticStrategyRuntime(
            AppMode.LIVE,
            armed,
            strategy,
            state_machine,
            shadow_intent_recorder=journal.record,
        )
        self.journal = journal
        self._snapshot: BybitReadSnapshot | None = None
        self._last_snapshot_observed_at: datetime | None = None
        self._last_bar: Candle | None = None
        self._interval: str | None = None

    async def bootstrap(self, symbol: str, interval: str) -> MainnetShadowBootstrap:
        if self.runtime.state_machine.state is not AppState.ARMED:
            raise PermissionError("Mainnet Shadow session requires an armed historical gate")
        self._require_binding(symbol, interval)
        snapshot = await self._fresh_snapshot(symbol)
        if snapshot.position.position.quantity > 0 and not snapshot.position.stop_loss:
            self.runtime.state_machine.transition(
                AppState.EMERGENCY_STOP,
                "open Mainnet position has no confirmed server-side stop",
            )
            raise RuntimeError("Mainnet position is unprotected; Shadow run aborted")
        required = self.runtime.strategy.warmup_bars(self.runtime.armed.parameters)
        bars = tuple(
            await self.adapter.historical_candles(symbol, interval, limit=min(1000, required + 1))
        )
        if not bars:
            raise RuntimeError("Mainnet Shadow warm-up returned no closed candles")
        for bar in bars:
            if bar.symbol != symbol or bar.timeframe != interval or not bar.is_closed:
                raise ValueError("Mainnet Shadow warm-up returned a mismatched candle")
        context = _context(snapshot, bars[-1], self.runtime.armed.parameters)
        await self.runtime.warm_up(context, bars)
        await self.runtime.start(context)
        self._snapshot = snapshot
        self._last_bar = bars[-1]
        self._interval = interval
        return MainnetShadowBootstrap(snapshot, bars)

    async def reconcile(self, symbol: str) -> BybitReadSnapshot:
        snapshot = await self._fresh_snapshot(symbol)
        self._snapshot = snapshot
        if self._last_bar is not None and self.runtime.state_machine.state is AppState.RUNNING:
            await self.runtime.reconcile(
                _context(snapshot, self._last_bar, self.runtime.armed.parameters)
            )
        return snapshot

    async def process_closed_bar(self, bar: Candle) -> AutomaticRuntimeDecision:
        if self._snapshot is None or self._interval is None:
            raise RuntimeError("Mainnet Shadow session is not bootstrapped")
        if bar.timeframe != self._interval:
            raise ValueError("Mainnet Shadow candle timeframe differs from the armed interval")
        # Every closed candle gets a new exchange snapshot before strategy evaluation.
        snapshot = await self._fresh_snapshot(bar.symbol)
        self._snapshot = snapshot
        context = _context(snapshot, bar, self.runtime.armed.parameters)
        if self.runtime.reconciliation_required:
            await self.runtime.reconcile(context)
        decision = await self.runtime.process_closed_bar(context, bar)
        self._last_bar = bar
        return decision

    async def stop(self, reason: str) -> None:
        await self.runtime.stop(reason)
        self.journal.finish("PAUSED")

    async def _fresh_snapshot(self, symbol: str) -> BybitReadSnapshot:
        snapshot = await self.adapter.read_snapshot(symbol)
        if snapshot.instrument.symbol != symbol:
            raise ValueError("Mainnet Shadow snapshot instrument differs from requested symbol")
        previous = self._last_snapshot_observed_at
        if previous is not None and snapshot.observed_at < previous:
            raise ValueError("Mainnet Shadow received an out-of-order exchange snapshot")
        self._last_snapshot_observed_at = snapshot.observed_at
        return snapshot

    def _require_binding(self, symbol: str, interval: str) -> None:
        query = self.runtime.armed.historical_gate.query
        if query is None:
            return
        if query.symbol != symbol or query.timeframe != interval:
            raise PermissionError("Shadow symbol/timeframe differs from exact historical binding")


def _context(
    snapshot: BybitReadSnapshot,
    bar: Candle,
    parameters: dict[str, object],
) -> ReadOnlyStrategyContext:
    pending = next(
        (order for order in snapshot.open_orders if order.request.role is OrderRole.ENTRY),
        None,
    )
    pending_view = None
    if pending is not None and pending.request.price is not None:
        pending_view = PendingEntrySnapshot(
            pending.request.client_order_id,
            pending.request.side,
            pending.request.price,
            pending.request.quantity,
            pending.remaining_quantity,
            pending.status,
            0,
        )
    position = snapshot.position
    return ReadOnlyStrategyContext(
        snapshot.instrument.symbol,
        bar.close,
        position.position,
        parameters,
        mark_price=position.mark_price,
        protection=ProtectionSnapshot(
            position.stop_loss,
            position.take_profit,
            position.trailing_stop_distance,
        ),
        pending_entry=pending_view,
        tick_size=snapshot.instrument.tick_size,
    )
