import asyncio
import unittest
from types import SimpleNamespace
from typing import Any

from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.credentials import BybitCredentials, WindowsCredentialStore
from bybit_workbench.domain.types import AppMode
from bybit_workbench.exchange.bybit import (
    PybitConstructors,
    create_mainnet_mutation_gateway,
    create_read_only_connection,
    create_testnet_execution_adapter,
)
from bybit_workbench.exchange.bybit.connection import (
    _ensure_pybit_regional_public_ws_template,
    create_mainnet_execution_connection,
)
from bybit_workbench.execution.mainnet_safety import (
    ExecutionArmingController,
    MainnetMutation,
    MemoryIdempotencyStore,
    MutationBlocked,
    MutationKind,
)


class MemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def delete_password(self, service_name: str, username: str) -> None:
        self.values.pop((service_name, username), None)


class CredentialStoreTests(unittest.TestCase):
    def test_profiles_are_isolated_and_secrets_are_not_in_repr(self) -> None:
        backend = MemoryKeyring()
        store = WindowsCredentialStore(backend)
        testnet = BybitCredentials(AppMode.TESTNET, "testnet-key-1234", "testnet-secret")
        demo = BybitCredentials(AppMode.DEMO, "demo-key-5678", "demo-secret")
        store.save(testnet)
        store.save(demo)
        self.assertEqual(store.load(AppMode.TESTNET), testnet)
        self.assertEqual(store.load(AppMode.DEMO), demo)
        self.assertNotIn(testnet.api_key, repr(testnet))
        self.assertNotIn(testnet.api_secret, repr(testnet))
        self.assertEqual(testnet.masked_key, "test…1234")
        self.assertEqual(len(backend.values), 2)

    def test_delete_and_replay_rejection(self) -> None:
        store = WindowsCredentialStore(MemoryKeyring())
        credentials = BybitCredentials(AppMode.TESTNET, "key", "secret")
        store.save(credentials)
        store.delete(AppMode.TESTNET)
        self.assertIsNone(store.load(AppMode.TESTNET))
        with self.assertRaises(ValueError):
            store.load(AppMode.REPLAY)
        with self.assertRaises(ValueError):
            BybitCredentials(AppMode.REPLAY, "key", "secret")

    def test_corrupted_profile_fails_without_exposing_payload(self) -> None:
        backend = MemoryKeyring()
        backend.values[("BybitStrategyWorkbench/testnet", "credentials")] = "not-json"
        store = WindowsCredentialStore(backend)
        with self.assertRaisesRegex(RuntimeError, "profile testnet is corrupted"):
            store.load(AppMode.TESTNET)


class FakeHttp:
    pass


class FakeWebSocket:
    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs
        self.subscriptions: list[str] = []

    def __getattr__(self, name: str) -> Any:
        def record(**kwargs: Any) -> None:
            self.subscriptions.append(name)

        return record

    def exit(self) -> None:
        pass


class ConstructorRecorder:
    def __init__(self) -> None:
        self.http_calls: list[dict[str, Any]] = []
        self.ws_calls: list[dict[str, Any]] = []

    def http(self, **kwargs: Any) -> FakeHttp:
        self.http_calls.append(kwargs)
        return FakeHttp()

    def websocket(self, **kwargs: Any) -> FakeWebSocket:
        self.ws_calls.append(kwargs)
        return FakeWebSocket(kwargs)


