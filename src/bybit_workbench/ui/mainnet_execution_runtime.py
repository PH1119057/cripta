from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Protocol

from bybit_workbench import __version__
from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.credentials import BybitCredentials, WindowsCredentialStore
from bybit_workbench.app.redaction import redact_text
from bybit_workbench.app.state_machine import AppStateMachine, InvalidStateTransition
from bybit_workbench.app.windows_time import resync_windows_time
from bybit_workbench.domain.models import Order
from bybit_workbench.domain.types import (
    AppMode,
    AppState,
    ExecutionMode,
    OrderStatus,
    OrderType,
    PositionSide,
)
from bybit_workbench.exchange.bybit.connection import (
    MainnetExecutionConnection,
    create_mainnet_execution_connection,
)
from bybit_workbench.exchange.bybit.errors import (
    BybitApiError,
    BybitClockSkewError,
    BybitErrorCategory,
)
from bybit_workbench.exchange.bybit.health import BybitHealthSnapshot
from bybit_workbench.exchange.bybit.streams import BybitStreamSnapshot
from bybit_workbench.exchange.bybit.testnet_execution import (
    BybitWriteRejected,
    ExchangeProtectionPlan,
)
from bybit_workbench.execution import ExecutionCommandStatus
from bybit_workbench.execution.mainnet_coordinator import MainnetExecutionCoordinator
from bybit_workbench.execution.mainnet_safety import (
    ExecutionArmingController,
    MainnetArmingTicket,
    MicroLiveEntryPlan,
    MicroLiveLimits,
    issue_micro_live_ticket,
)
from bybit_workbench.execution.mainnet_state import MainnetReadinessContext
from bybit_workbench.persistence import TradingJournal
from bybit_workbench.persistence.mainnet_idempotency import SqliteIdempotencyStore
from bybit_workbench.strategies import ArmedStrategy
from bybit_workbench.ui.manual_workflow import PreparedManualTrade
from bybit_workbench.ui.view_model import ProtectionView, UserFacingError, WorkbenchViewModel


class MainnetRuntimePhase(StrEnum):
    DISARMED = "DISARMED"
    CHECKING = "CHECKING"
    CHECKED = "CHECKED"
    ARMED = "ARMED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    EXPIRED = "EXPIRED"
    KILL_SWITCH = "KILL_SWITCH"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class MainnetRuntimeStatus:
    mode: ExecutionMode
    phase: MainnetRuntimePhase
    detail: str
    ticket_expires_at: datetime | None = None


RuntimeEventKind = Literal["status", "protection", "log", "error"]


@dataclass(frozen=True, slots=True)
class MainnetRuntimeEvent:
    kind: RuntimeEventKind
    payload: Any


class MainnetConnectionFactory(Protocol):
    def __call__(
        self,
        settings: AppSettings,
        credentials: BybitCredentials,
        arming: ExecutionArmingController,
        idempotency: SqliteIdempotencyStore,
        context_provider: Callable[[], MainnetReadinessContext | None],
    ) -> MainnetExecutionConnection: ...


@dataclass(slots=True)
class _MainnetSession:
    prepared_run_id: str
    strategy: ArmedStrategy
    arming: ExecutionArmingController
    ticket: MainnetArmingTicket
    connection: MainnetExecutionConnection
    coordinator: MainnetExecutionCoordinator
    journal: TradingJournal
    idempotency: SqliteIdempotencyStore


