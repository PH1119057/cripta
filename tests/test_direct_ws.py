import json
import time
import unittest
from collections import deque
from datetime import UTC, datetime
from typing import Any

import websocket

from bybit_workbench.app.credentials import BybitCredentials
from bybit_workbench.domain.types import AppMode
from bybit_workbench.exchange.bybit.direct_ws import DirectBybitWebSocketBridge
from bybit_workbench.exchange.bybit.health import HealthMonitor
from bybit_workbench.exchange.bybit.streams import BybitStreamProcessor


class FakeSocket:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = deque(json.dumps(item) for item in messages)
        self.sent: list[dict[str, Any]] = []
        self.connected = True

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    def recv(self) -> str:
        if self.messages:
            return self.messages.popleft()
        time.sleep(0.01)
        raise websocket.WebSocketTimeoutException("idle")

    def close(self) -> None:
        self.connected = False


class DirectWebSocketTests(unittest.TestCase):
    def test_regional_bridge_uses_direct_socket_and_accepts_data_before_subscribe_ack(self) -> None:
        public = FakeSocket(
            [
                {
                    "topic": "tickers.UNIUSDT",
                    "type": "snapshot",
                    "ts": 1786710543504,
                    "data": {
                        "symbol": "UNIUSDT",
                        "lastPrice": "3.242",
                        "markPrice": "3.241",
                    },
                },
                {
                    "success": True,
                    "ret_msg": "",
                    "req_id": "botw-public",
                    "op": "subscribe",
                },
            ]
        )
        private = FakeSocket(
            [
                {
                    "success": True,
                    "ret_msg": "",
                    "req_id": "botw-auth",
                    "op": "auth",
                },
                {
                    "success": True,
                    "ret_msg": "",
                    "req_id": "botw-private",
                    "op": "subscribe",
                },
            ]
        )

        def factory(url: str, **kwargs: Any) -> FakeSocket:
            return private if url.endswith("/private") else public

        health = HealthMonitor()
        processor = BybitStreamProcessor("UNIUSDT", health)
        bridge = DirectBybitWebSocketBridge(
            "wss://stream.bybit.kz/v5/public/linear",
            "wss://stream.bybit.kz/v5/private",
            BybitCredentials(AppMode.LIVE, "key", "secret", "BotW-Mainnet"),
            processor,
            health,
            socket_factory=factory,
            startup_timeout_seconds=1,
            heartbeat_seconds=60,
        )
        try:
            bridge.subscribe("UNIUSDT", "60")
            deadline = time.monotonic() + 0.5
            while processor.snapshot().ticker is None and time.monotonic() < deadline:
                time.sleep(0.01)
            ticker = processor.snapshot().ticker
            self.assertIsNotNone(ticker)
            assert ticker is not None
            self.assertEqual(str(ticker.last_price), "3.242")
            public_sub = next(item for item in public.sent if item.get("op") == "subscribe")
            self.assertEqual(
                public_sub["args"],
                ["kline.60.UNIUSDT", "tickers.UNIUSDT"],
            )
            auth = next(item for item in private.sent if item.get("op") == "auth")
            self.assertEqual(auth["args"][0], "key")
            self.assertNotEqual(auth["args"][2], "secret")
            private_sub = next(item for item in private.sent if item.get("op") == "subscribe")
            self.assertEqual(private_sub["args"], ["order", "execution", "position", "wallet"])
            public_ok, private_ok = bridge.transport_status(datetime.now(UTC))
            self.assertTrue(public_ok)
            self.assertTrue(private_ok)
        finally:
            bridge.close()


    def test_transport_stays_alive_when_market_data_freshness_expires(self) -> None:
        public = FakeSocket(
            [
                {
                    "topic": "tickers.UNIUSDT",
                    "type": "snapshot",
                    "ts": 1786710543504,
                    "data": {
                        "symbol": "UNIUSDT",
                        "lastPrice": "3.242",
                        "markPrice": "3.241",
                    },
                }
            ]
        )
        private = FakeSocket(
            [
                {
                    "success": True,
                    "ret_msg": "",
                    "req_id": "botw-auth",
                    "op": "auth",
                }
            ]
        )

        def factory(url: str, **kwargs: Any) -> FakeSocket:
            return private if url.endswith("/private") else public

        health = HealthMonitor(max_public_age_seconds=0.01)
        processor = BybitStreamProcessor("UNIUSDT", health)
        bridge = DirectBybitWebSocketBridge(
            "wss://stream.bybit.kz/v5/public/linear",
            "wss://stream.bybit.kz/v5/private",
            BybitCredentials(AppMode.LIVE, "key", "secret", "BotW-Mainnet"),
            processor,
            health,
            socket_factory=factory,
            startup_timeout_seconds=1,
            heartbeat_seconds=60,
        )
        try:
            bridge.subscribe("UNIUSDT", "60")
            time.sleep(0.03)
            snapshot = health.snapshot(datetime.now(UTC))
            self.assertFalse(snapshot.public.fresh)
            public_ok, private_ok = bridge.transport_status(datetime.now(UTC))
            self.assertTrue(public_ok)
            self.assertTrue(private_ok)
        finally:
            bridge.close()

    def test_public_protocol_anomaly_does_not_restart_healthy_socket(self) -> None:
        public = FakeSocket(
            [
                {
                    "topic": "kline.60.UNIUSDT",
                    "type": "snapshot",
                    "ts": 1786710543504,
                    "data": "unexpected-shape",
                },
                {
                    "topic": "tickers.UNIUSDT",
                    "type": "snapshot",
                    "ts": 1786710543505,
                    "data": {
                        "symbol": "UNIUSDT",
                        "lastPrice": "3.242",
                        "markPrice": "3.241",
                    },
                },
            ]
        )
        private = FakeSocket(
            [
                {
                    "success": True,
                    "ret_msg": "",
                    "req_id": "botw-auth",
                    "op": "auth",
                }
            ]
        )

        def factory(url: str, **kwargs: Any) -> FakeSocket:
            return private if url.endswith("/private") else public

        health = HealthMonitor()
        processor = BybitStreamProcessor("UNIUSDT", health)
        bridge = DirectBybitWebSocketBridge(
            "wss://stream.bybit.kz/v5/public/linear",
            "wss://stream.bybit.kz/v5/private",
            BybitCredentials(AppMode.LIVE, "key", "secret", "BotW-Mainnet"),
            processor,
            health,
            socket_factory=factory,
            startup_timeout_seconds=1,
            heartbeat_seconds=60,
        )
        try:
            bridge.subscribe("UNIUSDT", "60")
            deadline = time.monotonic() + 0.5
            while processor.snapshot().ticker is None and time.monotonic() < deadline:
                time.sleep(0.01)
            ticker = processor.snapshot().ticker
            self.assertIsNotNone(ticker)
            public_ok, private_ok = bridge.transport_status(datetime.now(UTC))
            self.assertTrue(public_ok)
            self.assertTrue(private_ok)
            self.assertIsNone(bridge.last_error)
        finally:
            bridge.close()

    def test_private_auth_rejection_is_reported_with_channel(self) -> None:
        public = FakeSocket([])
        private = FakeSocket(
            [
                {
                    "success": False,
                    "ret_msg": "invalid api key",
                    "req_id": "botw-auth",
                    "op": "auth",
                }
            ]
        )

        def factory(url: str, **kwargs: Any) -> FakeSocket:
            return private if url.endswith("/private") else public

        bridge = DirectBybitWebSocketBridge(
            "wss://stream.bybit.kz/v5/public/linear",
            "wss://stream.bybit.kz/v5/private",
            BybitCredentials(AppMode.LIVE, "key", "secret", "BotW-Mainnet"),
            BybitStreamProcessor("UNIUSDT", HealthMonitor()),
            HealthMonitor(),
            socket_factory=factory,
            startup_timeout_seconds=1,
        )
        with self.assertRaisesRegex(ConnectionError, "private WebSocket"):
            bridge.subscribe("UNIUSDT", "60")
        bridge.close()


if __name__ == "__main__":
    unittest.main()
