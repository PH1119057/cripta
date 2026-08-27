import time
import unittest

from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.credentials import BybitCredentials
from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.app.windows_time import WindowsTimeSyncResult
from bybit_workbench.domain.types import AppMode, AppState
from bybit_workbench.exchange.bybit.errors import BybitClockSkewError
from bybit_workbench.ui.read_only_runtime import (
    ReadOnlyRuntime,
    _disconnected_health,
    _is_retriable_transport_error,
)
from bybit_workbench.ui.view_model import WorkbenchViewModel


class StubCredentialStore:
    def __init__(self, credentials):
        self.credentials = credentials

    def load(self, profile, name=None):  # type: ignore[no-untyped-def]
        del profile, name
        return self.credentials


class ReadOnlyRuntimeTests(unittest.TestCase):
    def test_disconnected_health_clears_stale_green_channels(self) -> None:
        health = _disconnected_health()
        self.assertFalse(health.public.connected)
        self.assertFalse(health.public.fresh)
        self.assertFalse(health.private.connected)
        self.assertFalse(health.rest.connected)

    def test_reconfigure_switches_stopped_runtime_endpoint(self) -> None:
        runtime = ReadOnlyRuntime(
            AppSettings(mode=AppMode.LIVE, rest_url_override="https://api.bybit.kz"),
            AppStateMachine(),
            credential_store=StubCredentialStore(None),
        )
        runtime.reconfigure(
            AppSettings(mode=AppMode.LIVE, rest_url_override="https://api.bybit.com")
        )
        self.assertEqual(
            runtime.settings.endpoint_profile.rest_url,
            "https://api.bybit.com",
        )
        self.assertEqual(
            runtime.settings.endpoint_profile.public_ws_url,
            "wss://stream.bybit.com/v5/public/linear",
        )

    def test_websocket_retry_classifier_is_fail_closed_for_auth_errors(self) -> None:
        class WebSocketTimeoutException(Exception):
            pass

        self.assertTrue(_is_retriable_transport_error(WebSocketTimeoutException("timeout")))

        class ReadTimeout(Exception):
            pass

        self.assertTrue(_is_retriable_transport_error(ReadTimeout("REST read timed out")))
        self.assertTrue(_is_retriable_transport_error(ConnectionError("socket disconnected")))
        self.assertFalse(_is_retriable_transport_error(PermissionError("API key rejected")))
        self.assertFalse(_is_retriable_transport_error(ValueError("invalid symbol")))

    def test_missing_credentials_becomes_actionable_ui_error(self) -> None:
        runtime = ReadOnlyRuntime(
            AppSettings(mode=AppMode.TESTNET),
            AppStateMachine(),
            credential_store=StubCredentialStore(None),
        )
        model = WorkbenchViewModel(AppMode.TESTNET)

        runtime.start()
        self._wait_until_stopped(runtime)
        runtime.drain_into(model)

        self.assertIsNotNone(model.state.error)
        self.assertIn("не сохранены API-ключи", model.state.error.what_happened)
        self.assertIn("новые входы запрещены", model.state.error.automatic_action)

    def test_connection_exception_redacts_key_and_secret(self) -> None:
        credentials = BybitCredentials(
            AppMode.TESTNET,
            "public-test-key",
            "super-private-secret",
        )

        def fail_connection(settings, supplied, symbol):
            raise RuntimeError(f"rejected {supplied.api_key} / {supplied.api_secret}")

        runtime = ReadOnlyRuntime(
            AppSettings(mode=AppMode.TESTNET),
            AppStateMachine(),
            credential_store=StubCredentialStore(credentials),
            connection_factory=fail_connection,
        )
        model = WorkbenchViewModel(AppMode.TESTNET)

        runtime.start()
        self._wait_until_stopped(runtime)
        runtime.drain_into(model)

        error_text = model.state.error.text
        self.assertNotIn(credentials.api_key, error_text)
        self.assertNotIn(credentials.api_secret, error_text)
        self.assertIn("***REDACTED***", error_text)


    def test_start_moves_to_syncing_before_background_connection_work(self) -> None:
        class SlowMissingStore:
            def load(self, profile):
                time.sleep(0.15)
                return None

        machine = AppStateMachine()
        runtime = ReadOnlyRuntime(
            AppSettings(mode=AppMode.TESTNET),
            machine,
            credential_store=SlowMissingStore(),
        )

        runtime.start()
        self.assertEqual(machine.state, AppState.SYNCING)
        runtime.request_stop()
        self._wait_until_stopped(runtime)
        self.assertEqual(machine.state, AppState.DISCONNECTED)

    def test_transient_connection_failure_stays_degraded_while_retry_is_pending(self) -> None:
        credentials = BybitCredentials(
            AppMode.TESTNET,
            "public-test-key",
            "super-private-secret",
        )

        def fail_connection(settings, supplied, symbol):
            raise ConnectionError("Bybit WebSocket connection failed")

        machine = AppStateMachine()
        runtime = ReadOnlyRuntime(
            AppSettings(mode=AppMode.TESTNET),
            machine,
            credential_store=StubCredentialStore(credentials),
            connection_factory=fail_connection,
            reconnect_base_seconds=0.5,
            reconnect_maximum_seconds=0.5,
        )

        runtime.start()
        deadline = time.monotonic() + 1.0
        while machine.state is not AppState.DEGRADED and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(machine.state, AppState.DEGRADED)
        self.assertTrue(runtime.running)
        runtime.request_stop()
        self._wait_until_stopped(runtime)
        self.assertEqual(machine.state, AppState.DISCONNECTED)


    def test_switch_market_restarts_with_new_symbol_and_interval(self) -> None:
        runtime = ReadOnlyRuntime(
            AppSettings(mode=AppMode.TESTNET),
            AppStateMachine(),
            credential_store=StubCredentialStore(None),
            symbol="BTCUSDT",
            interval="1",
        )
        started: list[tuple[str | None, str | None]] = []

        def fake_start(symbol=None, interval=None):
            started.append((symbol, interval))

        runtime.start = fake_start  # type: ignore[method-assign]
        runtime.switch_market("uniusdt", "60")
        deadline = time.monotonic() + 1.0
        while not started and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertEqual(started, [("UNIUSDT", "60")])

    def test_live_clock_watchdog_defaults_to_15_seconds(self) -> None:
        runtime = ReadOnlyRuntime(
            AppSettings(mode=AppMode.LIVE),
            AppStateMachine(),
            credential_store=StubCredentialStore(None),
        )

        self.assertEqual(runtime.clock_check_seconds, 15.0)
        self.assertEqual(runtime.clock_sync_trigger_ms, 500)
        self.assertEqual(runtime.max_clock_offset_ms, 750)
        self.assertEqual(runtime.clock_recovery_attempts, 3)

    def test_clock_skew_retries_persist_without_consuming_transport_budget(self) -> None:
        credentials = BybitCredentials(
            AppMode.LIVE,
            "public-test-key",
            "super-private-secret",
        )
        machine = AppStateMachine()
        runtime = ReadOnlyRuntime(
            AppSettings(mode=AppMode.LIVE),
            machine,
            credential_store=StubCredentialStore(credentials),
            reconnect_base_seconds=0.01,
            reconnect_maximum_seconds=0.01,
            reconnect_max_attempts=1,
            clock_recovery_settle_seconds=0.01,
            time_sync=lambda: WindowsTimeSyncResult(
                attempted=True,
                succeeded=True,
                command=("w32tm", "/resync"),
                detail="stubbed clock resync",
            ),
        )
        attempts = 0

        def clock_skew_session(_credentials):  # type: ignore[no-untyped-def]
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise BybitClockSkewError(900, 750)
            runtime.request_stop()

        runtime._run_connected_session = clock_skew_session  # type: ignore[method-assign]
        runtime.start()
        self._wait_until_stopped(runtime)

        self.assertEqual(attempts, 3)
        self.assertEqual(machine.state, AppState.DISCONNECTED)

    def test_reconnect_budget_stops_instead_of_retrying_forever(self) -> None:
        credentials = BybitCredentials(
            AppMode.TESTNET,
            "public-test-key",
            "super-private-secret",
        )
        attempts = 0

        def fail_connection(settings, supplied, symbol):
            nonlocal attempts
            attempts += 1
            raise ConnectionError("public WebSocket handshake failed")

        machine = AppStateMachine()
        runtime = ReadOnlyRuntime(
            AppSettings(mode=AppMode.TESTNET),
            machine,
            credential_store=StubCredentialStore(credentials),
            connection_factory=fail_connection,
            reconnect_base_seconds=0.01,
            reconnect_maximum_seconds=0.01,
            reconnect_max_attempts=3,
        )
        model = WorkbenchViewModel(AppMode.TESTNET)

        runtime.start()
        self._wait_until_stopped(runtime)
        runtime.drain_into(model)

        self.assertEqual(attempts, 3)
        self.assertEqual(machine.state, AppState.DISCONNECTED)
        self.assertIsNotNone(model.state.error)
        self.assertIn("reconnect budget exhausted", model.state.error.what_happened)
        self.assertIn("public WebSocket handshake failed", model.state.error.what_happened)

    @staticmethod
    def _wait_until_stopped(runtime: ReadOnlyRuntime) -> None:
        deadline = time.monotonic() + 2
        while runtime.running and time.monotonic() < deadline:
            time.sleep(0.01)
        if runtime.running:
            runtime.stop()


if __name__ == "__main__":
    unittest.main()