class MainnetExecutionRuntime:
    """Desktop Mainnet lifecycle; construction and restart are always disarmed.

    `prepare` performs GET-only preflight on a background thread.  The only path
    to a write-capable operation is then exact manual arming followed by the
    coordinator, which itself only owns the safety-gated mutation gateway.
    """

    def __init__(
        self,
        settings: AppSettings,
        state_machine: AppStateMachine,
        *,
        context_provider: Callable[[], MainnetReadinessContext | None],
        private_snapshot_provider: Callable[
            [], tuple[BybitStreamSnapshot, BybitHealthSnapshot] | None
        ]
        | None = None,
        armed_strategy_provider: Callable[[PreparedManualTrade], ArmedStrategy] | None = None,
        limits_provider: Callable[[PreparedManualTrade], MicroLiveLimits] | None = None,
        credential_store: WindowsCredentialStore | None = None,
        connection_factory: MainnetConnectionFactory = create_mainnet_execution_connection,
        journal_factory: Callable[[Any], TradingJournal] = TradingJournal,
        poll_seconds: float = 0.5,
    ) -> None:
        settings.validate_startup()
        if settings.mode is not AppMode.LIVE:
            raise PermissionError("Mainnet runtime requires the live profile")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.settings = settings
        self.state_machine = state_machine
        self.context_provider = context_provider
        self.private_snapshot_provider = private_snapshot_provider
        self.armed_strategy_provider = armed_strategy_provider
        self.limits_provider = limits_provider or default_micro_live_limits
        self.credential_store = credential_store or WindowsCredentialStore()
        self.connection_factory = connection_factory
        self.journal_factory = journal_factory
        self.poll_seconds = poll_seconds
        self._events: queue.SimpleQueue[MainnetRuntimeEvent] = queue.SimpleQueue()
        self._stop = threading.Event()
        self._emergency = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._session: _MainnetSession | None = None
        self._status = MainnetRuntimeStatus(
            ExecutionMode.SHADOW,
            MainnetRuntimePhase.DISARMED,
            "Каждый запуск начинается в SHADOW; mutating requests заблокированы.",
        )

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def status(self) -> MainnetRuntimeStatus:
        with self._lock:
            return self._status

    def reconfigure(self, settings: AppSettings) -> None:
        """Switch Mainnet connection settings while no execution workflow is active."""

        settings.validate_startup()
        if settings.mode is not AppMode.LIVE:
            raise PermissionError("Mainnet runtime requires the live profile")
        with self._lock:
            if self.running:
                raise RuntimeError("stop the active Mainnet workflow before changing endpoint")
            self._dispose_session_locked()
            self.settings = settings
            endpoint = settings.endpoint_profile.rest_url or "offline"
            self._set_status_locked(
                MainnetRuntimeStatus(
                    ExecutionMode.SHADOW,
                    MainnetRuntimePhase.DISARMED,
                    f"Mainnet endpoint changed to {endpoint}; a new Check is required.",
                )
            )

    def prepare(self, prepared: PreparedManualTrade) -> None:
        if not prepared.decision.approved or prepared.decision.normalized_order is None:
            raise PermissionError("only an approved normalized trade can enter Mainnet Check")
        if self.armed_strategy_provider is None:
            raise PermissionError(
                "Mainnet strategy arming provider is not connected; "
                "preflight remains fail-closed"
            )
        strategy = self.armed_strategy_provider(prepared)
        if not self.settings.allow_live_trading:
            raise PermissionError("external BYBIT_WORKBENCH_ALLOW_LIVE_TRADING switch is off")
        with self._lock:
            if self.running:
                raise RuntimeError("a Mainnet workflow is already active")
            self._dispose_session_locked()
            self._stop.clear()
            self._emergency.clear()
            self._set_status_locked(
                MainnetRuntimeStatus(
                    ExecutionMode.SHADOW,
                    MainnetRuntimePhase.CHECKING,
                    "GET-only account-wide preflight выполняется; после успеха "
                    "pipeline продолжит автоматически.",
                )
            )
            self._thread = threading.Thread(
                target=self._prepare_worker,
                args=(prepared, strategy),
                name="mainnet-preflight",
                daemon=True,
            )
            self._thread.start()

    def arm(self, confirmation: str) -> None:
        with self._lock:
            session = self._required_session_locked()
            if self.running:
                raise RuntimeError("wait until Mainnet Check completes")
            if self._status.phase is not MainnetRuntimePhase.CHECKED:
                raise PermissionError("Mainnet workflow must be CHECKED before Arm")
            session.arming.arm_micro_live(confirmation, session.ticket)
            self._set_status_locked(
                MainnetRuntimeStatus(
                    ExecutionMode.MICRO_LIVE,
                    MainnetRuntimePhase.ARMED,
                    "Короткоживущий ticket вооружён в памяти; pipeline готовит отправку.",
                    session.ticket.expires_at,
                )
            )

    def submit(self, prepared: PreparedManualTrade) -> None:
        with self._lock:
            session = self._required_session_locked()
            if self.running:
                raise RuntimeError("a Mainnet execution workflow is already active")
            if self._status.phase is not MainnetRuntimePhase.ARMED:
                raise PermissionError("Mainnet ticket must be ARMED before Run")
            if session.prepared_run_id != prepared.run_id:
                raise PermissionError("checked Mainnet plan changed before Run")
            if self.state_machine.state is not AppState.RUNNING:
                raise PermissionError("engine must be RUNNING before Mainnet submission")
            self._stop.clear()
            self._emergency.clear()
            self._set_status_locked(
                MainnetRuntimeStatus(
                    ExecutionMode.MICRO_LIVE,
                    MainnetRuntimePhase.RUNNING,
                    "Execution передан единому Mainnet coordinator.",
                    session.ticket.expires_at,
                )
            )
            self._thread = threading.Thread(
                target=self._run_worker,
                args=(session, prepared),
                name="mainnet-execution",
                daemon=True,
            )
            self._thread.start()

    def request_stop(self) -> None:
        self._stop.set()

    def request_cancel_entries_for_symbol(self, symbol: str) -> None:
        self._start_maintenance("cancel entries", symbol, entries_only=True)

    def request_cancel_non_protective_for_symbol(self, symbol: str) -> None:
        self._start_maintenance("cancel non-protective", symbol, entries_only=False)

    def request_flatten_for_symbol(self, symbol: str) -> None:
        self._start_maintenance("flatten", symbol)

    def request_emergency(self) -> None:
        if not self.running:
            raise RuntimeError("there is no active Mainnet workflow")
        self._emergency.set()

    def request_emergency_for_symbol(self, symbol: str) -> None:
        with self._lock:
            if self.running:
                self._emergency.set()
                return
        self._start_maintenance("emergency", symbol)

    def invalidate(self, reason: str) -> None:
        with self._lock:
            if self.running:
                self._stop.set()
                return
            self._dispose_session_locked()
            self._set_status_locked(
                MainnetRuntimeStatus(
                    ExecutionMode.SHADOW,
                    MainnetRuntimePhase.DISARMED,
                    f"Mainnet plan invalidated: {reason}",
                )
            )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        with self._lock:
            if thread is not None and thread.is_alive():
                self._set_status_locked(
                    MainnetRuntimeStatus(
                        self._status.mode,
                        MainnetRuntimePhase.BLOCKED,
                        "Runtime thread did not stop; resources remain open and entries blocked.",
                        self._status.ticket_expires_at,
                    )
                )
                return
            self._dispose_session_locked()
            self._set_status_locked(
                MainnetRuntimeStatus(
                    ExecutionMode.SHADOW,
                    MainnetRuntimePhase.DISARMED,
                    "Runtime stopped; in-memory Mainnet ticket destroyed.",
                )
            )

    def drain_into(self, model: WorkbenchViewModel) -> int:
        count = 0
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            count += 1
            if event.kind == "status":
                status = event.payload
                model.set_execution_status(
                    status.mode,
                    status.phase.value,
                    status.detail,
                    status.ticket_expires_at,
                )
            elif event.kind == "protection":
                model.set_protection(event.payload)
            elif event.kind == "log":
                model.append_system_log(event.payload)
            elif event.kind == "error":
                model.set_error(event.payload)
        self._mark_expired_ticket_if_needed()
        return count

    def _prepare_worker(
        self,
        prepared: PreparedManualTrade,
        strategy: ArmedStrategy,
    ) -> None:
        credentials: BybitCredentials | None = None
        journal: TradingJournal | None = None
        idempotency: SqliteIdempotencyStore | None = None
        try:
            credentials = self.credential_store.load(
                AppMode.LIVE,
                name=self.settings.credential_profile_name,
            )
            if credentials is None:
                raise RuntimeError("Mainnet API-key profile is not saved")
            arming = ExecutionArmingController()
            idempotency = SqliteIdempotencyStore(self.settings.database_path)
            connection = self.connection_factory(
                self.settings,
                credentials,
                arming,
                idempotency,
                self.context_provider,
            )
            snapshot = asyncio.run(
                connection.state_provider.snapshot(prepared.intent.symbol)
            )
            entry_plan = micro_live_entry_plan(prepared)
            ticket = issue_micro_live_ticket(
                self.settings,
                snapshot,
                self.limits_provider(prepared),
                strategy,
                entry_plan,
            )
            if self._stop.is_set():
                raise InterruptedError("Mainnet preflight was invalidated")
            journal = self.journal_factory(self.settings.database_path)
            coordinator = MainnetExecutionCoordinator(
                self.settings,
                connection.gateway,
                connection.reader,
                journal,
                self.state_machine,
                private_snapshot_provider=self.private_snapshot_provider,
            )
            session = _MainnetSession(
                prepared.run_id,
                strategy,
                arming,
                ticket,
                connection,
                coordinator,
                journal,
                idempotency,
            )
            with self._lock:
                self._session = session
                self._set_status_locked(
                    MainnetRuntimeStatus(
                        ExecutionMode.SHADOW,
                        MainnetRuntimePhase.CHECKED,
                        "GET-only preflight и ticket gate пройдены; pipeline может "
                        "продолжить отправку.",
                        ticket.expires_at,
                    )
                )
            self._emit("log", "Mainnet GET-only preflight completed; transport remains SHADOW")
            journal = None
            idempotency = None
        except InterruptedError as exc:
            with self._lock:
                self._set_status_locked(
                    MainnetRuntimeStatus(
                        ExecutionMode.SHADOW,
                        MainnetRuntimePhase.DISARMED,
                        str(exc),
                    )
                )
        except Exception as exc:
            self._maybe_resync_after_clock_error(exc)
            safe = _redacted_error(exc, credentials)
            with self._lock:
                self._set_status_locked(
                    MainnetRuntimeStatus(
                        ExecutionMode.SHADOW,
                        MainnetRuntimePhase.BLOCKED,
                        safe,
                    )
                )
            self._emit(
                "error",
                UserFacingError(
                    safe,
                    "Mainnet остался SHADOW; ни одной mutating-команды не отправлено.",
                    "Устраните blocker, обновите read-only sync и снова исполните сделку.",
                ),
            )
        finally:
            if journal is not None:
                journal.close()
            if idempotency is not None:
                idempotency.close()

    def _run_worker(
        self,
        session: _MainnetSession,
        prepared: PreparedManualTrade,
    ) -> None:
        final_status = "ERROR"
        credentials: BybitCredentials | None = None
        try:
            credentials = self.credential_store.load(
                AppMode.LIVE,
                name=self.settings.credential_profile_name,
            )
            self._persist_audit_chain(session, prepared)
            decision = prepared.decision
            order_request = decision.normalized_order
            if order_request is None or decision.normalized_stop is None:
                raise RuntimeError("approved Mainnet trade has no normalized order or hard stop")
            protection = ExchangeProtectionPlan(
                decision.normalized_stop,
                prepared.intent.take_profit,
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
            acknowledgement = asyncio.run(
                session.coordinator.submit_entry(
                    order_request,
                    protection,
                    intent_id=prepared.intent.intent_id,
                )
            )
            self._emit(
                "log",
                "Mainnet entry acknowledged; waiting for Private WS / REST confirmation: "
                f"orderId={acknowledgement.order_id}",
            )
            order = asyncio.run(
                session.coordinator.wait_for_entry_confirmation(
                    order_request.symbol,
                    order_request.client_order_id,
                )
            )
            protected_quantity = Decimal("0")
            while True:
                if self._emergency.is_set():
                    self._perform_emergency(
                        session,
                        order_request.symbol,
                        prepared.intent.intent_id,
                    )
                    final_status = "EMERGENCY_STOP"
                    return
                if order.filled_quantity > protected_quantity:
                    protected_quantity = self._ensure_protection(
                        session,
                        order,
                        protected_quantity,
                        protection,
                        prepared.intent.intent_id,
                    )
                if self._stop.wait(self.poll_seconds):
                    self._stop_active_entry(session, order, protection, prepared.intent.intent_id)
                    final_status = "STOPPED"
                    self._set_status(
                        MainnetRuntimeStatus(
                            ExecutionMode.MICRO_LIVE,
                            MainnetRuntimePhase.PAUSED,
                            "Strategy stopped; active entry cancelled and fills remain protected.",
                            session.ticket.expires_at,
                        )
                    )
                    return
                order = asyncio.run(
                    session.coordinator.observe_order(
                        order_request.symbol,
                        order_request.client_order_id,
                    )
                ) or order
                command = session.journal.execution_command(
                    idempotency_key=f"mainnet:entry:{order_request.client_order_id}"
                )
                if command is not None and command.status is not ExecutionCommandStatus.CONFIRMED:
                    session.coordinator.confirm_entry(order)
                if order.status in {OrderStatus.CANCELLED, OrderStatus.REJECTED}:
                    final_status = order.status.value.upper()
                    self._set_status(
                        MainnetRuntimeStatus(
                            ExecutionMode.MICRO_LIVE,
                            MainnetRuntimePhase.PAUSED,
                            f"Entry is {order.status.value}; new entries paused.",
                            session.ticket.expires_at,
                        )
                    )
                    return
                if protected_quantity > 0:
                    position = asyncio.run(
                        session.coordinator.observe_position(order_request.symbol)
                    )
                    if position.position.side is PositionSide.FLAT:
                        final_status = "CLOSED"
                        self._set_status(
                            MainnetRuntimeStatus(
                                ExecutionMode.MICRO_LIVE,
                                MainnetRuntimePhase.PAUSED,
                                "Position is confirmed flat; new entry requires a new "
                                "execution cycle.",
                                session.ticket.expires_at,
                            )
                        )
                        return
        except Exception as exc:
            self._maybe_resync_after_clock_error(exc)
            safe = _redacted_error(exc, credentials)
            if self.state_machine.state is AppState.RUNNING:
                with suppress(InvalidStateTransition):
                    self.state_machine.transition(AppState.PAUSED, "Mainnet execution error")
            self._set_status(
                MainnetRuntimeStatus(
                    ExecutionMode.MICRO_LIVE,
                    MainnetRuntimePhase.BLOCKED,
                    safe,
                    session.ticket.expires_at,
                )
            )
            self._emit(
                "error",
                UserFacingError(
                    safe,
                    "Новые входы остановлены; слепой retry запрещён.",
                    "Проверьте Mainnet Orders/Position в Bybit и выполните reconciliation.",
                ),
            )
        finally:
            with suppress(Exception):
                session.journal.finish_strategy_run(prepared.run_id, final_status)

    def _ensure_protection(
        self,
        session: _MainnetSession,
        order: Order,
        protected_quantity: Decimal,
        protection: ExchangeProtectionPlan,
        intent_id: str,
    ) -> Decimal:
        position = asyncio.run(session.coordinator.observe_position(order.request.symbol))
        if position.position.side is PositionSide.FLAT:
            return protected_quantity
        if (
            position.stop_loss != protection.stop_loss
            or (
                protection.take_profit is not None
                and position.take_profit != protection.take_profit
            )
        ):
            try:
                asyncio.run(
                    session.coordinator.set_protection(
                        position,
                        protection,
                        intent_id=intent_id,
                    )
                )
            except Exception:
                self._perform_emergency(session, order.request.symbol, intent_id)
                raise
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
            "Server-side protection confirmed for filled quantity "
            f"{position.position.quantity} via {session.coordinator.last_observation_source}",
        )
        return max(protected_quantity, position.position.quantity)

    def _stop_active_entry(
        self,
        session: _MainnetSession,
        order: Order,
        protection: ExchangeProtectionPlan,
        intent_id: str,
    ) -> None:
        if order.status in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
            asyncio.run(session.coordinator.cancel_order(order, entries_only=True))
        if order.filled_quantity > 0:
            self._ensure_protection(
                session,
                order,
                Decimal("0"),
                protection,
                intent_id,
            )

    def _perform_emergency(
        self,
        session: _MainnetSession,
        symbol: str,
        intent_id: str | None,
    ) -> None:
        session.arming.activate_kill_switch()
        self._set_status(
            MainnetRuntimeStatus(
                ExecutionMode.MICRO_LIVE,
                MainnetRuntimePhase.KILL_SWITCH,
                "Kill switch active: only cancel and reduce-only are permitted.",
                session.ticket.expires_at,
            )
        )
        with suppress(Exception):
            asyncio.run(session.coordinator.cancel_for_symbol(symbol, entries_only=True))
        position = asyncio.run(session.coordinator.observe_position(symbol))
        acknowledgement = asyncio.run(
            session.coordinator.close_position(position.position, intent_id=intent_id)
        )
        if acknowledgement is None:
            return
        for _ in range(20):
            observed = asyncio.run(session.coordinator.observe_position(symbol))
            if observed.position.side is PositionSide.FLAT:
                command = session.journal.execution_command(
                    idempotency_key=(
                        f"mainnet:close:{intent_id or '-'}:{symbol}:"
                        f"{position.position.side.value}:{position.position.quantity}"
                    )
                )
                if command is not None:
                    session.coordinator.confirm_flat(command.command_id, observed.position)
                return
            if self._stop.wait(self.poll_seconds):
                continue
        raise RuntimeError("emergency reduce-only close is not confirmed flat")

    def _start_maintenance(
        self,
        action: str,
        symbol: str,
        *,
        entries_only: bool | None = None,
    ) -> None:
        selected = symbol.strip().upper()
        if not selected:
            raise ValueError("symbol is required")
        with self._lock:
            session = self._required_session_locked()
            if self.running:
                if action == "emergency":
                    self._emergency.set()
                    return
                raise RuntimeError("stop the active Mainnet workflow before maintenance")
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._maintenance_worker,
                args=(session, action, selected, entries_only),
                name="mainnet-maintenance",
                daemon=True,
            )
            self._thread.start()

    def _maintenance_worker(
        self,
        session: _MainnetSession,
        action: str,
        symbol: str,
        entries_only: bool | None,
    ) -> None:
        try:
            if action.startswith("cancel"):
                results = asyncio.run(
                    session.coordinator.cancel_for_symbol(
                        symbol,
                        entries_only=bool(entries_only),
                    )
                )
                self._emit("log", f"Mainnet maintenance acknowledged {len(results)} cancels")
            elif action == "flatten":
                position = asyncio.run(session.coordinator.observe_position(symbol))
                asyncio.run(session.coordinator.close_position(position.position))
                self._emit("log", "Mainnet reduce-only flatten acknowledged")
            elif action == "emergency":
                self._perform_emergency(session, symbol, None)
                self._emit("log", "Mainnet emergency path confirmed flat")
            else:
                raise ValueError(f"unknown maintenance action: {action}")
        except Exception as exc:
            self._maybe_resync_after_clock_error(exc)
            self._emit(
                "error",
                UserFacingError(
                    _redacted_error(exc, None),
                    "Новые входы не отправлялись; blind retry не выполнялся.",
                    "Проверьте Mainnet Orders/Position и выполните reconciliation.",
                ),
            )

    def _maybe_resync_after_clock_error(self, error: Exception) -> None:
        is_clock_error = isinstance(error, BybitClockSkewError) or (
            isinstance(error, (BybitApiError, BybitWriteRejected))
            and error.category is BybitErrorCategory.CLOCK_SKEW
        )
        if not is_clock_error:
            return
        result = resync_windows_time()
        status = "succeeded" if result.succeeded else "failed"
        self._emit(
            "log",
            f"Windows clock resync {status} after Bybit clock error: {result.detail}. "
            "Mutation is not retried automatically.",
        )

    def _persist_audit_chain(
        self,
        session: _MainnetSession,
        prepared: PreparedManualTrade,
    ) -> None:
        session.journal.start_strategy_run(
            prepared.run_id,
            strategy_id=session.strategy.strategy_id,
            strategy_version=session.strategy.strategy_version,
            code_version=__version__,
            mode="Micro-Live",
            symbol=prepared.intent.symbol,
            parameters=session.strategy.parameters,
            started_at=prepared.checked_at,
        )
        session.journal.record_strategy_decision(
            prepared.decision_id,
            prepared.run_id,
            inputs={"intent": prepared.intent},
            decision={
                "approved": prepared.decision.approved,
                "checks": prepared.decision.checks,
                "historical_parameters_fingerprint": (
                    session.strategy.historical_gate.parameters_fingerprint
                ),
                "historical_report_id": session.strategy.historical_gate.report_id,
                "historical_dataset_fingerprint": (
                    session.strategy.historical_gate.dataset_fingerprint
                ),
                "historical_binding_fingerprint": (
                    session.strategy.historical_gate.binding_fingerprint
                ),
                "risk_percent": prepared.risk_profile.max_risk_percent,
                "absolute_risk_cap": prepared.risk_profile.max_risk_amount,
                "risk_budget": prepared.decision.risk_budget,
                "estimated_loss_at_stop": prepared.decision.estimated_loss_at_stop,
            },
            created_at=prepared.checked_at,
        )
        session.journal.record_trade_intent(
            prepared.intent,
            prepared.run_id,
            decision_id=prepared.decision_id,
            created_at=prepared.checked_at,
        )
        session.journal.record_risk_decision(
            prepared.risk_decision_id,
            prepared.intent.intent_id,
            prepared.decision,
            created_at=prepared.checked_at,
        )

    def _mark_expired_ticket_if_needed(self) -> None:
        with self._lock:
            session = self._session
            if session is None or self._status.phase not in {
                MainnetRuntimePhase.CHECKED,
                MainnetRuntimePhase.ARMED,
            }:
                return
            if session.ticket.is_valid_at(datetime.now(UTC)):
                return
            self._set_status_locked(
                MainnetRuntimeStatus(
                    session.arming.mode,
                    MainnetRuntimePhase.EXPIRED,
                    "Ticket expired: new entries are blocked; cancel/reduce-only remain gated.",
                    session.ticket.expires_at,
                )
            )

    def _required_session_locked(self) -> _MainnetSession:
        if self._session is None:
            raise PermissionError("Mainnet Check has not produced an in-memory session")
        return self._session

    def _dispose_session_locked(self) -> None:
        session = self._session
        self._session = None
        if session is None:
            return
        session.arming.disarm()
        with suppress(Exception):
            session.journal.close()
        with suppress(Exception):
            session.idempotency.close()

    def _set_status(self, status: MainnetRuntimeStatus) -> None:
        with self._lock:
            self._set_status_locked(status)

    def _set_status_locked(self, status: MainnetRuntimeStatus) -> None:
        self._status = status
        self._events.put(MainnetRuntimeEvent("status", status))

    def _emit(self, kind: RuntimeEventKind, payload: Any) -> None:
        self._events.put(MainnetRuntimeEvent(kind, payload))


