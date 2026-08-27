from __future__ import annotations

import asyncio
import queue
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.credentials import BybitCredentials, WindowsCredentialStore
from bybit_workbench.app.redaction import redact_text
from bybit_workbench.app.state_machine import AppStateMachine, InvalidStateTransition
from bybit_workbench.app.windows_time import WindowsTimeSyncResult, resync_windows_time
from bybit_workbench.domain.types import AppMode, AppState
from bybit_workbench.exchange.bybit.connection import (
    ReadOnlyBybitConnection,
    create_mainnet_connection_tester,
    create_read_only_connection,
)
from bybit_workbench.exchange.bybit.errors import (
    BybitApiError,
    BybitClockSkewError,
    BybitErrorCategory,
)
from bybit_workbench.exchange.bybit.health import (
    BybitHealthSnapshot,
    ChannelHealth,
    ReconnectBackoff,
)
from bybit_workbench.exchange.bybit.models import MainnetConnectionTestReport
from bybit_workbench.exchange.bybit.streams import BybitStreamSnapshot
from bybit_workbench.exchange.bybit.synchronizer import ReadOnlySynchronizer
from bybit_workbench.execution.mainnet_state import MainnetReadinessContext
from bybit_workbench.persistence import TradingJournal
from bybit_workbench.ui.view_model import UserFacingError, WorkbenchViewModel

EventKind = Literal[
    "connection_test",
    "read",
    "candles",
    "executions",
    "closed_pnl",
    "clock",
    "stream",
    "health",
    "log",
    "error",
]


@dataclass(frozen=True, slots=True)
class ReadOnlyRuntimeEvent:
    kind: EventKind
    payload: Any


