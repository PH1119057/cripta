from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from bybit_workbench import __version__
from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.credentials import BybitCredentials, WindowsCredentialStore
from bybit_workbench.app.redaction import redact_text
from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.domain.models import Order
from bybit_workbench.domain.types import (
    AppMode,
    AppState,
    OrderRole,
    OrderStatus,
    PositionSide,
)
from bybit_workbench.exchange.bybit.connection import create_testnet_execution_adapter
from bybit_workbench.exchange.bybit.health import BybitHealthSnapshot
from bybit_workbench.exchange.bybit.streams import BybitStreamSnapshot
from bybit_workbench.exchange.bybit.testnet_execution import (
    BybitTestnetExecutionAdapter,
    ExchangeProtectionPlan,
)
from bybit_workbench.execution import AmbiguousExecutionCommand, ExecutionCommandStatus
from bybit_workbench.execution.testnet_coordinator import TestnetExecutionCoordinator
from bybit_workbench.persistence import TradingJournal
from bybit_workbench.ui.manual_workflow import PreparedManualTrade
from bybit_workbench.ui.view_model import (
    ProtectionView,
    UserFacingError,
    WorkbenchViewModel,
)

RuntimeEventKind = Literal["protection", "log", "error"]


@dataclass(frozen=True, slots=True)
class TestnetRuntimeEvent:
    kind: RuntimeEventKind
    payload: Any