def micro_live_entry_plan(prepared: PreparedManualTrade) -> MicroLiveEntryPlan:
    decision = prepared.decision
    order = decision.normalized_order
    if order is None or decision.normalized_stop is None:
        raise PermissionError(
            "approved Micro-Live plan must contain normalized entry facts and stop"
        )
    if order.order_type is OrderType.LIMIT and order.price is None:
        raise PermissionError("approved Limit Micro-Live plan is missing limit price")
    reference_entry = decision.normalized_entry or order.price
    if reference_entry is None:
        raise PermissionError("approved Market Micro-Live plan is missing entry reference")
    if decision.risk_budget is None or decision.estimated_loss_at_stop is None:
        raise PermissionError("approved Micro-Live plan is missing risk-budget facts")
    risk_percent = prepared.risk_profile.max_risk_percent
    if risk_percent <= 0:
        raise PermissionError("Micro-Live requires a positive percentage risk budget")
    return MicroLiveEntryPlan(
        symbol=order.symbol,
        client_order_id=order.client_order_id,
        side=order.side,
        quantity=order.quantity,
        # For Market this is the sealed risk/reference entry, not a price sent to Bybit.
        limit_price=order.price or reference_entry,
        stop_loss=decision.normalized_stop,
        take_profit=prepared.intent.take_profit,
        risk_percent=risk_percent,
        risk_budget=decision.risk_budget,
        estimated_loss_at_stop=decision.estimated_loss_at_stop,
        order_type=order.order_type,
    )


