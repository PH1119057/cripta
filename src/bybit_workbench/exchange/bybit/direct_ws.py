from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import websocket

from bybit_workbench.app.credentials import BybitCredentials

from .errors import BybitProtocolError
from .health import HealthMonitor
from .streams import BybitStreamProcessor

SocketFactory = Callable[..., Any]


class DirectBybitWebSocketBridge:
    """Minimal read-only V5 WebSocket client for regional Bybit endpoints.

    The implementation intentionally uses ``websocket.create_connection`` rather
    than pybit's WebSocketApp manager.  It mirrors the transport path used by the
    operator's direct connectivity diagnostic and keeps authentication/subscription
    ordering explicit: private subscriptions are sent only after an auth success.
    """

    def __init__(
        self,
        public_url: str,
        private_url: str,
        credentials: BybitCredentials,
        processor: BybitStreamProcessor,
        health: HealthMonitor,
        *,
        socket_factory: SocketFactory = websocket.create_connection,
        connect_timeout_seconds: float = 10.0,
        startup_timeout_seconds: float = 12.0,
        heartbeat_seconds: float = 20.0,
    ) -> None:
        if not public_url.startswith("wss://") or not private_url.startswith("wss://"):
            raise ValueError("Bybit WebSocket URLs must use wss://")
        if connect_timeout_seconds <= 0 or startup_timeout_seconds <= 0 or heartbeat_seconds <= 0:
            raise ValueError("WebSocket timeout values must be positive")
        self.public_url = public_url
        self.private_url = private_url
        self.credentials = credentials
        self.processor = processor
        self.health = health
        self.socket_factory = socket_factory
        self.connect_timeout_seconds = connect_timeout_seconds
        self.startup_timeout_seconds = startup_timeout_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._stop = threading.Event()
        self._public_ready = threading.Event()
        self._private_ready = threading.Event()
        self._public_socket: Any | None = None
        self._private_socket: Any | None = None
        self._public_thread: threading.Thread | None = None
        self._private_thread: threading.Thread | None = None
        self._error_lock = threading.Lock()
        self._last_error: str | None = None
        self._activity_lock = threading.Lock()
        self._last_transport_activity: dict[str, float | None] = {
            "public": None,
            "private": None,
        }
        self._transport_stale_seconds = max(45.0, heartbeat_seconds * 3.0)

    def subscribe(self, symbol: str, interval: str) -> None:
        symbol = symbol.strip().upper()
        interval = interval.strip()
        if not symbol or not interval:
            raise ValueError("symbol and interval are required")
        if self._threads_alive():
            raise RuntimeError("WebSocket bridge is already running")

        self._stop.clear()
        self._public_ready.clear()
        self._private_ready.clear()
        with self._error_lock:
            self._last_error = None
        with self._activity_lock:
            self._last_transport_activity = {"public": None, "private": None}

        self._public_thread = threading.Thread(
            target=self._public_worker,
            args=(symbol, interval),
            name="bybit-kz-public-ws",
            daemon=True,
        )
        self._private_thread = threading.Thread(
            target=self._private_worker,
            name="bybit-kz-private-ws",
            daemon=True,
        )
        self._public_thread.start()
        self._private_thread.start()

        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self._public_ready.is_set() and self._private_ready.is_set():
                return
            error = self.last_error
            if error is not None:
                self.close()
                raise ConnectionError(error)
            time.sleep(0.05)

        public = self._public_ready.is_set()
        private = self._private_ready.is_set()
        self.close()
        raise ConnectionError(
            "WebSocket startup timed out "
            f"(public_ready={public}, private_ready={private})"
        )

    def close(self) -> None:
        self._stop.set()
        for sock in (self._public_socket, self._private_socket):
            if sock is None:
                continue
            with suppress(Exception):
                sock.close()
        for thread in (self._public_thread, self._private_thread):
            if (
                thread is not None
                and thread.is_alive()
                and thread is not threading.current_thread()
            ):
                thread.join(timeout=1.0)
        self._public_socket = None
        self._private_socket = None

    def transport_status(self, now: datetime) -> tuple[bool, bool]:
        public_connected = self._transport_connected(
            "public", self._public_socket, self._public_thread
        )
        private_connected = self._transport_connected(
            "private", self._private_socket, self._private_thread
        )
        if not public_connected:
            self.health.mark_error(
                "public", self.last_error or "public WebSocket transport is not alive"
            )
        if private_connected:
            # Private business topics may stay silent on an empty account. A live
            # socket with heartbeat activity is sufficient transport evidence.
            self.health.mark_message("private", now)
        else:
            self.health.mark_error(
                "private", self.last_error or "private WebSocket transport is not alive"
            )
        return public_connected, private_connected

    @property
    def last_error(self) -> str | None:
        with self._error_lock:
            return self._last_error

    def _public_worker(self, symbol: str, interval: str) -> None:
        sock: Any | None = None
        try:
            sock = self._connect(self.public_url)
            self._public_socket = sock
            self._mark_transport_activity("public")
            self.health.mark_connected("public")
            self._send_json(
                sock,
                {
                    "op": "subscribe",
                    "req_id": "botw-public",
                    "args": [f"kline.{interval}.{symbol}", f"tickers.{symbol}"],
                },
            )
            self._public_ready.set()
            self._receive_loop(sock, "public")
        except Exception as exc:
            self._record_error("public", exc)
        finally:
            if sock is not None:
                with suppress(Exception):
                    sock.close()

    def _private_worker(self) -> None:
        sock: Any | None = None
        try:
            sock = self._connect(self.private_url)
            self._private_socket = sock
            self._mark_transport_activity("private")
            expires = int(time.time() * 1000) + 10_000
            payload = f"GET/realtime{expires}".encode()
            signature = hmac.new(
                self.credentials.api_secret.encode(),
                payload,
                hashlib.sha256,
            ).hexdigest()
            self._send_json(
                sock,
                {
                    "op": "auth",
                    "req_id": "botw-auth",
                    "args": [self.credentials.api_key, expires, signature],
                },
            )
            self._await_private_auth(sock)
            self.health.mark_connected("private")
            self._send_json(
                sock,
                {
                    "op": "subscribe",
                    "req_id": "botw-private",
                    "args": ["order", "execution", "position", "wallet"],
                },
            )
            self._private_ready.set()
            self._receive_loop(sock, "private")
        except Exception as exc:
            self._record_error("private", exc)
        finally:
            if sock is not None:
                with suppress(Exception):
                    sock.close()

    def _await_private_auth(self, sock: Any) -> None:
        deadline = time.monotonic() + self.connect_timeout_seconds
        while time.monotonic() < deadline and not self._stop.is_set():
            message = self._recv_json(sock)
            self._mark_transport_activity("private")
            if self._is_pong(message):
                continue
            if message.get("op") != "auth":
                continue
            if message.get("success") is True or message.get("retCode") == 0:
                return
            raise PermissionError(
                "Bybit private WebSocket authentication rejected: "
                f"{message.get('ret_msg') or message.get('retMsg') or 'unknown error'}"
            )
        raise TimeoutError("Bybit private WebSocket authentication timed out")

    def _receive_loop(self, sock: Any, channel: str) -> None:
        next_heartbeat = time.monotonic() + self.heartbeat_seconds
        while not self._stop.is_set():
            try:
                message = self._recv_json(sock)
            except websocket.WebSocketTimeoutException:
                message = None
            if message is not None:
                self._mark_transport_activity(channel)
                if self._is_pong(message):
                    pass
                elif message.get("op") == "subscribe":
                    if message.get("success") is False or message.get("retCode") not in (None, 0):
                        raise ConnectionError(
                            f"Bybit {channel} subscription rejected: "
                            f"{message.get('ret_msg') or message.get('retMsg') or 'unknown error'}"
                        )
                elif channel == "public":
                    try:
                        self.processor.on_public(
                            message,
                            received_at=datetime.now(UTC),
                        )
                    except BybitProtocolError:
                        # Market payload anomalies must not churn a healthy socket.
                        # Health keeps the warning and the normal freshness gate
                        # blocks entries if valid updates stop arriving.
                        continue
                else:
                    self.processor.on_private(message, received_at=datetime.now(UTC))
            if time.monotonic() >= next_heartbeat:
                self._send_json(sock, {"op": "ping"})
                next_heartbeat = time.monotonic() + self.heartbeat_seconds

    def _connect(self, url: str) -> Any:
        sock = self.socket_factory(url, timeout=self.connect_timeout_seconds)
        with suppress(AttributeError):
            sock.settimeout(1.0)
        return sock

    @staticmethod
    def _send_json(sock: Any, payload: dict[str, Any]) -> None:
        sock.send(json.dumps(payload, separators=(",", ":")))

    @staticmethod
    def _recv_json(sock: Any) -> dict[str, Any]:
        raw = sock.recv()
        if raw in (None, ""):
            raise ConnectionError("Bybit WebSocket closed by remote peer")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ConnectionError("Bybit WebSocket message must be a JSON object")
        return value

    @staticmethod
    def _is_pong(message: dict[str, Any]) -> bool:
        return message.get("op") == "pong" or message.get("ret_msg") == "pong"


    def _mark_transport_activity(self, channel: str) -> None:
        with self._activity_lock:
            self._last_transport_activity[channel] = time.monotonic()

    def _transport_connected(
        self,
        channel: str,
        sock: Any | None,
        thread: threading.Thread | None,
    ) -> bool:
        if not self._socket_connected(sock) or not self._thread_alive(thread):
            return False
        with self._activity_lock:
            observed = self._last_transport_activity[channel]
        if observed is None:
            return False
        return (time.monotonic() - observed) <= self._transport_stale_seconds

    def _record_error(self, channel: str, exc: Exception) -> None:
        text = f"{channel} WebSocket: {type(exc).__name__}: {exc}"
        with self._error_lock:
            if self._last_error is None:
                self._last_error = text
        self.health.mark_error(channel, text)

    def _threads_alive(self) -> bool:
        return self._thread_alive(self._public_thread) or self._thread_alive(self._private_thread)

    @staticmethod
    def _thread_alive(thread: threading.Thread | None) -> bool:
        return thread is not None and thread.is_alive()

    @staticmethod
    def _socket_connected(sock: Any | None) -> bool:
        if sock is None:
            return False
        try:
            return bool(getattr(sock, "connected", False))
        except Exception:
            return False
