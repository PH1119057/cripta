from __future__ import annotations

import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any, cast

import websocket

from bybit_workbench.app.config import AppSettings
from bybit_workbench.domain.models import Candle
from bybit_workbench.exchange.bybit.mappers import map_rest_klines, map_ws_klines

from .audit import EntryBotAuditStore
from .calibration import load_calibrations
from .config import EntryBotConfig
from .engine import EntrySymbolEngine, OiPoint
from .handoff import PositionHandoffStore
from .models import EntryBotSnapshot, EntrySignalEvent, ScannerState

_HTTPS_CONTEXT = ssl.create_default_context()


def subscription_topics(config: EntryBotConfig) -> tuple[str, ...]:
    topics: list[str] = []
    for symbol in config.all_market_symbols:
        for interval in ("5", "15", "60"):
            topics.append(f"kline.{interval}.{symbol}")
        topics.append(f"tickers.{symbol}")
    for symbol in config.working_symbols:
        topics.append(f"publicTrade.{symbol}")
    return tuple(topics)


class EntryBotRuntime:
    """Public-data multi-asset Entry V1 scanner.

    This runtime has deliberately no authenticated Bybit transport and no order
    method. P48 V1 runs live market screening and persists Core signals while
    automatic Mainnet writes stay fail-closed until production-equivalence and
    per-signal execution safety are connected.
    """

    def __init__(
        self,
        settings: AppSettings,
        *,
        config: EntryBotConfig | None = None,
        calibration_path: Path | None = None,
    ) -> None:
        if settings.endpoint_profile.rest_url is None:
            raise ValueError("Entry Bot requires a network REST endpoint")
        if settings.endpoint_profile.public_ws_url is None:
            raise ValueError("Entry Bot requires a public WebSocket endpoint")
        self._settings = settings
        self.config = config or EntryBotConfig()
        self.calibration_path = calibration_path or (
            settings.database_path.parent / "entry_bot_calibration.json"
        )
        calibrations = load_calibrations(self.calibration_path)
        self._engines = {
            symbol: EntrySymbolEngine(symbol, self.config, calibrations.get(symbol))
            for symbol in self.config.working_symbols
        }
        self._handoffs = PositionHandoffStore(settings.database_path)
        self._audit = EntryBotAuditStore(settings.database_path)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: Any | None = None
        self._lock = threading.RLock()
        self._state = ScannerState.STOPPED
        self._detail = "Entry Bot stopped"
        self._updated_at: datetime | None = None
        self._signals: SimpleQueue[EntrySignalEvent] = SimpleQueue()
        self._reference_prices: dict[str, Decimal] = {}
        self._last_ticker_oi_sample: dict[str, datetime] = {}
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        calibrations = load_calibrations(self.calibration_path)
        for symbol, engine in self._engines.items():
            engine.set_calibration(calibrations.get(symbol))
        self._stop.clear()
        with self._lock:
            self._state = ScannerState.WARMUP
            self._detail = "Starting 10-asset public scanner…"
            self._updated_at = datetime.now(UTC)
            self._last_error = None
        self._thread = threading.Thread(
            target=self._worker,
            name="entry-bot-live-scanner",
            daemon=True,
        )
        self._thread.start()

    def request_stop(self) -> None:
        self._stop.set()
        if self.running:
            self._set_state(ScannerState.STOPPING, "Stopping Entry Bot…")
        sock = self._socket
        if sock is not None:
            with suppress(Exception):
                sock.close()

    def stop(self) -> None:
        self.request_stop()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=25.0)
        if thread is not None and thread.is_alive():
            self._set_error("Entry Bot shutdown timeout; public worker is still alive")
            return
        self._thread = None
        self._socket = None
        with self._lock:
            self._state = ScannerState.STOPPED
            self._detail = "Entry Bot stopped"
            self._updated_at = datetime.now(UTC)

    def close(self) -> None:
        self.stop()
        if not self.running:
            self._handoffs.close()
            self._audit.close()

    def reconfigure(self, settings: AppSettings) -> None:
        if self.running:
            raise RuntimeError("Stop Entry Bot before changing endpoint")
        if (
            settings.endpoint_profile.rest_url is None
            or settings.endpoint_profile.public_ws_url is None
        ):
            raise ValueError("Entry Bot requires network endpoints")
        self._settings = settings

    def snapshot(self) -> EntryBotSnapshot:
        now = datetime.now(UTC)
        assets = tuple(
            self._engines[symbol].snapshot(now) for symbol in self.config.working_symbols
        )
        with self._lock:
            return EntryBotSnapshot(
                state=self._state,
                running=self.running,
                detail=self._detail,
                execution_mode="SHADOW · AUTO ENTRY LOCKED",
                assets=assets,
                updated_at=self._updated_at,
                audit_event_count=self._audit.count,
            )

    def drain_signals(self) -> tuple[EntrySignalEvent, ...]:
        rows: list[EntrySignalEvent] = []
        while True:
            try:
                rows.append(self._signals.get_nowait())
            except Empty:
                return tuple(rows)

    def _worker(self) -> None:
        try:
            ready = self._warmup()
        except Exception as exc:
            self._set_error(f"Entry Bot warm-up failed: {_error_text(exc)}")
            return
        if not ready or self._stop.is_set():
            return
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._set_state(ScannerState.CONNECTING, "Connecting public multi-asset WS…")
                self._run_socket()
                backoff = 1.0
            except Exception as exc:
                if self._stop.is_set():
                    break
                now = datetime.now(UTC)
                detail = _error_text(exc)
                for engine in self._engines.values():
                    engine.mark_stream_gap(now, "public stream gap; flow warm-up restarted")
                    self._flush_audit(engine)
                self._set_state(
                    ScannerState.RECONNECTING,
                    f"Public WS reconnect in {backoff:.0f}s · {detail}",
                )
                self._stop.wait(backoff)
                backoff = min(10.0, backoff * 2.0)
        if self._stop.is_set():
            self._set_state(ScannerState.STOPPED, "Entry Bot stopped")

    def _warmup(self) -> bool:
        rest = self._settings.endpoint_profile.rest_url
        if rest is None:
            raise ValueError("REST endpoint is missing")
        calibrated = tuple(
            symbol
            for symbol in self.config.working_symbols
            if self._engines[symbol].calibration is not None
        )
        skipped = len(self.config.working_symbols) - len(calibrated)
        if not calibrated:
            self._set_state(
                ScannerState.STOPPED,
                "No calibrated assets. Build Entry Bot calibration before screening.",
            )
            return False

        ready = 0
        failed: list[str] = []
        total = len(calibrated)
        for index, symbol in enumerate(calibrated, start=1):
            if self._stop.is_set():
                return False
            suffix = f" · {skipped} skipped (NO CALIBRATION)" if skipped else ""
            self._set_state(
                ScannerState.WARMUP,
                f"Warm-up {index}/{total}: {symbol} · 5m/15m/60m + OI{suffix}",
            )
            observed_at = datetime.now(UTC)
            try:
                candles = {
                    interval: self._fetch_klines(rest, symbol, interval, observed_at)
                    for interval in ("5", "15", "60")
                }
                oi = self._fetch_open_interest(rest, symbol)
                self._engines[symbol].load_history(candles, oi, observed_at=observed_at)
                self._flush_audit(self._engines[symbol])
            except Exception as exc:
                detail = _error_text(exc)
                self._engines[symbol].mark_warmup_error(
                    datetime.now(UTC),
                    f"REST warm-up failed after retries: {detail}",
                )
                self._flush_audit(self._engines[symbol])
                failed.append(symbol)
                continue
            ready += 1

        if ready == 0:
            names = ", ".join(failed) if failed else "none"
            self._set_error(
                f"Entry Bot warm-up failed for all {total} calibrated assets: {names}"
            )
            return False

        parts = [f"Historical warm-up complete: {ready}/{total} calibrated assets ready"]
        if failed:
            parts.append("REST failed: " + ", ".join(failed))
        if skipped:
            parts.append(f"NO CALIBRATION skipped: {skipped}")
        parts.append("collecting live 4+1 minute tape windows")
        self._set_state(ScannerState.CONNECTING, " · ".join(parts))
        return True

    def _fetch_klines(
        self,
        rest: str,
        symbol: str,
        interval: str,
        observed_at: datetime,
    ) -> tuple[Candle, ...]:
        payload = self._get_json(
            rest,
            "/v5/market/kline",
            {
                "category": "linear",
                "symbol": symbol,
                "interval": interval,
                "limit": self.config.history_limit,
            },
        )
        rows = _result_list(payload)
        candles = map_rest_klines(
            cast(list[list[Any]], rows),
            symbol=symbol,
            interval=interval,
            observed_at=observed_at,
        )
        return tuple(item for item in candles if item.is_closed)

    def _fetch_open_interest(self, rest: str, symbol: str) -> tuple[OiPoint, ...]:
        payload = self._get_json(
            rest,
            "/v5/market/open-interest",
            {
                "category": "linear",
                "symbol": symbol,
                "intervalTime": "5min",
                "limit": 30,
            },
        )
        rows = _result_list(payload)
        points: list[OiPoint] = []
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            try:
                value = Decimal(str(raw["openInterest"]))
                timestamp = datetime.fromtimestamp(int(str(raw["timestamp"])) / 1000, UTC)
            except (KeyError, InvalidOperation, ValueError) as exc:
                raise ValueError(f"invalid OI row for {symbol}") from exc
            if value > 0:
                points.append(OiPoint(timestamp, value))
        return tuple(sorted(points, key=lambda item: item.timestamp))

    def _get_json(
        self,
        rest: str,
        endpoint: str,
        params: Mapping[str, object],
    ) -> dict[str, Any]:
        url = f"{rest.rstrip('/')}{endpoint}?{urllib.parse.urlencode(params)}"
        attempts = 4
        symbol = str(params.get("symbol") or "market")
        transient: tuple[type[Exception], ...] = (
            TimeoutError,
            socket.timeout,
            ssl.SSLError,
            urllib.error.URLError,
            ConnectionError,
        )
        for attempt in range(1, attempts + 1):
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "BybitStrategyWorkbench/EntryBot-P48"},
            )
            try:
                with urllib.request.urlopen(
                    request,
                    timeout=15.0,
                    context=_HTTPS_CONTEXT,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError:
                raise
            except transient as exc:
                if attempt >= attempts:
                    raise
                delay = min(4.0, 0.75 * (2 ** (attempt - 1)))
                self._set_state(
                    ScannerState.WARMUP,
                    f"REST retry {attempt + 1}/{attempts}: {symbol} {endpoint} · "
                    f"{_error_text(exc)}",
                )
                if self._stop.wait(delay):
                    raise InterruptedError("Entry Bot stop requested during REST retry") from exc
                continue
            if not isinstance(payload, dict):
                raise ValueError(f"Bybit response is not an object: {endpoint}")
            if int(payload.get("retCode", -1)) != 0:
                raise RuntimeError(
                    f"Bybit {endpoint} failed: retCode={payload.get('retCode')} "
                    f"retMsg={payload.get('retMsg')}"
                )
            return cast(dict[str, Any], payload)
        raise RuntimeError(f"unreachable REST retry state for {symbol} {endpoint}")

    def _run_socket(self) -> None:
        url = self._settings.endpoint_profile.public_ws_url
        if url is None:
            raise ValueError("public WS endpoint is missing")
        sock = websocket.create_connection(url, timeout=10.0)
        self._socket = sock
        with suppress(AttributeError):
            sock.settimeout(1.0)
        try:
            topics = subscription_topics(self.config)
            for chunk_index, start in enumerate(range(0, len(topics), 40), start=1):
                chunk = topics[start : start + 40]
                sock.send(
                    json.dumps(
                        {
                            "op": "subscribe",
                            "req_id": f"entry-bot-{chunk_index}",
                            "args": list(chunk),
                        },
                        separators=(",", ":"),
                    )
                )
            self._set_state(
                ScannerState.RUNNING,
                f"Screening {len(self.config.working_symbols)} assets · "
                f"{len(topics)} public topics",
            )
            next_ping = time.monotonic() + 20.0
            while not self._stop.is_set():
                message: dict[str, Any] | None = None
                try:
                    raw = sock.recv()
                    if raw in (None, ""):
                        raise ConnectionError("public WebSocket closed by remote peer")
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        message = cast(dict[str, Any], parsed)
                except websocket.WebSocketTimeoutException:
                    pass
                if message is not None:
                    self._handle_message(message)
                if time.monotonic() >= next_ping:
                    sock.send('{"op":"ping"}')
                    next_ping = time.monotonic() + 20.0
        finally:
            with suppress(Exception):
                sock.close()
            self._socket = None

    def _handle_message(self, message: dict[str, Any]) -> None:
        if message.get("op") == "pong" or message.get("ret_msg") == "pong":
            return
        if message.get("op") == "subscribe":
            if message.get("success") is False or message.get("retCode") not in (None, 0):
                raise ConnectionError(
                    "Entry Bot subscription rejected: "
                    + str(message.get("ret_msg") or message.get("retMsg") or "unknown")
                )
            return
        topic = str(message.get("topic") or "")
        if topic.startswith("publicTrade."):
            self._handle_public_trade(message)
        elif topic.startswith("kline."):
            self._handle_kline(message)
        elif topic.startswith("tickers."):
            self._handle_ticker(message)

    def _handle_public_trade(self, message: dict[str, Any]) -> None:
        data = message.get("data")
        if not isinstance(data, list):
            return
        for raw in data:
            if not isinstance(raw, Mapping):
                continue
            symbol = str(raw.get("s") or "").upper()
            engine = self._engines.get(symbol)
            if engine is None:
                continue
            try:
                signal = engine.on_trade(
                    price=Decimal(str(raw["p"])),
                    size=Decimal(str(raw["v"])),
                    taker_side=str(raw["S"]),
                    traded_at=datetime.fromtimestamp(int(str(raw["T"])) / 1000, UTC),
                )
            except (KeyError, InvalidOperation, ValueError):
                continue
            self._flush_audit(engine)
            if signal is not None:
                self._handoffs.record_signal(signal)
                self._signals.put(signal)
                self._set_state(
                    ScannerState.RUNNING,
                    f"CORE signal {signal.symbol} {signal.direction} @ {signal.entry_price}",
                )

    def _handle_kline(self, message: dict[str, Any]) -> None:
        try:
            candles = map_ws_klines(message)
        except Exception:
            return
        now = datetime.now(UTC)
        for candle in candles:
            engine = self._engines.get(candle.symbol)
            if engine is None:
                continue
            if candle.timeframe == "5" and not candle.is_closed:
                engine.on_current_five_minute_open(candle.opened_at, candle.open, now)
            if candle.is_closed:
                engine.on_closed_candle(candle)
            self._flush_audit(engine)

    def _handle_ticker(self, message: dict[str, Any]) -> None:
        raw = message.get("data")
        if isinstance(raw, list):
            if not raw or not isinstance(raw[0], Mapping):
                return
            data = raw[0]
        elif isinstance(raw, Mapping):
            data = raw
        else:
            return
        symbol = str(data.get("symbol") or message.get("topic", "").split(".")[-1]).upper()
        observed_ms = int(str(message.get("ts") or time.time() * 1000))
        observed = datetime.fromtimestamp(observed_ms / 1000, UTC)
        last_raw = data.get("lastPrice")
        if last_raw not in (None, ""):
            try:
                last = Decimal(str(last_raw))
            except InvalidOperation:
                last = None
            if last is not None and symbol in self.config.reference_symbols:
                self._reference_prices[symbol] = last
        engine = self._engines.get(symbol)
        if engine is None:
            return
        oi_raw = data.get("openInterest")
        if oi_raw in (None, ""):
            return
        previous = self._last_ticker_oi_sample.get(symbol)
        if previous is not None and observed - previous < timedelta(seconds=30):
            return
        try:
            oi = Decimal(str(oi_raw))
        except InvalidOperation:
            return
        engine.on_open_interest(oi, observed)
        self._flush_audit(engine)
        self._last_ticker_oi_sample[symbol] = observed

    def _flush_audit(self, engine: EntrySymbolEngine) -> None:
        events = engine.drain_audit_events()
        if events:
            self._audit.record_events(events)

    def _set_state(self, state: ScannerState, detail: str) -> None:
        with self._lock:
            self._state = state
            self._detail = detail
            self._updated_at = datetime.now(UTC)
            if state is not ScannerState.ERROR:
                self._last_error = None

    def _set_error(self, detail: str) -> None:
        with self._lock:
            self._state = ScannerState.ERROR
            self._detail = detail
            self._updated_at = datetime.now(UTC)
            self._last_error = detail


def _result_list(payload: Mapping[str, Any]) -> list[Any]:
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("Bybit result object is missing")
    rows = result.get("list")
    if not isinstance(rows, list):
        raise ValueError("Bybit result list is missing")
    return rows


def _error_text(exc: Exception) -> str:
    return str(exc).strip() or exc.__class__.__name__