def default_micro_live_limits(prepared: PreparedManualTrade) -> MicroLiveLimits:
    plan = micro_live_entry_plan(prepared)
    equity_at_check = prepared.equity_at_check
    if equity_at_check is None:
        equity_at_check = plan.risk_budget * Decimal("100") / plan.risk_percent
    notional = plan.limit_price * plan.quantity
    if plan.order_type is OrderType.LIMIT:
        execution_cap = notional
    else:
        # Market has no caller-supplied execution price. Allow a small local mark drift
        # during the GET-only preflight, but never exceed the operator's position cap.
        market_drift_cap = notional * Decimal("1.02")
        execution_cap = max(
            notional,
            min(prepared.risk_profile.max_position_notional, market_drift_cap),
        )
    return MicroLiveLimits(
        allowed_symbols=frozenset({plan.symbol}),
        max_order_notional=execution_cap,
        max_total_exposure=execution_cap,
        max_daily_loss=prepared.risk_profile.daily_loss_limit(equity_at_check),
        max_orders_per_interval=1,
        order_interval=timedelta(minutes=5),
        cooldown=timedelta(minutes=5),
        required_leverage=prepared.intent.leverage,
        require_isolated_margin=True,
    )


def _redacted_error(
    error: Exception,
    credentials: BybitCredentials | None,
) -> str:
    return redact_text(error, credentials)