class ConnectionFactoryTests(unittest.TestCase):
    def test_mainnet_execution_uses_separate_retrying_read_session(self) -> None:
        recorder = ConstructorRecorder()
        settings = AppSettings(
            mode=AppMode.LIVE,
            allow_live_trading=True,
            rest_url_override="https://api.bybit.kz",
        )
        credentials = BybitCredentials(
            AppMode.LIVE,
            "mainnet-key",
            "mainnet-secret",
            "BotW-Mainnet",
        )

        connection = create_mainnet_execution_connection(
            settings,
            credentials,
            ExecutionArmingController(),
            MemoryIdempotencyStore(),
            lambda: None,
            constructors=PybitConstructors(recorder.http, recorder.websocket),
        )

        self.assertIsNotNone(connection)
        self.assertEqual(len(recorder.http_calls), 2)
        write_call, read_call = recorder.http_calls
        self.assertEqual(write_call["timeout"], 20)
        self.assertEqual(write_call["recv_window"], 10_000)
        self.assertFalse(write_call["force_retry"])
        self.assertEqual(read_call["timeout"], 20)
        self.assertTrue(read_call["force_retry"])
        self.assertEqual(read_call["max_retries"], 2)
        self.assertEqual(read_call["retry_delay"], 1)
        self.assertEqual(write_call["domain"], "bybit")
        self.assertEqual(write_call["tld"], "kz")
        self.assertEqual(read_call["domain"], "bybit")
        self.assertEqual(read_call["tld"], "kz")

    def test_mainnet_gateway_factory_is_transport_blocked_in_shadow(self) -> None:
        recorder = ConstructorRecorder()
        gateway = create_mainnet_mutation_gateway(
            AppSettings(mode=AppMode.LIVE),
            BybitCredentials(
                AppMode.LIVE,
                "mainnet-key",
                "mainnet-secret",
                "BotW-Mainnet",
            ),
            ExecutionArmingController(),
            MemoryIdempotencyStore(),
            constructors=PybitConstructors(recorder.http, recorder.websocket),
        )
        mutation = MainnetMutation(
            "/v5/order/create",
            {"category": "linear", "symbol": "BTCUSDT", "stopLoss": "49000"},
            MutationKind.ENTRY,
            "factory-shadow",
        )
        with self.assertRaisesRegex(MutationBlocked, "SHADOW"):
            asyncio.run(gateway.submit(mutation))
        self.assertEqual(len(recorder.http_calls), 1)
        write_call = recorder.http_calls[0]
        self.assertEqual(write_call["timeout"], 20)
        self.assertEqual(write_call["recv_window"], 10_000)
        self.assertFalse(write_call["force_retry"])

    def create(self, mode: AppMode) -> tuple[ConstructorRecorder, Any]:
        recorder = ConstructorRecorder()
        settings = AppSettings(
            mode=mode,
            allow_live_trading=mode is AppMode.LIVE,
        )
        credentials = BybitCredentials(mode, f"{mode.value}-key", f"{mode.value}-secret")
        connection = create_read_only_connection(
            settings,
            credentials,
            "BTCUSDT",
            constructors=PybitConstructors(recorder.http, recorder.websocket),
        )
        return recorder, connection

    def test_testnet_flags_and_public_session_has_no_keys(self) -> None:
        recorder, connection = self.create(AppMode.TESTNET)
        self.assertEqual(
            recorder.http_calls,
            [
                {
                    "testnet": True,
                    "demo": False,
                    "api_key": "testnet-key",
                    "api_secret": "testnet-secret",
                    "timeout": 20,
                    "force_retry": True,
                    "max_retries": 2,
                    "retry_delay": 1,
                }
            ],
        )
        public, private = recorder.ws_calls
        self.assertEqual(
            public,
            {
                "testnet": True,
                "demo": False,
                "channel_type": "linear",
                "retries": 3,
                "restart_on_error": False,
            },
        )
        self.assertNotIn("api_key", public)
        self.assertTrue(private["testnet"])
        self.assertFalse(private["demo"])
        self.assertEqual(private["retries"], 3)
        self.assertFalse(private["restart_on_error"])
        connection.close()

    def test_demo_uses_mainnet_public_and_demo_private(self) -> None:
        recorder, connection = self.create(AppMode.DEMO)
        self.assertFalse(recorder.http_calls[0]["testnet"])
        self.assertTrue(recorder.http_calls[0]["demo"])
        public, private = recorder.ws_calls
        self.assertFalse(public["testnet"])
        self.assertFalse(public["demo"])
        self.assertEqual(public["retries"], 3)
        self.assertFalse(public["restart_on_error"])
        self.assertTrue(private["demo"])
        connection.close()

    def test_pybit_public_ws_template_honours_regional_tld(self) -> None:
        module = SimpleNamespace(
            PUBLIC_WSS="wss://{SUBDOMAIN}.{DOMAIN}.com/v5/public/{CHANNEL_TYPE}"
        )
        _ensure_pybit_regional_public_ws_template(module)
        self.assertEqual(
            module.PUBLIC_WSS,
            "wss://{SUBDOMAIN}.{DOMAIN}.{TLD}/v5/public/{CHANNEL_TYPE}",
        )

    def test_pybit_public_ws_template_rejects_unknown_upstream_shape(self) -> None:
        module = SimpleNamespace(PUBLIC_WSS="wss://unexpected.example/v5/public/{CHANNEL_TYPE}")
        with self.assertRaisesRegex(RuntimeError, "unsupported pybit public WebSocket template"):
            _ensure_pybit_regional_public_ws_template(module)

    def test_kazakhstan_mainnet_uses_explicit_regional_domain_without_fallback(self) -> None:
        recorder = ConstructorRecorder()
        settings = AppSettings(
            mode=AppMode.LIVE,
            rest_url_override="https://api.bybit.kz",
        )
        credentials = BybitCredentials(
            AppMode.LIVE,
            "mainnet-key",
            "mainnet-secret",
            "BotW-Mainnet",
        )
        connection = create_read_only_connection(
            settings,
            credentials,
            "BTCUSDT",
            constructors=PybitConstructors(recorder.http, recorder.websocket),
        )
        self.assertEqual(recorder.http_calls[0]["domain"], "bybit")
        self.assertEqual(recorder.http_calls[0]["tld"], "kz")
        self.assertEqual(recorder.ws_calls[0]["tld"], "kz")
        self.assertEqual(recorder.ws_calls[1]["tld"], "kz")
        connection.close()

    def test_profile_mismatch_and_replay_are_rejected(self) -> None:
        constructors = PybitConstructors(lambda **kwargs: None, lambda **kwargs: None)
        with self.assertRaises(ValueError):
            create_read_only_connection(
                AppSettings(mode=AppMode.TESTNET),
                BybitCredentials(AppMode.DEMO, "key", "secret"),
                "BTCUSDT",
                constructors=constructors,
            )

    def test_testnet_execution_factory_requires_external_switch(self) -> None:
        recorder = ConstructorRecorder()
        constructors = PybitConstructors(recorder.http, recorder.websocket)
        credentials = BybitCredentials(AppMode.TESTNET, "key", "secret")
        with self.assertRaises(PermissionError):
            create_testnet_execution_adapter(
                AppSettings(mode=AppMode.TESTNET),
                credentials,
                constructors=constructors,
            )
        adapter = create_testnet_execution_adapter(
            AppSettings(
                mode=AppMode.TESTNET,
                enable_testnet_execution=True,
            ),
            credentials,
            constructors=constructors,
        )
        self.assertIsNotNone(adapter)
        self.assertEqual(recorder.http_calls[-1]["testnet"], True)
        with self.assertRaises(PermissionError):
            create_testnet_execution_adapter(
                AppSettings(
                    mode=AppMode.LIVE,
                    allow_live_trading=True,
                    enable_testnet_execution=True,
                ),
                BybitCredentials(AppMode.LIVE, "key", "secret"),
                constructors=constructors,
            )
        with self.assertRaises(ValueError):
            create_read_only_connection(
                AppSettings(mode=AppMode.REPLAY),
                BybitCredentials(AppMode.TESTNET, "key", "secret"),
                "BTCUSDT",
                constructors=constructors,
            )


if __name__ == "__main__":
    unittest.main()