class ReadOnlyRuntime:
    """Own pybit, SQLite, and asyncio work on a background thread.

    The Qt thread only drains immutable copies from the event queue. The runtime
    deliberately exposes no order submission API.
    """

    def __init__(
        self,
        settings: AppSettings,
        state_machine: AppStateMachine,
        *,
        symbol: str = "BTCUSDT",
        interval: str = "60",
        credential_store: WindowsCredentialStore | None = None,
        connection_factory: Callable[
            [AppSettings, BybitCredentials, str], ReadOnlyBybitConnection
        ] = create_read_only_connection,
        connection_test_factory: Callable[[AppSettings, BybitCredentials], Any] = (
            create_mainnet_connection_tester
        ),
        journal_factory: Callable[[Any], TradingJournal] = TradingJournal,
        poll_seconds: float = 0.25,
        rest_refresh_seconds: float = 30.0,
        rest_retry_seconds: float = 5.0,
        reconnect_base_seconds: float = 2.0,
        reconnect_maximum_seconds: float = 30.0,
        reconnect_max_attempts: int = 4,
        clock_check_seconds: float = 15.0,
        clock_sync_trigger_ms: int = 500,
        max_clock_offset_ms: int = 750,
        clock_recovery_attempts: int = 3,
        clock_recovery_settle_seconds: float = 0.75,
        time_sync: Callable[[], WindowsTimeSyncResult] = resync_windows_time,
    ) -> None:
        if settings.mode is AppMode.REPLAY:
            raise ValueError("Replay does not use the Bybit read-only runtime")
        if not symbol.strip() or not interval.strip():
            raise ValueError("symbol and interval are required")
        if poll_seconds <= 0 or rest_refresh_seconds <= 0 or rest_retry_seconds <= 0:
            raise ValueError("poll and REST refresh intervals must be positive")
        if (
            reconnect_base_seconds <= 0
            or reconnect_maximum_seconds < reconnect_base_seconds
        ):
            raise ValueError("invalid reconnect delay bounds")
        if reconnect_max_attempts <= 0:
            raise ValueError("reconnect_max_attempts must be positive")
        if clock_check_seconds <= 0:
            raise ValueError("clock_check_seconds must be positive")
        if clock_sync_trigger_ms < 0 or max_clock_offset_ms <= 0:
            raise ValueError("clock offset limits must be non-negative/positive")
        if clock_sync_trigger_ms > max_clock_offset_ms:
            raise ValueError("clock sync trigger cannot exceed hard clock limit")
        if clock_recovery_attempts <= 0:
            raise ValueError("clock_recovery_attempts must be positive")
        if clock_recovery_settle_seconds <= 0:
            raise ValueError("clock_recovery_settle_seconds must be positive")
        self.settings = settings
        self.state_machine = state_machine
        self.symbol = symbol.strip().upper()
        self.interval = interval.strip()
        self.credential_store = credential_store or WindowsCredentialStore()
        self.connection_factory = connection_factory
        self.connection_test_factory = connection_test_factory
        self.journal_factory = journal_factory
        self.poll_seconds = poll_seconds
        self.rest_refresh_seconds = rest_refresh_seconds
        self.rest_retry_seconds = rest_retry_seconds
        self.reconnect_base_seconds = reconnect_base_seconds
        self.reconnect_maximum_seconds = reconnect_maximum_seconds
        self.reconnect_max_attempts = reconnect_max_attempts
        self.clock_check_seconds = clock_check_seconds
        self.clock_sync_trigger_ms = clock_sync_trigger_ms
        self.max_clock_offset_ms = max_clock_offset_ms
        self.clock_recovery_attempts = clock_recovery_attempts
        self.clock_recovery_settle_seconds = clock_recovery_settle_seconds
        self.time_sync = time_sync
        self._events: queue.SimpleQueue[ReadOnlyRuntimeEvent] = queue.SimpleQueue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._observation_lock = threading.Lock()
        self._latest_private_observation: tuple[BybitStreamSnapshot, BybitHealthSnapshot] | None = (
            None
        )
        self._latest_connection_report: MainnetConnectionTestReport | None = None
        self._latest_health: BybitHealthSnapshot | None = None
        self._reconciliation_complete = False
        self._desired_connected = threading.Event()
        self._market_switch_lock = threading.Lock()

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def reconfigure(self, settings: AppSettings) -> None:
        """Replace connection settings only while the read-only runtime is stopped."""

        settings.validate_startup()
        if settings.mode is not self.settings.mode:
            raise ValueError("runtime mode cannot be changed in-place")
        with self._lifecycle_lock:
            if self.running:
                raise RuntimeError("disconnect read-only before changing Mainnet endpoint")
            self.settings = settings
            self._desired_connected.clear()
            self._stop.set()
            self._reset_observation_state()
            self._discard_pending_events()

    def start(
        self,
        symbol: str | None = None,
        interval: str | None = None,
    ) -> None:
        with self._lifecycle_lock:
            if self.running:
                self._emit("log", f"{_now()} read-only connection already running")
                return
            if (symbol is None) != (interval is None):
                raise ValueError("symbol and interval must be supplied together")
            if symbol is not None and interval is not None:
                if not symbol.strip() or not interval.strip():
                    raise ValueError("symbol and interval are required")
                self.symbol = symbol.strip().upper()
                self.interval = interval.strip()
            self._discard_pending_events()
            self._desired_connected.set()
            self._stop.clear()
            self._move_to_syncing("operator requested read-only connection")
            self._emit("log", f"{_now()} read-only connection requested")
            self._thread = threading.Thread(
                target=self._run,
                name="bybit-read-only",
                daemon=True,
            )
            try:
                self._thread.start()
            except Exception:
                self._move_to_disconnected()
                raise

    def stop(self, timeout: float = 5.0) -> None:
        self.request_stop()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        if thread is not None and thread.is_alive():
            self._emit(
                "error",
                UserFacingError(
                    "Фоновое подключение не завершилось вовремя.",
                    "Новые входы остаются запрещены.",
                    "Закройте приложение и проверьте системный журнал.",
                ),
            )

    def request_stop(self) -> None:
        """Signal shutdown without blocking the caller (notably the Qt thread)."""

        self._desired_connected.clear()
        self._stop.set()
        self._emit("log", f"{_now()} read-only disconnect requested")
        if not self.running:
            self._move_to_disconnected()

    def switch_market(self, symbol: str, interval: str) -> None:
        """Restart read-only streams on a new symbol/timeframe without blocking Qt."""

        normalized_symbol = symbol.strip().upper()
        normalized_interval = interval.strip()
        if not normalized_symbol or not normalized_interval:
            raise ValueError("symbol and interval are required")
        if normalized_symbol == self.symbol and normalized_interval == self.interval:
            return
        self._desired_connected.set()
        self._emit(
            "log",
            f"{_now()} market switch requested: "
            f"{self.symbol}/{self.interval} -> {normalized_symbol}/{normalized_interval}",
        )
        self._stop.set()

        def worker() -> None:
            with self._market_switch_lock:
                thread = self._thread
                if thread is not None and thread is not threading.current_thread():
                    thread.join(7.0)
                if thread is not None and thread.is_alive():
                    self._emit(
                        "error",
                        UserFacingError(
                            "Не удалось быстро остановить старую market-data сессию.",
                            "Новая подписка не запущена; торговые действия запрещены.",
                            "Нажмите «Отключить», затем подключитесь повторно.",
                        ),
                    )
                    return
                if not self._desired_connected.is_set():
                    return
                self.start(normalized_symbol, normalized_interval)

        threading.Thread(
            target=worker,
            name="bybit-read-only-market-switch",
            daemon=True,
        ).start()

    def latest_private_observation(
        self,
    ) -> tuple[BybitStreamSnapshot, BybitHealthSnapshot] | None:
        with self._observation_lock:
            return self._latest_private_observation

    def latest_mainnet_context(self) -> MainnetReadinessContext | None:
        """Return immutable GET-test/reconciliation context without credentials."""

        with self._observation_lock:
            report = self._latest_connection_report
            health = self._latest_health
            if report is None or report.api_key is None or health is None:
                return None
            return MainnetReadinessContext(
                report.endpoint,
                report.api_key,
                health,
                self._reconciliation_complete,
            )

    def drain_into(self, model: WorkbenchViewModel) -> int:
        """Apply queued immutable updates; call this only from the UI thread."""

        count = 0
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            count += 1
            if event.kind == "connection_test":
                model.apply_connection_test(event.payload)
            elif event.kind == "read":
                model.apply_read_snapshot(event.payload)
            elif event.kind == "candles":
                for candle in event.payload:
                    model.apply_candle(candle)
            elif event.kind == "executions":
                model.merge_executions(event.payload)
            elif event.kind == "closed_pnl":
                model.set_closed_trades(event.payload)
            elif event.kind == "clock":
                model.set_clock_offset(event.payload)
            elif event.kind == "stream":
                model.apply_stream_snapshot(event.payload)
            elif event.kind == "health":
                model.apply_health(event.payload)
            elif event.kind == "log":
                model.append_system_log(event.payload)
            elif event.kind == "error":
                model.set_error(event.payload)
        return count

    def _run(self) -> None:
        credentials: BybitCredentials | None = None
        backoff = ReconnectBackoff(
            base_seconds=self.reconnect_base_seconds,
            maximum_seconds=self.reconnect_maximum_seconds,
            jitter_ratio=0.2,
            seed=17,
        )
        try:
            credential_name = (
                self.settings.credential_profile_name
                if self.settings.mode is AppMode.LIVE
                else None
            )
            credentials = (
                self.credential_store.load(self.settings.mode, name=credential_name)
                if credential_name is not None
                else self.credential_store.load(self.settings.mode)
            )
            if credentials is None:
                raise RuntimeError(f"Для профиля {self.settings.mode.value} не сохранены API-ключи")
            reconnect_attempt = 0
            while not self._stop.is_set():
                session_started = time.monotonic()
                try:
                    self._run_connected_session(credentials)
                    return
                except Exception as exc:
                    if self._stop.is_set():
                        return
                    # A session that was healthy for at least a minute starts a fresh
                    # backoff budget instead of accumulating reconnects over the whole day.
                    if time.monotonic() - session_started >= 60.0:
                        reconnect_attempt = 0
                        backoff.reset()
                    if _is_clock_skew_error(exc):
                        safe = _redacted_error(exc, credentials)
                        self._reset_observation_state()
                        self._move_to_degraded(
                            "clock skew detected; automatic resync/reconnect pending"
                        )
                        self._emit(
                            "log",
                            f"{_now()} clock skew forced a safe session restart: {safe}",
                        )
                        sync = self._attempt_windows_time_sync(
                            "automatic clock reconnect recovery"
                        )
                        # Clock recovery is intentionally persistent while the operator
                        # still wants the runtime connected. A temporary bad Windows/NTP
                        # correction must not permanently stop the screener. Trading
                        # remains fail-closed until a fresh session passes the clock gate.
                        delay = (
                            self.clock_recovery_settle_seconds
                            if sync.succeeded
                            else backoff.next_delay()
                        )
                        self._emit(
                            "log",
                            f"{_now()} automatic clock recovery will reconnect in "
                            f"{delay:.1f}s",
                        )
                        if self._stop.wait(delay):
                            return
                        self._move_to_syncing(
                            "automatic reconnect after Windows clock recovery"
                        )
                        continue
                    if not _is_retriable_transport_error(exc):
                        raise
                    reconnect_attempt += 1
                    safe = _redacted_error(exc, credentials)
                    self._reset_observation_state()
                    if reconnect_attempt >= self.reconnect_max_attempts:
                        raise ConnectionError(
                            "read-only reconnect budget exhausted after "
                            f"{reconnect_attempt} attempts; last error: {safe}"
                        ) from exc
                    self._move_to_degraded("transient transport failure; reconnect pending")
                    delay = backoff.next_delay()
                    self._emit(
                        "log",
                        f"{_now()} transient transport failure: {safe}; "
                        f"retry {reconnect_attempt}/{self.reconnect_max_attempts} "
                        f"in {delay:.1f}s",
                    )
                    if self._stop.wait(delay):
                        return
                    self._move_to_syncing("automatic read-only reconnect attempt")
        except Exception as exc:
            safe = _redacted_error(exc, credentials)
            self._emit(
                "error",
                UserFacingError(
                    safe,
                    "Read-only подключение остановлено; новые входы запрещены.",
                    "Проверьте режим, профиль ключей и сеть, затем подключитесь повторно.",
                ),
            )
            self._emit("log", f"{_now()} read-only connection failed: {safe}")
        finally:
            self._reset_observation_state()
            self._move_to_disconnected()
            self._emit("health", _disconnected_health())
            self._emit("clock", None)
            self._emit("log", f"{_now()} read-only connection stopped")

    def _run_connected_session(self, credentials: BybitCredentials) -> None:
        connection: ReadOnlyBybitConnection | None = None
        journal: TradingJournal | None = None
        try:
            self._emit("log", f"{_now()} read-only connection starting")
            if self.settings.mode is AppMode.LIVE:
                tester = self.connection_test_factory(self.settings, credentials)
                try:
                    report = asyncio.run(tester.run(self.symbol))
                except Exception as exc:
                    if not _is_clock_skew_error(exc):
                        raise
                    self._emit(
                        "log",
                        f"{_now()} clock skew detected during Mainnet GET-only test: "
                        f"{_redacted_error(exc, credentials)}",
                    )
                    sync = self._attempt_windows_time_sync("clock-skew recovery")
                    if not sync.succeeded:
                        raise
                    time.sleep(0.25)
                    report = asyncio.run(tester.run(self.symbol))
                with self._observation_lock:
                    self._latest_connection_report = report
                self._emit("connection_test", report)
                self._emit("clock", report.clock_offset_ms)
                if report.api_key is None:
                    self._emit(
                        "log",
                        f"{_now()} Mainnet core GET-only checks passed for {report.endpoint}; "
                        "API-key metadata unavailable, execution arming remains blocked",
                    )
                else:
                    self._emit(
                        "log",
                        f"{_now()} Mainnet GET-only connection test passed for {report.endpoint}",
                    )
            connection = self.connection_factory(self.settings, credentials, self.symbol)
            journal = self.journal_factory(self.settings.database_path)
            synchronizer = ReadOnlySynchronizer(
                connection.adapter,
                journal,
                self.state_machine,
                connection.health,
            )
            outcome = asyncio.run(
                synchronizer.synchronize(
                    self.symbol,
                    f"startup-{uuid.uuid4().hex}",
                )
            )
            with self._observation_lock:
                self._reconciliation_complete = outcome.verification.synchronized
            self._emit("read", outcome.snapshot)
            recent_executions = asyncio.run(_recent_executions(connection.adapter, self.symbol))
            if recent_executions:
                self._emit("executions", recent_executions)
            self._emit(
                "closed_pnl",
                asyncio.run(_recent_closed_pnl(connection.adapter, self.symbol)),
            )
            candles = asyncio.run(
                connection.adapter.historical_candles(
                    self.symbol,
                    self.interval,
                    limit=200,
                )
            )
            self._emit("candles", tuple(candles))
            connection.bridge.subscribe(self.symbol, self.interval)
            self._emit("log", f"{_now()} REST synchronized; streams subscribed")
            next_rest_refresh = time.monotonic() + self.rest_refresh_seconds
            next_clock_check = time.monotonic() + self.clock_check_seconds
            public_was_fresh: bool | None = None
            while not self._stop.wait(self.poll_seconds):
                now = datetime.now(UTC)
                public_connected, private_connected = connection.bridge.transport_status(now)
                if not public_connected or not private_connected:
                    transport_health = connection.health.snapshot(now)
                    failures: list[str] = []
                    if not public_connected:
                        failures.append(
                            transport_health.public.last_error or "public WebSocket disconnected"
                        )
                    if not private_connected:
                        failures.append(
                            transport_health.private.last_error or "private WebSocket disconnected"
                        )
                    raise ConnectionError("; ".join(failures))
                if self.settings.mode is AppMode.LIVE and time.monotonic() >= next_clock_check:
                    offset = asyncio.run(self._measure_clock_offset(connection.adapter))
                    self._emit("clock", offset)
                    if abs(offset) >= self.clock_sync_trigger_ms:
                        offset = self._recover_clock_offset(
                            connection.adapter,
                            offset,
                            reason="clock watchdog",
                        )
                    if abs(offset) > self.max_clock_offset_ms:
                        raise BybitClockSkewError(offset, self.max_clock_offset_ms)
                    next_clock_check = time.monotonic() + self.clock_check_seconds
                if time.monotonic() >= next_rest_refresh:
                    try:
                        if self.state_machine.state in {
                            AppState.READY,
                            AppState.PAUSED,
                            AppState.DEGRADED,
                        }:
                            refreshed = asyncio.run(
                                synchronizer.synchronize(
                                    self.symbol,
                                    f"periodic-{uuid.uuid4().hex}",
                                    update_state=False,
                                )
                            )
                            with self._observation_lock:
                                self._reconciliation_complete = (
                                    refreshed.verification.synchronized
                                )
                            snapshot = refreshed.snapshot
                        else:
                            snapshot = asyncio.run(connection.adapter.read_snapshot(self.symbol))
                            connection.health.mark_message("rest", now)
                        self._emit("read", snapshot)
                        recent_executions = asyncio.run(
                            _recent_executions(connection.adapter, self.symbol)
                        )
                        if recent_executions:
                            self._emit("executions", recent_executions)
                        self._emit(
                            "closed_pnl",
                            asyncio.run(_recent_closed_pnl(connection.adapter, self.symbol)),
                        )
                        self._emit("log", f"{_now()} periodic REST reconciliation complete")
                    except Exception as exc:
                        if _is_clock_skew_error(exc):
                            self._emit(
                                "log",
                                f"{_now()} authenticated REST detected clock skew: "
                                f"{_redacted_error(exc, credentials)}",
                            )
                            sync = self._attempt_windows_time_sync("REST clock-skew recovery")
                            if sync.succeeded:
                                next_rest_refresh = time.monotonic()
                                next_clock_check = time.monotonic()
                                continue
                            raise
                        # A single GET timeout is not evidence that the active Bybit
                        # session is dead. Preserve the last successful REST snapshot
                        # until its normal freshness deadline and retry sooner.
                        connection.health.mark_transient_error("rest", str(exc))
                        rest_health = connection.health.snapshot(now).rest
                        if (
                            not rest_health.fresh
                            and self.state_machine.state
                            in {AppState.READY, AppState.ARMED, AppState.RUNNING}
                        ):
                            self.state_machine.transition(
                                AppState.DEGRADED,
                                "REST snapshot became stale after refresh failure",
                            )
                        self._emit(
                            "log",
                            f"{_now()} periodic REST reconciliation failed; "
                            f"keeping last fresh snapshot and retrying in "
                            f"{self.rest_retry_seconds:.1f}s: "
                            f"{_redacted_error(exc, credentials)}",
                        )
                        next_rest_refresh = time.monotonic() + self.rest_retry_seconds
                    else:
                        next_rest_refresh = time.monotonic() + self.rest_refresh_seconds
                stream_snapshot = connection.processor.snapshot()
                health_snapshot = connection.health.snapshot(now)
                # Market-data freshness and transport liveness are deliberately
                # separate concerns. A live socket must not be torn down merely
                # because a ticker/kline update is late. Risk Gate still sees the
                # stale timestamp and remains fail-closed for new entries.
                if public_was_fresh is None:
                    public_was_fresh = health_snapshot.public.fresh
                elif health_snapshot.public.fresh != public_was_fresh:
                    public_was_fresh = health_snapshot.public.fresh
                    if public_was_fresh:
                        self._emit(
                            "log",
                            f"{_now()} public market data fresh again; session kept alive",
                        )
                    else:
                        self._emit(
                            "log",
                            f"{_now()} public market data stale; WebSocket transport "
                            "remains connected and Risk Gate blocks new entries",
                        )
                with self._observation_lock:
                    self._latest_private_observation = (
                        stream_snapshot,
                        health_snapshot,
                    )
                    self._latest_health = health_snapshot
                self._emit("stream", stream_snapshot)
                self._emit("health", health_snapshot)
        finally:
            if connection is not None:
                connection.close()
            if journal is not None:
                journal.close()

    def _recover_clock_offset(
        self,
        adapter: Any,
        initial_offset_ms: int,
        *,
        reason: str,
    ) -> int:
        """Try repeated W32Time corrections before giving up the active session.

        Windows Time may slew a sub-second error instead of correcting it fully in
        one operation. Re-measuring after each resync avoids tearing down a healthy
        market-data session when a second correction can bring the clock back into
        the green zone.
        """

        offset = initial_offset_ms
        for attempt in range(1, self.clock_recovery_attempts + 1):
            if abs(offset) < self.clock_sync_trigger_ms:
                break
            self._emit(
                "log",
                f"{_now()} {reason} observed offset={offset} ms; "
                f"W32Time recovery {attempt}/{self.clock_recovery_attempts}",
            )
            sync = self._attempt_windows_time_sync(reason)
            if not sync.succeeded:
                continue
            if self._stop.wait(self.clock_recovery_settle_seconds):
                return offset
            offset = asyncio.run(self._measure_clock_offset(adapter))
            self._emit("clock", offset)
        return offset

    async def _measure_clock_offset(self, adapter: Any) -> int:
        local_before = datetime.now(UTC)
        server_time = await adapter.server_time()
        local_after = datetime.now(UTC)
        local_midpoint = local_before + (local_after - local_before) / 2
        return int((server_time - local_midpoint).total_seconds() * 1_000)

    def _attempt_windows_time_sync(self, reason: str) -> WindowsTimeSyncResult:
        result = self.time_sync()
        if not result.attempted:
            self._emit("log", f"{_now()} {result.detail}")
            return result
        status = "succeeded" if result.succeeded else "failed"
        self._emit(
            "log",
            f"{_now()} Windows clock resync {status} ({reason}): {result.detail}",
        )
        return result

    def _reset_observation_state(self) -> None:
        with self._observation_lock:
            self._latest_private_observation = None
            self._latest_connection_report = None
            self._latest_health = None
            self._reconciliation_complete = False

    def _move_to_degraded(self, reason: str) -> None:
        if self.state_machine.state is AppState.DEGRADED:
            return
        with suppress(InvalidStateTransition):
            self.state_machine.transition(AppState.DEGRADED, reason)

    def _move_to_syncing(self, reason: str) -> None:
        if self.state_machine.state is AppState.SYNCING:
            return
        if self.state_machine.state is AppState.DISCONNECTED:
            self.state_machine.transition(AppState.SYNCING, reason)
            return
        with suppress(InvalidStateTransition):
            self.state_machine.transition(AppState.SYNCING, reason)

    def _move_to_disconnected(self) -> None:
        if self.state_machine.state is AppState.DISCONNECTED:
            return
        with suppress(InvalidStateTransition):
            self.state_machine.transition(
                AppState.DISCONNECTED,
                "Bybit read-only runtime stopped",
            )

    def _emit(self, kind: EventKind, payload: Any) -> None:
        self._events.put(ReadOnlyRuntimeEvent(kind, payload))

    def _discard_pending_events(self) -> None:
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                return