class TestnetExecutionRuntime:
    """One-manual-trade Testnet worker; no Live construction path exists."""

    def __init__(
        self,
        settings: AppSettings,
        state_machine: AppStateMachine,
        *,
        credential_store: WindowsCredentialStore | None = None,
        adapter_factory: Callable[
            [AppSettings, BybitCredentials], BybitTestnetExecutionAdapter
        ] = create_testnet_execution_adapter,
        journal_factory: Callable[[Any], TradingJournal] = TradingJournal,
        poll_seconds: float = 0.5,
        private_snapshot_provider: Callable[
            [], tuple[BybitStreamSnapshot, BybitHealthSnapshot] | None
        ]
        | None = None,
    ) -> None:
        settings.validate_startup()
        if settings.mode is not AppMode.TESTNET or not settings.testnet_execution_allowed:
            raise PermissionError("Testnet execution runtime is externally locked")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.settings = settings
        self.state_machine = state_machine
        self.credential_store = credential_store or WindowsCredentialStore()
        self.adapter_factory = adapter_factory
        self.journal_factory = journal_factory
        self.poll_seconds = poll_seconds
        self.private_snapshot_provider = private_snapshot_provider
        self._events: queue.SimpleQueue[TestnetRuntimeEvent] = queue.SimpleQueue()
        self._stop = threading.Event()
        self._emergency = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def submit(
        self,
        prepared: PreparedManualTrade,
        health: BybitHealthSnapshot,
    ) -> None:
        if not prepared.decision.approved or prepared.decision.normalized_order is None:
            raise PermissionError("only an approved normalized trade can be submitted")
        with self._lock:
            if self.running:
                raise RuntimeError("a Testnet execution workflow is already active")
            self._stop.clear()
            self._emergency.clear()
            self._thread = threading.Thread(
                target=self._run,
                args=(prepared, health),
                name="testnet-execution",
                daemon=True,
            )
            self._thread.start()

    def request_stop(self) -> None:
        self._stop.set()

    def request_cancel_entries_for_symbol(self, symbol: str) -> None:
        if self.running:
            self.request_stop()
            return
        self._start_order_cancellation(symbol, entries_only=True)

    def request_cancel_non_protective_for_symbol(self, symbol: str) -> None:
        if self.running:
            raise RuntimeError("pause the active workflow before cancelling all orders")
        self._start_order_cancellation(symbol, entries_only=False)

    def request_flatten_for_symbol(self, symbol: str) -> None:
        self.request_emergency_for_symbol(symbol)

    def _start_order_cancellation(self, symbol: str, *, entries_only: bool) -> None:
        if not symbol.strip():
            raise ValueError("symbol is required")
        with self._lock:
            if self.running:
                raise RuntimeError("a Testnet execution workflow is already active")
            self._stop.clear()
            self._emergency.clear()
            self._thread = threading.Thread(
                target=self._run_order_cancellation,
                args=(symbol.strip().upper(), entries_only),
                name="testnet-order-cancellation",
                daemon=True,
            )
            self._thread.start()

    def request_emergency(self) -> None:
        if not self.running:
            raise RuntimeError(
                "no active Testnet execution workflow; use the Bybit interface "
                "and reconcile an externally opened position"
            )
        self._emergency.set()

    def request_emergency_for_symbol(self, symbol: str) -> None:
        if not symbol.strip():
            raise ValueError("symbol is required for emergency recovery")
        with self._lock:
            if self.running:
                self._emergency.set()
                return
            self._stop.clear()
            self._emergency.clear()
            self._thread = threading.Thread(
                target=self._run_existing_position_emergency,
                args=(symbol.strip().upper(),),
                name="testnet-existing-position-emergency",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self.request_stop()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def drain_into(self, model: WorkbenchViewModel) -> int:
        count = 0
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return count
            count += 1
            if event.kind == "protection":
                model.set_protection(event.payload)
            elif event.kind == "log":
                model.append_system_log(event.payload)
            elif event.kind == "error":
                model.set_error(event.payload)

    def _run(
        self,
        prepared: PreparedManualTrade,
        health: BybitHealthSnapshot,
    ) -> None:
        journal: TradingJournal | None = None
        credentials: BybitCredentials | None = None
        final_status = "ERROR"
        try:
            credentials = self.credential_store.load(AppMode.TESTNET)
            if credentials is None:
                raise RuntimeError("Testnet API-key profile is not saved")
            adapter = self.adapter_factory(self.settings, credentials)
            journal = self.journal_factory(self.settings.database_path)
            self._persist_audit_chain(journal, prepared)
            coordinator = TestnetExecutionCoordinator(
                self.settings,
                adapter,
                journal,
                self.state_machine,
                private_snapshot_provider=self.private_snapshot_provider,
            )
            decision = prepared.decision
            order_request = decision.normalized_order
            if order_request is None or decision.normalized_stop is None:
                raise RuntimeError("approved trade has no normalized order or hard stop")
            protection = ExchangeProtectionPlan(
                decision.normalized_stop,
                prepared.intent.take_profit,
            )
            self._emit(
                "protection",
                ProtectionView(
                    planned_stop=protection.stop_loss,
                    planned_take_profit=protection.take_profit,
                ),
            )
            acknowledgement = asyncio.run(
                coordinator.submit_entry(
                    order_request,
                    protection,
                    health,
                    intent_id=prepared.intent.intent_id,
                )
            )
            self._emit(
                "protection",
                ProtectionView(
                    planned_stop=protection.stop_loss,
                    requested_stop=protection.stop_loss,
                    planned_take_profit=protection.take_profit,
                    requested_take_profit=protection.take_profit,
                ),
            )
            self._emit(
                "log",
                f"Testnet entry acknowledged: orderId={acknowledgement.order_id} "
                f"orderLinkId={acknowledgement.client_order_id}",
            )
            protected_quantity = Decimal("0")
            while True:
                if self._emergency.is_set():
                    asyncio.run(
                        self._emergency_action(
                            coordinator,
                            adapter,
                            order_request.symbol,
                            order_request.client_order_id,
                            prepared.intent.intent_id,
                        )
                    )
                    final_status = "EMERGENCY_STOP"
                    return
                if self._stop.wait(self.poll_seconds):
                    break
                order = asyncio.run(
                    coordinator.observe_order(
                        order_request.symbol,
                        order_request.client_order_id,
                    )
                )
                if order is None:
                    continue
                entry_command = journal.execution_command(
                    idempotency_key=f"entry:{order_request.client_order_id}"
                )
                if (
                    entry_command is not None
                    and entry_command.status is not ExecutionCommandStatus.CONFIRMED
                ):
                    coordinator.confirm_entry(order)
                    self._emit(
                        "log",
                        f"Testnet order confirmed via {coordinator.last_observation_source}: "
                        f"{order.status.value}",
                    )
                elif entry_command is not None:
                    coordinator.confirm_entry(order)
                if order.filled_quantity > protected_quantity:
                    position = asyncio.run(coordinator.observe_position(order_request.symbol))
                    if position.position.side is not PositionSide.FLAT:
                        asyncio.run(
                            coordinator.ensure_protection(
                                position,
                                protection,
                                intent_id=prepared.intent.intent_id,
                            )
                        )
                        protected_quantity = position.position.quantity
                        self._emit(
                            "protection",
                            ProtectionView(
                                planned_stop=protection.stop_loss,
                                requested_stop=protection.stop_loss,
                                confirmed_stop=protection.stop_loss,
                                planned_take_profit=protection.take_profit,
                                requested_take_profit=protection.take_profit,
                                confirmed_take_profit=protection.take_profit,
                            ),
                        )
                        self._emit(
                            "log",
                            "Exchange protection confirmed for filled quantity "
                            f"{protected_quantity} via {coordinator.last_observation_source}",
                        )
                if order.status is OrderStatus.FILLED and protected_quantity > 0:
                    position = asyncio.run(coordinator.observe_position(order_request.symbol))
                    if position.position.side is PositionSide.FLAT:
                        final_status = "CLOSED"
                        return
                if order.status in {OrderStatus.CANCELLED, OrderStatus.REJECTED}:
                    final_status = order.status.value.upper()
                    return
            order = asyncio.run(
                coordinator.observe_order(
                    order_request.symbol,
                    order_request.client_order_id,
                )
            )
            if order is None:
                raise AmbiguousExecutionCommand(
                    "stop requested but acknowledged entry is not visible; "
                    "manual reconciliation is required"
                )
            if order is not None and order.status in {
                OrderStatus.ACCEPTED,
                OrderStatus.PARTIALLY_FILLED,
            }:
                asyncio.run(coordinator.cancel_entry(order))
                self._emit("log", "Strategy stop requested cancellation of active entry")
                cancelled = self._wait_for_cancel_confirmation(
                    coordinator,
                    order_request.symbol,
                    order_request.client_order_id,
                )
                if cancelled is not None:
                    coordinator.confirm_cancel(cancelled)
                    self._emit(
                        "log",
                        f"Entry cancellation confirmed via {coordinator.last_observation_source}",
                    )
                if order.filled_quantity > protected_quantity:
                    position = asyncio.run(coordinator.observe_position(order_request.symbol))
                    if position.position.side is not PositionSide.FLAT:
                        asyncio.run(
                            coordinator.ensure_protection(
                                position,
                                protection,
                                intent_id=prepared.intent.intent_id,
                            )
                        )
                        self._emit("log", "Partial fill protection confirmed after Stop")
            final_status = "STOPPED"
        except Exception as exc:
            safe = _redacted_error(exc, credentials)
            if self.state_machine.state is AppState.RUNNING:
                self.state_machine.transition(AppState.PAUSED, "Testnet execution error")
            self._emit(
                "error",
                UserFacingError(
                    safe,
                    "Новые входы остановлены; автоматический повтор заявки не выполнялся.",
                    "Проверьте Testnet Orders/Position и выполните reconciliation.",
                ),
            )
        finally:
            if journal is not None:
                with suppress(Exception):
                    journal.finish_strategy_run(prepared.run_id, final_status)
                journal.close()

    def _persist_audit_chain(
        self,
        journal: TradingJournal,
        prepared: PreparedManualTrade,
    ) -> None:
        journal.start_strategy_run(
            prepared.run_id,
            strategy_id="manual_protected_trade",
            strategy_version="1.0",
            code_version=__version__,
            mode=self.settings.mode.value,
            symbol=prepared.intent.symbol,
            parameters={"source": "desktop_manual_workflow"},
            started_at=prepared.checked_at,
        )
        journal.record_strategy_decision(
            prepared.decision_id,
            prepared.run_id,
            inputs={"intent": prepared.intent},
            decision={
                "approved": prepared.decision.approved,
                "checks": prepared.decision.checks,
            },
            created_at=prepared.checked_at,
        )
        journal.record_trade_intent(
            prepared.intent,
            prepared.run_id,
            decision_id=prepared.decision_id,
            created_at=prepared.checked_at,
        )
        journal.record_risk_decision(
            prepared.risk_decision_id,
            prepared.intent.intent_id,
            prepared.decision,
            created_at=prepared.checked_at,
        )

    def _run_existing_position_emergency(self, symbol: str) -> None:
        journal: TradingJournal | None = None
        credentials: BybitCredentials | None = None
        try:
            credentials = self.credential_store.load(AppMode.TESTNET)
            if credentials is None:
                raise RuntimeError("Testnet API-key profile is not saved")
            adapter = self.adapter_factory(self.settings, credentials)
            journal = self.journal_factory(self.settings.database_path)
            coordinator = TestnetExecutionCoordinator(
                self.settings,
                adapter,
                journal,
                self.state_machine,
                private_snapshot_provider=self.private_snapshot_provider,
            )
            observed = asyncio.run(coordinator.observe_position(symbol))
            command = asyncio.run(
                coordinator.emergency_close(
                    observed.position,
                    intent_id=None,
                )
            )
            if observed.position.side is PositionSide.FLAT:
                self._emit("log", "Recovery emergency: exchange position is already flat")
                return
            for _ in range(20):
                if self._stop.wait(self.poll_seconds):
                    continue
                observed = asyncio.run(coordinator.observe_position(symbol))
                if observed.position.side is not PositionSide.FLAT:
                    continue
                coordinator.confirm_emergency_close(command.command_id, observed.position)
                journal.record_position_snapshot(
                    observed.position,
                    source="restart-emergency-reconciliation",
                    observed_at=observed.observed_at,
                )
                reconciliation_id = f"restart-emergency-{command.command_id}"
                journal.start_reconciliation(
                    reconciliation_id,
                    symbol,
                    started_at=observed.observed_at,
                )
                journal.finish_reconciliation(
                    reconciliation_id,
                    synchronized=True,
                    discrepancies=(),
                    finished_at=observed.observed_at,
                )
                self._emit(
                    "log",
                    "Existing Testnet position emergency close confirmed flat and reconciled",
                )
                return
            raise RuntimeError(
                "emergency close was acknowledged but flat position was not confirmed"
            )
        except Exception as exc:
            self._emit(
                "error",
                UserFacingError(
                    _redacted_error(exc, credentials),
                    "Новые входы остаются заблокированы в EMERGENCY_STOP.",
                    "Проверьте позицию Testnet в Bybit и повторите reconciliation.",
                ),
            )
        finally:
            if journal is not None:
                journal.close()

    def _run_order_cancellation(self, symbol: str, entries_only: bool) -> None:
        journal: TradingJournal | None = None
        credentials: BybitCredentials | None = None
        try:
            credentials = self.credential_store.load(AppMode.TESTNET)
            if credentials is None:
                raise RuntimeError("Testnet API-key profile is not saved")
            adapter = self.adapter_factory(self.settings, credentials)
            journal = self.journal_factory(self.settings.database_path)
            coordinator = TestnetExecutionCoordinator(
                self.settings,
                adapter,
                journal,
                self.state_machine,
                private_snapshot_provider=self.private_snapshot_provider,
            )
            orders = asyncio.run(adapter.open_orders(symbol))
            targets = [
                order
                for order in orders
                if order.status in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}
                and order.request.role is not OrderRole.PROTECTIVE
                and (not entries_only or order.request.role is OrderRole.ENTRY)
            ]
            for order in targets:
                if entries_only:
                    asyncio.run(coordinator.cancel_entry(order))
                else:
                    asyncio.run(coordinator.cancel_non_protective(order))
                cancelled = self._wait_for_cancel_confirmation(
                    coordinator,
                    symbol,
                    order.request.client_order_id,
                )
                if cancelled is None:
                    raise RuntimeError(f"order cancellation not confirmed: {order.order_id}")
                coordinator.confirm_cancel(cancelled)
            remaining = asyncio.run(adapter.open_orders(symbol))
            remaining_targets = [
                order
                for order in remaining
                if order.status in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}
                and order.request.role is not OrderRole.PROTECTIVE
                and (not entries_only or order.request.role is OrderRole.ENTRY)
            ]
            reconciliation_id = f"cancel-orders-{symbol}-{int(datetime.now().timestamp())}"
            observed_at = datetime.now(UTC)
            journal.start_reconciliation(reconciliation_id, symbol, started_at=observed_at)
            journal.finish_reconciliation(
                reconciliation_id,
                synchronized=not remaining_targets,
                discrepancies=tuple(
                    f"order still active: {order.order_id}" for order in remaining_targets
                ),
                finished_at=observed_at,
            )
            if remaining_targets:
                raise RuntimeError("one or more order cancellations remain unconfirmed")
            label = "entry" if entries_only else "non-protective"
            self._emit("log", f"Cancelled {len(targets)} {label} orders; reconciliation passed")
        except Exception as exc:
            self._emit(
                "error",
                UserFacingError(
                    _redacted_error(exc, credentials),
                    "Новые заявки не отправлялись; защитные заявки не отменялись.",
                    "Проверьте Testnet Orders и выполните reconciliation.",
                ),
            )
        finally:
            if journal is not None:
                journal.close()

    async def _emergency_action(
        self,
        coordinator: TestnetExecutionCoordinator,
        adapter: BybitTestnetExecutionAdapter,
        symbol: str,
        client_order_id: str,
        intent_id: str,
    ) -> None:
        order = await coordinator.observe_order(symbol, client_order_id)
        if order is not None and order.status in {
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
        }:
            await coordinator.cancel_entry(order)
            self._emit("log", "Emergency: active entry cancellation acknowledged")
        position = await coordinator.observe_position(symbol)
        command = await coordinator.emergency_close(
            position.position,
            intent_id=intent_id,
        )
        if position.position.side is PositionSide.FLAT:
            self._emit("log", "Emergency: position already flat")
            return
        for _ in range(10):
            if self._stop.wait(self.poll_seconds):
                break
            observed = await coordinator.observe_position(symbol)
            if observed.position.side is PositionSide.FLAT:
                coordinator.confirm_emergency_close(command.command_id, observed.position)
                coordinator.journal.record_position_snapshot(
                    observed.position,
                    source="emergency-reconciliation",
                    observed_at=observed.observed_at,
                )
                reconciliation_id = f"emergency-{command.command_id}"
                coordinator.journal.start_reconciliation(
                    reconciliation_id,
                    symbol,
                    started_at=observed.observed_at,
                )
                coordinator.journal.finish_reconciliation(
                    reconciliation_id,
                    synchronized=True,
                    discrepancies=(),
                    finished_at=observed.observed_at,
                )
                self._emit("log", "Emergency reduce-only close confirmed flat")
                return
        self._emit(
            "error",
            UserFacingError(
                "Emergency close was acknowledged but flat position is not confirmed.",
                "New entries remain blocked in EMERGENCY_STOP.",
                "Check the Bybit position and run reconciliation immediately.",
            ),
        )

    def _emit(self, kind: RuntimeEventKind, payload: Any) -> None:
        self._events.put(TestnetRuntimeEvent(kind, payload))

    def _wait_for_cancel_confirmation(
        self,
        coordinator: TestnetExecutionCoordinator,
        symbol: str,
        client_order_id: str,
        attempts: int = 10,
    ) -> Order | None:
        for _ in range(attempts):
            observed = asyncio.run(coordinator.observe_order(symbol, client_order_id))
            if observed is not None and observed.status is OrderStatus.CANCELLED:
                return observed
            if self._stop.wait(self.poll_seconds):
                continue
        return None


def _redacted_error(
    error: Exception,
    credentials: BybitCredentials | None,
) -> str:
    return redact_text(error, credentials)