def _disconnected_health() -> BybitHealthSnapshot:
    channel = ChannelHealth(False, False, None, "Read-only disconnected")
    return BybitHealthSnapshot(channel, channel, channel)


async def _recent_executions(adapter: Any, symbol: str) -> tuple[Any, ...]:
    loader = getattr(adapter, "recent_executions", None)
    if not callable(loader):
        return ()
    result = await loader(symbol, limit=50)
    return tuple(result)


async def _recent_closed_pnl(adapter: Any, symbol: str) -> tuple[Any, ...]:
    loader = getattr(adapter, "recent_closed_pnl", None)
    if not callable(loader):
        return ()
    result = await loader(symbol, limit=50)
    return tuple(result)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _redacted_error(
    error: Exception,
    credentials: BybitCredentials | None,
) -> str:
    return redact_text(error, credentials)


def _is_retriable_transport_error(error: Exception) -> bool:
    """Classify network/WebSocket failures that are safe to reconnect from.

    Authentication, permission, protocol, and validation failures intentionally do
    not match this predicate and still stop the runtime fail-closed.
    """

    names = {
        "WebSocketTimeoutException",
        "WebSocketConnectionClosedException",
        "ConnectionResetError",
        "ConnectionAbortedError",
        "ConnectionRefusedError",
        "TimeoutError",
        "ReadTimeout",
        "ConnectTimeout",
        "SSLError",
        "ConnectionError",
    }
    if type(error).__name__ in names:
        return True
    text = str(error).lower()
    return "websocket" in text and any(
        marker in text
        for marker in (
            "connection failed",
            "connection reset",
            "connection closed",
            "transport disconnected",
            "timed out",
            "timeout",
        )
    )


def _is_clock_skew_error(error: Exception) -> bool:
    if isinstance(error, BybitClockSkewError):
        return True
    return isinstance(error, BybitApiError) and error.category is BybitErrorCategory.CLOCK_SKEW
