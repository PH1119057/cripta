from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import queue
import signal
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

import psycopg
import websocket

from bybit_workbench.domain.models import Candle
from bybit_workbench.exchange.bybit.mappers import map_rest_klines, map_ws_klines
from bybit_workbench.universal_entry import (
    DataQuality,
    FrozenPolicy,
    MarketFactEnvelope,
    ObjectiveContext,
    TechnicalReadiness,
    TradingCapacitySnapshot,
    UniversalEntryEngine,
)
from bybit_workbench.universal_entry.fingerprint import fingerprint
from bybit_workbench.universal_entry.market_watch import GenericOiPoint
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.oi30s_source import (
    CurrentOiResponse,
    Oi30sConfig,
    Oi30sHealthTracker,
    OiSlotResult,
    OiSlotState,
    poll_current_oi_slot,
    slot_at,
)
from bybit_workbench.universal_entry.paper_runtime import PaperTradeRuntime
from bybit_workbench.universal_entry.parity import V1DeterministicParityRunner
from bybit_workbench.universal_entry.registry import ActivePlanRegistry
from bybit_workbench.universal_entry.runtime_loader import load_active_strategy_bundles
from bybit_workbench.universal_entry.shadow_runtime import (
    DurableFactJournal,
    OnlineParityComparator,
    ShadowComparability,
    ShadowComparabilityGate,
    ShadowRunIdentity,
    derive_unknown_prestart_horizon_seconds,
)
from bybit_workbench.universal_entry.storage import ShadowParityStore, StrategyEntryStore
from bybit_workbench.universal_entry.trade_mirror import PublicTradeMirrorBuffer
from bybit_workbench.universal_entry.transport_continuity import (
    ContinuityAction,
    ContinuityNotProvable,
    ExactFactDeduper,
    OiSampleCursor,
    PublicWsHeartbeat,
    ReplayTrade,
    TradeCursor,
    assess_oi30s_continuity,
    audit_recent_trade_window,
    build_exact_trade_replay,
    expected_boundaries,
    resolve_continuity_action,
)
from bybit_workbench.universal_entry.v1_compat import load_v1_compatibility_bundle

PROJECT_ROOT = Path(os.environ.get("CRIPTA_U5_SOURCE_ROOT", "/srv/cripta/source_checkout"))
STATE_ROOT = Path(os.environ.get("CRIPTA_U5_STATE_ROOT", "/var/lib/cripta/universal_entry_shadow"))
STATUS_PATH = STATE_ROOT / "status.json"
JOURNAL_PATH = STATE_ROOT / "normalized_facts.jsonl"
PUBLIC_REST = os.environ.get("CRIPTA_U5_PUBLIC_REST", "https://api.bybit.kz")
PUBLIC_WS = os.environ.get("CRIPTA_U5_PUBLIC_WS", "wss://stream.bybit.kz/v5/public/linear")
DATABASE_DSN = os.environ.get(
    "CRIPTA_U5_DATABASE_DSN", "dbname=cripta user=cripta host=/var/run/postgresql"
)
LOADED_COMMIT = os.environ.get("CRIPTA_U5_LOADED_COMMIT", "").strip()
BASELINE_COMMIT = os.environ.get(
    "CRIPTA_U5_BASELINE_COMMIT", "49670cb0631a8742b2bf8dace9ab33d6b29a107d"
).strip()
FACT_SOURCE_ID = os.environ.get("CRIPTA_U5_FACT_SOURCE_ID", "BYBIT_PUBLIC_NORMALIZED_U5_V1").strip()
RUNTIME_MODE = os.environ.get("CRIPTA_U5_RUNTIME_MODE", "PARITY_V1").strip().upper()
OBSERVER_STATE_ROOT = Path(
    os.environ.get(
        "CRIPTA_UNIVERSAL_ENTRY_OBSERVER_STATE_ROOT",
        "/var/lib/cripta/universal_entry_observer",
    )
)
OBSERVER_STATUS_PATH = OBSERVER_STATE_ROOT / "status.json"
OI_SAMPLE_SECONDS = int(os.environ.get("CRIPTA_U5_OI_SAMPLE_SECONDS", "30"))

PING_INTERVAL_SECONDS = 10.0
PONG_TIMEOUT_SECONDS = 5.0
TRADE_SILENCE_AUDIT_SECONDS = 10.0
TRADE_AUDIT_REQUEST_TIMEOUT_SECONDS = 4.0
MIRROR_RETENTION_SECONDS = 600
MIRROR_READY_TIMEOUT_SECONDS = 12.0
REST_OI30S_SOURCE_ID = "BYBIT_PUBLIC_REST_CURRENT_OI_30S_V1"
LEGACY_WS_OI30S_SOURCE_ID = "BYBIT_PUBLIC_NORMALIZED_U5_V1_OI30S"
_HTTPS_CONTEXT = ssl.create_default_context()


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _result_list(payload: Mapping[str, object]) -> list[Any]:
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise ValueError("public response result is not an object")
    rows = result.get("list")
    if not isinstance(rows, list):
        raise ValueError("public response result.list is not a list")
    return rows


def _get_json(endpoint: str, params: Mapping[str, object]) -> dict[str, Any]:
    url = f"{PUBLIC_REST.rstrip('/')}{endpoint}?{urllib.parse.urlencode(params)}"
    transient: tuple[type[Exception], ...] = (
        TimeoutError,
        socket.timeout,
        ssl.SSLError,
        urllib.error.URLError,
        ConnectionError,
    )
    for attempt in range(4):
        request = urllib.request.Request(url, headers={"User-Agent": "Cripta-U5-Shadow/1"})
        try:
            with urllib.request.urlopen(request, timeout=15.0, context=_HTTPS_CONTEXT) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError:
            raise
        except transient:
            if attempt == 3:
                raise
            time.sleep(min(4.0, 0.75 * (2**attempt)))
            continue
        if not isinstance(payload, dict):
            raise ValueError("public response is not an object")
        ret_code = int(payload.get("retCode", -1))
        if ret_code == 10006 and attempt < 3:
            time.sleep(min(4.0, 0.75 * (2**attempt)))
            continue
        if ret_code != 0:
            raise RuntimeError(
                f"public endpoint failed: retCode={payload.get('retCode')} "
                f"retMsg={payload.get('retMsg')}"
            )
        return cast(dict[str, Any], payload)
    raise RuntimeError("unreachable public REST retry state")


def _fetch_current_oi_rest(timeout: float) -> CurrentOiResponse:
    request_started_at = datetime.now(UTC)
    query = urllib.parse.urlencode({"category": "linear"})
    request = urllib.request.Request(
        f"{PUBLIC_REST.rstrip('/')}/v5/market/tickers?{query}",
        headers={"User-Agent": "Cripta-U5-OI30S/1"},
    )
    with urllib.request.urlopen(request, timeout=timeout, context=_HTTPS_CONTEXT) as response:
        payload = json.loads(response.read().decode("utf-8"))
    response_received_at = datetime.now(UTC)
    if not isinstance(payload, dict) or int(payload.get("retCode", -1)) != 0:
        raise RuntimeError("public current-tickers request failed")
    server_ms = payload.get("time")
    if server_ms is None:
        raise RuntimeError("public current-tickers response lacks server time")
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError("public current-tickers result is not an object")
    rows = result.get("list")
    if not isinstance(rows, list):
        raise RuntimeError("public current-tickers result.list is not a list")
    values: dict[str, Decimal] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        symbol = str(row.get("symbol") or "").upper()
        raw_oi = row.get("openInterest")
        if not symbol or raw_oi in (None, ""):
            continue
        try:
            value = Decimal(str(raw_oi))
        except InvalidOperation:
            continue
        if value.is_finite() and value > 0:
            values[symbol] = value
    return CurrentOiResponse(
        request_started_at=request_started_at,
        response_received_at=response_received_at,
        exchange_server_observed_at=datetime.fromtimestamp(int(str(server_ms)) / 1000, UTC),
        open_interest=values,
        provenance="GET /v5/market/tickers?category=linear",
    )


def _run_rest_oi30s_worker(
    config: Oi30sConfig,
    output: queue.Queue[OiSlotResult],
    stop_event: threading.Event,
    *,
    fetch_fn: Any | None = None,
) -> None:
    fetch = _fetch_current_oi_rest if fetch_fn is None else fetch_fn
    nominal = slot_at(datetime.now(UTC), config.slot_seconds) + timedelta(
        seconds=config.slot_seconds
    )
    while not stop_event.is_set():
        delay = (nominal - datetime.now(UTC)).total_seconds()
        if delay > 0 and stop_event.wait(delay):
            return
        result = poll_current_oi_slot(
            config,
            nominal_slot_at=nominal,
            fetch=fetch,
        )
        output.put(result)
        if result.state is not OiSlotState.COMPLETE:
            return
        nominal += timedelta(seconds=config.slot_seconds)


def _fetch_history(
    symbol: str,
    observed_at: datetime,
    intervals: tuple[str, ...] = ("5", "15", "60"),
) -> dict[str, tuple[Candle, ...]]:
    history: dict[str, tuple[Candle, ...]] = {}
    for interval in intervals:
        payload = _get_json(
            "/v5/market/kline",
            {"category": "linear", "symbol": symbol, "interval": interval, "limit": 1000},
        )
        candles = map_rest_klines(
            cast(list[list[Any]], _result_list(payload)),
            symbol=symbol,
            interval=interval,
            observed_at=observed_at,
        )
        history[interval] = tuple(item for item in candles if item.is_closed)
    return history


def _fetch_oi_history(symbol: str) -> tuple[GenericOiPoint, ...]:
    payload = _get_json(
        "/v5/market/open-interest",
        {"category": "linear", "symbol": symbol, "intervalTime": "5min", "limit": 30},
    )
    points: list[GenericOiPoint] = []
    for raw in _result_list(payload):
        if not isinstance(raw, Mapping):
            continue
        try:
            value = Decimal(str(raw["openInterest"]))
            timestamp = datetime.fromtimestamp(int(str(raw["timestamp"])) / 1000, UTC)
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise ValueError(f"invalid OI row for {symbol}") from exc
        if value > 0:
            points.append(GenericOiPoint(timestamp, value))
    return tuple(sorted(points, key=lambda item: item.timestamp))


def _trade_fact(raw: Mapping[str, object], received_at: datetime) -> MarketFactEnvelope:
    symbol = str(raw.get("s") or "").upper()
    traded_at = datetime.fromtimestamp(int(str(raw["T"])) / 1000, UTC)
    trade_id = str(raw.get("i") or "")
    source_identity = (
        trade_id
        or hashlib.sha256(
            json.dumps(dict(raw), sort_keys=True, default=str, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
    )
    return MarketFactEnvelope(
        fact_id="trade-" + source_identity,
        event_kind="PUBLIC_TRADE",
        symbol=symbol,
        observed_at=traded_at,
        event_at=traded_at,
        received_at=received_at,
        source_refs=(f"bybit:publicTrade:{symbol}:{source_identity}",),
        attributes=FrozenPolicy.from_mapping(
            {
                "price": str(raw["p"]),
                "size": str(raw["v"]),
                "taker_side": str(raw["S"]),
            }
        ),
    )


def _kline_facts(
    message: Mapping[str, object], received_at: datetime
) -> tuple[MarketFactEnvelope, ...]:
    try:
        candles = map_ws_klines(dict(message))
    except Exception:
        return ()
    topic = str(message.get("topic") or "")
    result: list[MarketFactEnvelope] = []
    for candle in candles:
        boundary = candle.closed_at if candle.is_closed else candle.opened_at
        kind = "CANDLE_CLOSED" if candle.is_closed else "BAR_OPEN"
        if not candle.is_closed and candle.timeframe != "5":
            continue
        observed_at = candle.closed_at if candle.is_closed else received_at
        attributes: dict[str, object]
        if candle.is_closed:
            attributes = {
                "timeframe": candle.timeframe,
                "opened_at": candle.opened_at.isoformat(),
                "closed_at": candle.closed_at.isoformat(),
                "open": str(candle.open),
                "high": str(candle.high),
                "low": str(candle.low),
                "close": str(candle.close),
                "volume": str(candle.volume),
            }
        else:
            attributes = {
                "opened_at": candle.opened_at.isoformat(),
                "open_price": str(candle.open),
            }
        identity = fingerprint(
            {
                "topic": topic,
                "symbol": candle.symbol,
                "timeframe": candle.timeframe,
                "boundary": boundary,
                "closed": candle.is_closed,
            }
        )[:32]
        result.append(
            MarketFactEnvelope(
                fact_id=f"{kind.lower()}-{identity}",
                event_kind=kind,
                symbol=candle.symbol,
                observed_at=observed_at,
                event_at=boundary,
                received_at=received_at,
                source_refs=(
                    f"bybit:{topic}:{candle.symbol}:{candle.timeframe}:{boundary.isoformat()}",
                ),
                attributes=FrozenPolicy.from_mapping(attributes),
            )
        )
    return tuple(result)


def _ticker_oi_fact(
    message: Mapping[str, object],
    received_at: datetime,
    last_samples: dict[str, datetime],
) -> tuple[MarketFactEnvelope, OiSampleCursor] | None:
    raw = message.get("data")
    if isinstance(raw, list):
        if not raw or not isinstance(raw[0], Mapping):
            return None
        data = raw[0]
    elif isinstance(raw, Mapping):
        data = raw
    else:
        return None
    symbol = str(data.get("symbol") or str(message.get("topic") or "").split(".")[-1]).upper()
    observed_ms = int(str(message.get("ts") or int(received_at.timestamp() * 1000)))
    observed_at = datetime.fromtimestamp(observed_ms / 1000, UTC)
    previous = last_samples.get(symbol)
    if previous is not None and observed_at - previous < timedelta(seconds=OI_SAMPLE_SECONDS):
        return None
    raw_oi = data.get("openInterest")
    if raw_oi in (None, ""):
        return None
    try:
        oi = Decimal(str(raw_oi))
    except InvalidOperation:
        return None
    last_samples[symbol] = observed_at
    identity = f"{symbol}:{observed_ms}:{oi}"
    fact = MarketFactEnvelope(
        fact_id="oi-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32],
        event_kind="OPEN_INTEREST",
        symbol=symbol,
        observed_at=observed_at,
        event_at=observed_at,
        received_at=received_at,
        source_refs=(f"bybit:ticker-oi:{identity}",),
        attributes=FrozenPolicy.from_mapping({"open_interest": str(oi)}),
    )
    raw_cs = message.get("cs")
    try:
        ticker_cs = None if raw_cs in (None, "") else int(str(raw_cs))
    except ValueError as exc:
        raise ContinuityNotProvable("ticker OI cross-sequence is invalid") from exc
    return fact, OiSampleCursor(symbol, observed_at, ticker_cs)


def _topics(
    symbols: tuple[str, ...],
    intervals: tuple[str, ...] = ("5", "15", "60"),
) -> tuple[str, ...]:
    topics: list[str] = []
    use_rest_oi30s = FACT_SOURCE_ID == REST_OI30S_SOURCE_ID
    for symbol in symbols:
        for interval in intervals:
            topics.append(f"kline.{interval}.{symbol}")
        if not use_rest_oi30s:
            topics.append(f"tickers.{symbol}")
        topics.append(f"publicTrade.{symbol}")
    return tuple(topics)


def _fetch_server_time() -> datetime:
    payload = _get_json("/v5/market/time", {})
    try:
        return datetime.fromtimestamp(int(str(payload["time"])) / 1000, UTC)
    except (KeyError, ValueError) as exc:
        raise ContinuityNotProvable(
            "public server time is unavailable for continuity proof"
        ) from exc


class PublicTradeStreamBehind(ConnectionError):
    """Public recent-trade proves the accepted WS trade cursor is behind."""


def _fetch_recent_trade_snapshot(
    symbol: str,
) -> tuple[list[Mapping[str, object]], datetime]:
    query = urllib.parse.urlencode({"category": "linear", "symbol": symbol, "limit": 1000})
    request = urllib.request.Request(
        f"{PUBLIC_REST.rstrip('/')}/v5/market/recent-trade?{query}",
        headers={"User-Agent": "Cripta-U5-Trade-Audit/1"},
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=TRADE_AUDIT_REQUEST_TIMEOUT_SECONDS,
            context=_HTTPS_CONTEXT,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise ContinuityNotProvable(
            f"PUBLIC_TRADE silence audit unavailable for {symbol}: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(payload, dict) or int(payload.get("retCode", -1)) != 0:
        raise ContinuityNotProvable(f"PUBLIC_TRADE silence audit failed for {symbol}")
    server_ms = payload.get("time")
    if server_ms is None:
        raise ContinuityNotProvable(f"PUBLIC_TRADE silence audit lacks server time for {symbol}")
    try:
        server_at = datetime.fromtimestamp(int(str(server_ms)) / 1000, UTC)
    except ValueError as exc:
        raise ContinuityNotProvable(
            f"PUBLIC_TRADE silence audit has invalid server time for {symbol}"
        ) from exc
    rows = [row for row in _result_list(payload) if isinstance(row, Mapping)]
    return cast(list[Mapping[str, object]], rows), server_at


def _audit_public_trade_silence(
    symbols: tuple[str, ...],
    trade_cursors: Mapping[str, TradeCursor],
    known_current_seq_exec_ids: Mapping[str, set[str]],
    *,
    fetch_fn: Any | None = None,
) -> dict[str, int]:
    if not symbols:
        return {}
    for symbol in symbols:
        if symbol not in trade_cursors:
            raise ContinuityNotProvable(
                f"PUBLIC_TRADE silence audit lacks exact cursor for {symbol}"
            )

    fetch = _fetch_recent_trade_snapshot if fetch_fn is None else fetch_fn
    snapshots: dict[str, tuple[list[Mapping[str, object]], datetime]] = {}
    if fetch_fn is None:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(10, len(symbols)),
            thread_name_prefix="u5-trade-audit",
        ) as executor:
            pending = {executor.submit(fetch, symbol): symbol for symbol in symbols}
            for future in concurrent.futures.as_completed(pending):
                symbol = pending[future]
                snapshots[symbol] = future.result()
    else:
        for symbol in symbols:
            snapshots[symbol] = fetch(symbol)

    missing_counts: dict[str, int] = {}
    for symbol in symbols:
        rows, cutoff_at = snapshots[symbol]
        unknown_exec_ids = audit_recent_trade_window(
            rows,
            anchor=trade_cursors[symbol],
            cutoff_at=cutoff_at,
            known_exec_ids=known_current_seq_exec_ids.get(symbol, set()),
        )
        missing_counts[symbol] = len(unknown_exec_ids)
    missing = {symbol: count for symbol, count in missing_counts.items() if count > 0}
    if missing:
        rendered = ",".join(f"{symbol}:{count}" for symbol, count in sorted(missing.items()))
        raise PublicTradeStreamBehind(
            f"PUBLIC_TRADE silence audit found missing exact trade(s): {rendered}"
        )
    return missing_counts


def _trade_cursor(raw: Mapping[str, object]) -> TradeCursor:
    try:
        return TradeCursor(
            symbol=str(raw["s"]).upper(),
            exec_id=str(raw["i"]),
            seq=int(str(raw["seq"])),
            traded_at=datetime.fromtimestamp(int(str(raw["T"])) / 1000, UTC),
        )
    except (KeyError, ValueError) as exc:
        raise ContinuityNotProvable("publicTrade row lacks exact execId/seq/time cursor") from exc


def _replay_trade_fact(row: ReplayTrade, received_at: datetime) -> MarketFactEnvelope:
    return MarketFactEnvelope(
        fact_id="trade-" + row.exec_id,
        event_kind="PUBLIC_TRADE",
        symbol=row.symbol,
        observed_at=row.traded_at,
        event_at=row.traded_at,
        received_at=received_at,
        source_refs=(f"bybit:publicTrade:{row.symbol}:{row.exec_id}",),
        attributes=FrozenPolicy.from_mapping(
            {"price": row.price, "size": row.size, "taker_side": row.side}
        ),
    )


def _candle_fact(candle: Candle, *, closed: bool, received_at: datetime) -> MarketFactEnvelope:
    boundary = candle.closed_at if closed else candle.opened_at
    kind = "CANDLE_CLOSED" if closed else "BAR_OPEN"
    topic = f"kline.{candle.timeframe}.{candle.symbol}"
    identity = fingerprint(
        {
            "topic": topic,
            "symbol": candle.symbol,
            "timeframe": candle.timeframe,
            "boundary": boundary,
            "closed": closed,
        }
    )[:32]
    if closed:
        attributes: dict[str, object] = {
            "timeframe": candle.timeframe,
            "opened_at": candle.opened_at.isoformat(),
            "closed_at": candle.closed_at.isoformat(),
            "open": str(candle.open),
            "high": str(candle.high),
            "low": str(candle.low),
            "close": str(candle.close),
            "volume": str(candle.volume),
        }
        observed_at = candle.closed_at
    else:
        attributes = {
            "opened_at": candle.opened_at.isoformat(),
            "open_price": str(candle.open),
        }
        # BAR_OPEN source semantics use receipt time as observed_at; event_at stays exact boundary.
        observed_at = received_at
    return MarketFactEnvelope(
        fact_id=f"{kind.lower()}-{identity}",
        event_kind=kind,
        symbol=candle.symbol,
        observed_at=observed_at,
        event_at=boundary,
        received_at=received_at,
        source_refs=(f"bybit:{topic}:{candle.symbol}:{candle.timeframe}:{boundary.isoformat()}",),
        attributes=FrozenPolicy.from_mapping(attributes),
    )


def _connect_and_subscribe(
    symbols: tuple[str, ...],
    intervals: tuple[str, ...] = ("5", "15", "60"),
) -> tuple[Any, datetime, datetime, tuple[dict[str, Any], ...]]:
    sock = websocket.create_connection(PUBLIC_WS, timeout=10.0, enable_multithread=False)
    with suppress(AttributeError):
        sock.settimeout(1.0)
    pending: set[str] = set()
    topics = _topics(symbols, intervals)
    for chunk_index, start in enumerate(range(0, len(topics), 40), start=1):
        req_id = f"u5-shadow-{chunk_index}"
        pending.add(req_id)
        sock.send(
            json.dumps(
                {
                    "op": "subscribe",
                    "req_id": req_id,
                    "args": list(topics[start : start + 40]),
                },
                separators=(",", ":"),
            )
        )
    buffered: list[dict[str, Any]] = []
    deadline = time.monotonic() + 10.0
    while pending:
        if time.monotonic() >= deadline:
            sock.close()
            raise ConnectionError("public subscription acknowledgement timeout")
        try:
            raw_message = sock.recv()
        except websocket.WebSocketTimeoutException:
            continue
        if raw_message in (None, ""):
            sock.close()
            raise websocket.WebSocketConnectionClosedException("public WebSocket closed")
        parsed = json.loads(raw_message)
        if not isinstance(parsed, dict):
            continue
        message = cast(dict[str, Any], parsed)
        if message.get("op") == "subscribe":
            if message.get("success") is False or message.get("retCode") not in (None, 0):
                sock.close()
                raise ConnectionError("U5 public subscription rejected")
            req_id = str(message.get("req_id") or "")
            pending.discard(req_id)
        elif message.get("op") == "pong" or message.get("ret_msg") == "pong":
            continue
        else:
            buffered.append(message)
    ready_local_at = datetime.now(UTC)
    ready_server_at = _fetch_server_time()
    return sock, ready_local_at, ready_server_at, tuple(buffered)


def _reconnect_until_oi_safe(
    symbols: tuple[str, ...],
    oi_cursors: Mapping[str, OiSampleCursor],
    *,
    connect_fn: Any | None = None,
    server_time_fn: Any | None = None,
    sleep_fn: Any | None = None,
) -> tuple[Any, datetime, datetime, tuple[dict[str, Any], ...]]:
    connect = _connect_and_subscribe if connect_fn is None else connect_fn
    server_time = _fetch_server_time if server_time_fn is None else server_time_fn
    sleeper = time.sleep if sleep_fn is None else sleep_fn
    transport_errors = (
        websocket.WebSocketConnectionClosedException,
        ConnectionError,
        OSError,
        ssl.SSLError,
    )
    while True:
        candidate_sock: Any | None = None
        try:
            candidate_sock, ready_local_at, ready_server_at, buffered = connect(symbols)
            verdict = assess_oi30s_continuity(
                oi_cursors,
                required_symbols=symbols,
                subscription_ready_server_at=ready_server_at,
                sample_seconds=OI_SAMPLE_SECONDS,
            )
            if not verdict.proven:
                with suppress(Exception):
                    candidate_sock.close()
                raise ContinuityNotProvable(verdict.reason)
            return candidate_sock, ready_local_at, ready_server_at, buffered
        except ContinuityNotProvable:
            raise
        except transport_errors as reconnect_exc:
            with suppress(Exception):
                if candidate_sock is not None:
                    candidate_sock.close()
            try:
                server_now = server_time()
            except Exception as time_exc:
                raise ContinuityNotProvable(
                    "cannot prove OI30S deadline while reconnecting: "
                    f"{type(time_exc).__name__}: {time_exc}"
                ) from time_exc
            verdict = assess_oi30s_continuity(
                oi_cursors,
                required_symbols=symbols,
                subscription_ready_server_at=server_now,
                sample_seconds=OI_SAMPLE_SECONDS,
            )
            if not verdict.proven:
                raise ContinuityNotProvable(
                    verdict.reason
                    + "; last reconnect error="
                    + f"{type(reconnect_exc).__name__}: {reconnect_exc}"
                ) from reconnect_exc
            sleeper(0.25)


def _mirror_topics(symbols: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"publicTrade.{symbol}" for symbol in symbols)


def _connect_public_trade_mirror(
    symbols: tuple[str, ...],
) -> tuple[Any, datetime, tuple[tuple[dict[str, Any], datetime], ...]]:
    sock = websocket.create_connection(PUBLIC_WS, timeout=10.0, enable_multithread=False)
    with suppress(AttributeError):
        sock.settimeout(1.0)
    req_id = "u5-trade-mirror"
    sock.send(
        json.dumps(
            {"op": "subscribe", "req_id": req_id, "args": list(_mirror_topics(symbols))},
            separators=(",", ":"),
        )
    )
    buffered: list[tuple[dict[str, Any], datetime]] = []
    deadline = time.monotonic() + 10.0
    while True:
        if time.monotonic() >= deadline:
            sock.close()
            raise ConnectionError("publicTrade mirror subscription acknowledgement timeout")
        try:
            raw_message = sock.recv()
        except websocket.WebSocketTimeoutException:
            continue
        received_at = datetime.now(UTC)
        if raw_message in (None, ""):
            sock.close()
            raise websocket.WebSocketConnectionClosedException("publicTrade mirror closed")
        parsed = json.loads(raw_message)
        if not isinstance(parsed, dict):
            continue
        message = cast(dict[str, Any], parsed)
        if message.get("op") == "subscribe" and str(message.get("req_id") or "") == req_id:
            if message.get("success") is False or message.get("retCode") not in (None, 0):
                sock.close()
                raise ConnectionError("publicTrade mirror subscription rejected")
            return sock, received_at, tuple(buffered)
        if message.get("op") == "pong" or message.get("ret_msg") == "pong":
            continue
        if str(message.get("topic") or "").startswith("publicTrade."):
            buffered.append((message, received_at))


def _run_public_trade_mirror(
    symbols: tuple[str, ...],
    mirror: PublicTradeMirrorBuffer,
    stop_event: threading.Event,
    ready_event: threading.Event,
) -> None:
    while not stop_event.is_set():
        sock: Any | None = None
        try:
            sock, ready_at, buffered = _connect_public_trade_mirror(symbols)
            mirror.start_epoch(ready_at)
            for buffered_message, buffered_received_at in buffered:
                mirror.record_message(
                    buffered_message,
                    received_at=buffered_received_at,
                )
            ready_event.set()
            heartbeat = PublicWsHeartbeat(
                PING_INTERVAL_SECONDS,
                PONG_TIMEOUT_SECONDS,
                started_monotonic=time.monotonic(),
            )
            while not stop_event.is_set():
                message: dict[str, Any] | None = None
                received_at = datetime.now(UTC)
                try:
                    raw_message = sock.recv()
                    received_at = datetime.now(UTC)
                    if raw_message in (None, ""):
                        raise websocket.WebSocketConnectionClosedException(
                            "publicTrade mirror closed"
                        )
                    parsed = json.loads(raw_message)
                    if isinstance(parsed, dict):
                        message = cast(dict[str, Any], parsed)
                except websocket.WebSocketTimeoutException:
                    pass
                now_monotonic = time.monotonic()
                if message is not None:
                    if message.get("op") == "pong" or message.get("ret_msg") == "pong":
                        heartbeat.note_pong(now_monotonic)
                    elif message.get("op") != "subscribe":
                        heartbeat.note_market_frame(now_monotonic)
                        if str(message.get("topic") or "").startswith("publicTrade."):
                            mirror.record_message(message, received_at=received_at)
                if heartbeat.deadline_exceeded(now_monotonic):
                    raise websocket.WebSocketConnectionClosedException(
                        "publicTrade mirror application heartbeat deadline exceeded"
                    )
                if heartbeat.ping_due(now_monotonic):
                    sock.send('{"op":"ping"}')
                    heartbeat.note_ping_sent(now_monotonic)
        except Exception as exc:
            mirror.mark_gap(f"{type(exc).__name__}: {exc}")
            if stop_event.wait(0.25):
                break
        finally:
            if sock is not None:
                with suppress(Exception):
                    sock.close()


def _transport_cursor_payload(
    *,
    trade_cursors: Mapping[str, TradeCursor],
    oi_cursors: Mapping[str, OiSampleCursor],
    closed_boundaries: Mapping[tuple[str, str], datetime],
    bar_open_boundaries: Mapping[str, datetime],
) -> dict[str, object]:
    return {
        "trade": {
            symbol: {
                "exec_id": cursor.exec_id,
                "seq": cursor.seq,
                "traded_at": cursor.traded_at.astimezone(UTC).isoformat(),
            }
            for symbol, cursor in sorted(trade_cursors.items())
        },
        "oi30s": {
            symbol: {
                "observed_at": cursor.observed_at.astimezone(UTC).isoformat(),
                "ticker_cs": cursor.ticker_cross_sequence,
            }
            for symbol, cursor in sorted(oi_cursors.items())
        },
        "closed": {
            f"{symbol}:{timeframe}": boundary.astimezone(UTC).isoformat()
            for (symbol, timeframe), boundary in sorted(closed_boundaries.items())
        },
        "bar_open": {
            symbol: boundary.astimezone(UTC).isoformat()
            for symbol, boundary in sorted(bar_open_boundaries.items())
        },
    }


def _record_transport_event(
    connection: Any,
    identity: ShadowRunIdentity,
    *,
    category: str,
    occurred_at: datetime,
    payload: Mapping[str, object],
) -> None:
    rendered = dict(payload)
    causal_key = (
        f"{category}:{occurred_at.astimezone(UTC).isoformat()}:" + fingerprint(rendered)[:16]
    )
    event_id = "spe-" + fingerprint({"run": identity.parity_run_id, "causal_key": causal_key})[:32]
    encoded = json.dumps(
        rendered, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    connection.execute(
        """INSERT INTO strategy_entry.shadow_parity_events(
               parity_event_id,parity_run_id,causal_key,event_at,category,
               observed_at,source_refs,strategy_config_fingerprint,
               entry_plan_fingerprint,legacy_payload,universal_payload,
               equivalent,difference
           ) VALUES(%s,%s,%s,%s,%s,%s,'[]'::jsonb,%s,%s,%s::jsonb,%s::jsonb,true,'{}'::jsonb)
           ON CONFLICT(parity_run_id,causal_key) DO NOTHING""",
        (
            event_id,
            identity.parity_run_id,
            causal_key,
            occurred_at,
            category,
            occurred_at,
            identity.strategy_config_fingerprint,
            identity.entry_plan_fingerprint,
            encoded,
            encoded,
        ),
    )


def _fetch_gap_candles(
    symbol: str,
    timeframe: str,
    ready_server_at: datetime,
) -> tuple[Candle, ...]:
    payload = _get_json(
        "/v5/market/kline",
        {
            "category": "linear",
            "symbol": symbol,
            "interval": timeframe,
            "limit": 10,
        },
    )
    rows = map_rest_klines(
        cast(list[list[Any]], _result_list(payload)),
        symbol=symbol,
        interval=timeframe,
        observed_at=ready_server_at,
    )
    return tuple(rows)


def _recover_public_gap(
    *,
    symbols: tuple[str, ...],
    ready_local_at: datetime,
    ready_server_at: datetime,
    trade_cursors: Mapping[str, TradeCursor],
    oi_cursors: Mapping[str, OiSampleCursor],
    closed_boundaries: Mapping[tuple[str, str], datetime],
    bar_open_boundaries: Mapping[str, datetime],
    known_trade_exec_ids: Mapping[str, set[str]] | None = None,
    trade_replay_override: tuple[tuple[MarketFactEnvelope, TradeCursor], ...] | None = None,
    intervals: tuple[str, ...] = ("5", "15", "60"),
) -> tuple[tuple[tuple[MarketFactEnvelope, TradeCursor | None], ...], dict[str, int]]:
    if FACT_SOURCE_ID != REST_OI30S_SOURCE_ID:
        oi_verdict = assess_oi30s_continuity(
            oi_cursors,
            required_symbols=symbols,
            subscription_ready_server_at=ready_server_at,
            sample_seconds=OI_SAMPLE_SECONDS,
        )
        if resolve_continuity_action(oi_verdict) is ContinuityAction.NOT_COMPARABLE:
            raise ContinuityNotProvable(oi_verdict.reason)

    replay: list[tuple[MarketFactEnvelope, TradeCursor | None]] = []
    counts = {"PUBLIC_TRADE": 0, "CANDLE_CLOSED": 0, "BAR_OPEN": 0, "OPEN_INTEREST": 0}
    if trade_replay_override is not None:
        replay.extend(trade_replay_override)
        counts["PUBLIC_TRADE"] = len(trade_replay_override)
    for symbol in symbols:
        if trade_replay_override is None:
            anchor = trade_cursors.get(symbol)
            if anchor is None:
                raise ContinuityNotProvable(f"exact PUBLIC_TRADE cursor missing for {symbol}")
            payload = _get_json(
                "/v5/market/recent-trade",
                {"category": "linear", "symbol": symbol, "limit": 1000},
            )
            raw_rows = [row for row in _result_list(payload) if isinstance(row, Mapping)]
            rows = build_exact_trade_replay(
                cast(list[Mapping[str, object]], raw_rows),
                anchor=anchor,
                cutoff_at=ready_server_at,
                known_exec_ids=(
                    set()
                    if known_trade_exec_ids is None
                    else known_trade_exec_ids.get(symbol, set())
                ),
            )
            for row in rows:
                cursor = TradeCursor(row.symbol, row.exec_id, row.seq, row.traded_at)
                replay.append((_replay_trade_fact(row, ready_local_at), cursor))
                counts["PUBLIC_TRADE"] += 1

        cached: dict[str, tuple[Candle, ...]] = {}
        for timeframe in intervals:
            last_closed = closed_boundaries.get((symbol, timeframe))
            if last_closed is None:
                raise ContinuityNotProvable(
                    f"exact CANDLE_CLOSED cursor missing for {symbol}:{timeframe}"
                )
            expected = expected_boundaries(
                last_closed,
                ready_server_at,
                timeframe_minutes=int(timeframe),
            )
            candles = _fetch_gap_candles(symbol, timeframe, ready_server_at)
            cached[timeframe] = candles
            by_closed = {row.closed_at: row for row in candles if row.is_closed}
            missing = [boundary for boundary in expected if boundary not in by_closed]
            if missing:
                raise ContinuityNotProvable(
                    f"exact closed-candle boundary missing for {symbol}:{timeframe}:"
                    + ",".join(item.isoformat() for item in missing)
                )
            for boundary in expected:
                replay.append(
                    (
                        _candle_fact(by_closed[boundary], closed=True, received_at=ready_local_at),
                        None,
                    )
                )
                counts["CANDLE_CLOSED"] += 1

        last_open = bar_open_boundaries.get(symbol)
        if last_open is None:
            raise ContinuityNotProvable(f"exact BAR_OPEN cursor missing for {symbol}")
        expected_open = expected_boundaries(last_open, ready_server_at, timeframe_minutes=5)
        if "5" not in cached:
            raise ContinuityNotProvable("BAR_OPEN recovery requires 5m transport")
        by_open = {row.opened_at: row for row in cached["5"]}
        missing_open = [boundary for boundary in expected_open if boundary not in by_open]
        if missing_open:
            raise ContinuityNotProvable(
                f"exact BAR_OPEN boundary missing for {symbol}:"
                + ",".join(item.isoformat() for item in missing_open)
            )
        for boundary in expected_open:
            replay.append(
                (_candle_fact(by_open[boundary], closed=False, received_at=ready_local_at), None)
            )
            counts["BAR_OPEN"] += 1

    priority = {"CANDLE_CLOSED": 0, "BAR_OPEN": 1, "PUBLIC_TRADE": 2, "OPEN_INTEREST": 3}
    replay.sort(
        key=lambda item: (
            (
                item[0].received_at
                if trade_replay_override is not None and item[0].event_kind == "PUBLIC_TRADE"
                else item[0].event_at
            ),
            priority[item[0].event_kind],
            -int(str(item[0].attributes.to_dict().get("timeframe") or 0)),
        )
    )
    return tuple(replay), counts


def _merge_gap_oi_without_reordering_replay(
    replay_items: tuple[tuple[MarketFactEnvelope, TradeCursor | None], ...],
    oi_facts: list[MarketFactEnvelope],
) -> list[tuple[MarketFactEnvelope, TradeCursor | None]]:
    """Insert exact OI facts by receipt time without changing proven replay order."""

    combined = list(replay_items)
    for fact in sorted(oi_facts, key=lambda item: (item.received_at, item.fact_id)):
        index = 0
        while index < len(combined) and combined[index][0].received_at <= fact.received_at:
            index += 1
        combined.insert(index, (fact, None))
    return combined


def _status_payload(
    *,
    identity: ShadowRunIdentity,
    state: ShadowComparability,
    gate: ShadowComparabilityGate,
    comparator: OnlineParityComparator,
    facts_received: int,
    seeded_symbols: int,
    total_symbols: int,
) -> dict[str, object]:
    return {
        "updated_at": datetime.now(UTC).isoformat(),
        "service": "cripta-universal-entry-shadow.service",
        "trading_effect": "NONE",
        "parity_run_id": identity.parity_run_id,
        "state": state.value,
        "state_reason": gate.reason,
        "source_commit": identity.universal_source_commit,
        "baseline_commit": identity.baseline_source_commit,
        "strategy_config_fingerprint": identity.strategy_config_fingerprint,
        "entry_plan_fingerprint": identity.entry_plan_fingerprint,
        "calibration_sha256": identity.calibration_sha256,
        "calibration_size": identity.calibration_size,
        "fact_source_id": identity.fact_source_id,
        "facts_received": facts_received,
        "seeded_symbols": seeded_symbols,
        "total_symbols": total_symbols,
        **comparator.summary(),
    }


def _observer_intervals(bundles: tuple[object, ...]) -> tuple[str, ...]:
    intervals: set[str] = {"5"}
    for raw_bundle in bundles:
        plan = cast(Any, raw_bundle).entry_plan
        watch = plan.watch_policy.to_dict()
        for value in watch.get("required_closed_timeframes", []):
            intervals.add(str(value))
        geometry = watch.get("geometry")
        if isinstance(geometry, Mapping):
            for value in geometry.get("timeframes", []):
                intervals.add(str(value))
        swing = watch.get("hourly_swing")
        if isinstance(swing, Mapping) and bool(swing.get("enabled", False)):
            intervals.add(str(swing.get("timeframe") or ""))
        local = plan.local_entry_policy.to_dict()
        if bool(local.get("enabled", False)):
            intervals.update(("5", "15"))
            if bool(local.get("use_1m", False)):
                intervals.add("1")
    intervals.discard("")
    return tuple(sorted(intervals, key=lambda value: int(value)))


def _observer_light_signature(connection: Any) -> tuple[tuple[str, ...], ...]:
    rows = connection.execute(
        """SELECT a.activation_id,a.strategy_id,a.strategy_version,
                  a.strategy_config_fingerprint,a.updated_at,
                  ep.entry_plan_fingerprint,xp.exit_plan_fingerprint
             FROM strategy_entry.strategy_activations a
             JOIN strategy_entry.entry_plans ep
               ON ep.strategy_id=a.strategy_id
              AND ep.strategy_version=a.strategy_version
              AND ep.strategy_config_fingerprint=a.strategy_config_fingerprint
             JOIN strategy_entry.exit_plans xp
               ON xp.strategy_id=a.strategy_id
              AND xp.strategy_version=a.strategy_version
              AND xp.strategy_config_fingerprint=a.strategy_config_fingerprint
            WHERE a.enabled=true
            ORDER BY a.activation_id,ep.entry_plan_fingerprint,xp.exit_plan_fingerprint"""
    ).fetchall()
    return tuple(tuple(str(item) for item in row) for row in rows)


def _observer_objective_inputs(
    connection: Any,
    symbols: tuple[str, ...],
    observed_at: datetime,
) -> tuple[ObjectiveContext | None, dict[str, ObjectiveContext], TradingCapacitySnapshot | None]:
    global_row = connection.execute(
        """SELECT global_context_id,observed_at,data_quality,payload,source_market_context_id
             FROM dispatcher_v2.global_market_contexts
            WHERE observed_at <= %s
            ORDER BY observed_at DESC LIMIT 1""",
        (observed_at,),
    ).fetchone()
    global_context: ObjectiveContext | None = None
    if global_row is not None:
        global_context = ObjectiveContext(
            context_id=str(global_row[0]),
            context_type="DISPATCHER_GLOBAL",
            observed_at=cast(datetime, global_row[1]).astimezone(UTC),
            quality=DataQuality(str(global_row[2])),
            completeness="COMPLETE",
            payload=FrozenPolicy.from_mapping(cast(Mapping[str, object], global_row[3])),
            source_refs=(f"dispatcher-global:{global_row[0]}", f"mayak:{global_row[4]}"),
        )
    coins: dict[str, ObjectiveContext] = {}
    if symbols:
        coin_rows = connection.execute(
            """SELECT DISTINCT ON (symbol)
                      symbol,coin_context_id,observed_at,data_quality,payload,source_coin_context_id
                 FROM dispatcher_v2.coin_market_contexts
                WHERE symbol = ANY(%s) AND observed_at <= %s
                ORDER BY symbol,observed_at DESC""",
            (list(symbols), observed_at),
        ).fetchall()
        for row in coin_rows:
            coins[str(row[0])] = ObjectiveContext(
                context_id=str(row[1]),
                context_type="DISPATCHER_COIN",
                observed_at=cast(datetime, row[2]).astimezone(UTC),
                quality=DataQuality(str(row[3])),
                completeness="COMPLETE",
                payload=FrozenPolicy.from_mapping(cast(Mapping[str, object], row[4])),
                source_refs=(f"dispatcher-coin:{row[1]}", f"mayak-coin:{row[5]}"),
            )
    capacity_row = connection.execute(
        """SELECT capacity_snapshot_id,observed_at,available_for_new_trading,
                  data_quality,source_account_ref
             FROM dispatcher_v2.trading_capacity_snapshots
            WHERE observed_at <= %s
            ORDER BY observed_at DESC LIMIT 1""",
        (observed_at,),
    ).fetchone()
    capacity: TradingCapacitySnapshot | None = None
    if capacity_row is not None:
        capacity = TradingCapacitySnapshot(
            capacity_snapshot_id=str(capacity_row[0]),
            observed_at=cast(datetime, capacity_row[1]).astimezone(UTC),
            available_for_new_trading=(
                None if capacity_row[2] is None else Decimal(str(capacity_row[2]))
            ),
            quality=DataQuality(str(capacity_row[3])),
            source_ref=str(capacity_row[4]),
        )
    return global_context, coins, capacity


def _observer_status(
    *,
    state: str,
    epoch_id: str | None,
    active_bundles: tuple[object, ...],
    symbols: tuple[str, ...],
    facts_received: int = 0,
    evaluations: int = 0,
    signals: int = 0,
    warmup_until: datetime | None = None,
    reason: str = "",
) -> None:
    _atomic_json(
        OBSERVER_STATUS_PATH,
        {
            "updated_at": datetime.now(UTC).isoformat(),
            "service": "cripta-universal-entry-observer.service",
            "runtime_mode": "MULTI_STRATEGY_OBSERVER",
            "state": state,
            "reason": reason,
            "observer_ready": state in {"IDLE", "WARMUP", "RUNNING", "RELOADING"},
            "trading_effect": "NONE",
            "source_commit": LOADED_COMMIT,
            "fact_source_id": FACT_SOURCE_ID,
            "observer_epoch_id": epoch_id,
            "active_strategies": len(active_bundles),
            "active_entry_plans": [
                cast(Any, item).entry_plan.entry_plan_fingerprint for item in active_bundles
            ],
            "active_exit_plans": [
                cast(Any, item).exit_plan.exit_plan_fingerprint for item in active_bundles
            ],
            "symbols": list(symbols),
            "facts_received": facts_received,
            "evaluations": evaluations,
            "signals": signals,
            "warmup_until": None if warmup_until is None else warmup_until.isoformat(),
        },
    )


def _run_observer_epoch(
    connection: Any,
    bundles: tuple[object, ...],
    stopping: Callable[[], bool],
) -> str:
    epoch_started = datetime.now(UTC)
    signature = _observer_light_signature(connection)
    if not signature:
        return "RELOAD"
    registry = ActivePlanRegistry()
    for raw_bundle in bundles:
        bundle = cast(Any, raw_bundle)
        registry.register_card(bundle.card)
        materialized = registry.activate(bundle.activation)
        if materialized.entry_plan_fingerprint != bundle.entry_plan.entry_plan_fingerprint:
            raise RuntimeError("observer materialized EntryPlan fingerprint mismatch")
    engine = UniversalEntryEngine(registry)
    store = StrategyEntryStore(connection)
    paper = PaperTradeRuntime(connection)
    bundle_by_entry_plan = {
        cast(Any, item).entry_plan.entry_plan_fingerprint: cast(Any, item) for item in bundles
    }
    symbols = tuple(
        sorted({symbol for bundle in cast(Any, bundles) for symbol in bundle.card.symbols})
    )
    intervals = _observer_intervals(bundles)
    if "5" not in intervals:
        raise RuntimeError("production observer requires 5m candidate transport")
    epoch_id = (
        "ueo-"
        + fingerprint(
            {
                "started_at": epoch_started,
                "plans": [
                    cast(Any, bundle).entry_plan.entry_plan_fingerprint for bundle in bundles
                ],
                "source_commit": LOADED_COMMIT,
            }
        )[:32]
    )
    warmup_seconds = max(
        (
            derive_unknown_prestart_horizon_seconds(cast(Any, bundle).entry_plan)
            if cast(Any, bundle).activation.enabled_at < epoch_started - timedelta(seconds=5)
            else 0
        )
        for bundle in bundles
    )
    warmup_until = epoch_started + timedelta(seconds=warmup_seconds)
    required_flow_symbols = {
        symbol
        for bundle in cast(Any, bundles)
        if bool(bundle.entry_plan.watch_policy.to_dict().get("flow", {}).get("enabled", False))
        for symbol in bundle.entry_plan.symbols
    }
    required_oi_symbols = {
        symbol
        for bundle in cast(Any, bundles)
        if bool(bundle.entry_plan.watch_policy.to_dict().get("oi", {}).get("enabled", False))
        for symbol in bundle.entry_plan.symbols
    }

    closed_boundaries: dict[tuple[str, str], datetime] = {}
    for symbol in symbols:
        observed = datetime.now(UTC)
        history = _fetch_history(symbol, observed, intervals)
        oi_history = _fetch_oi_history(symbol) if required_oi_symbols else ()
        for timeframe in intervals:
            rows = history.get(timeframe, ())
            if not rows:
                raise RuntimeError(f"observer causal history seed missing {symbol}:{timeframe}")
            closed_boundaries[(symbol, timeframe)] = max(item.closed_at for item in rows)
        engine.load_watch_history(
            symbol,
            {key: tuple(value) for key, value in history.items()},
            tuple(oi_history),
            observed_at=observed,
        )
        time.sleep(0.05)

    flow_minutes: dict[str, set[datetime]] = {symbol: set() for symbol in symbols}
    oi_seen: set[str] = set()
    trade_cursors: dict[str, TradeCursor] = {}
    trade_current_seq_exec_ids: dict[str, set[str]] = {}
    trade_proof_monotonic: dict[str, float] = {}
    deduper = ExactFactDeduper()
    oi_config = Oi30sConfig(
        fact_source_id=FACT_SOURCE_ID,
        required_symbols=symbols,
        slot_seconds=30,
        request_timeout_seconds=5.0,
        retry_interval_seconds=0.25,
    )
    oi_health = Oi30sHealthTracker(oi_config)
    oi_results: queue.Queue[OiSlotResult] = queue.Queue()
    oi_stop = threading.Event()
    trade_mirror = PublicTradeMirrorBuffer(
        tuple(symbols),
        retention_seconds=MIRROR_RETENTION_SECONDS,
    )
    trade_mirror_stop = threading.Event()
    trade_mirror_ready = threading.Event()
    trade_mirror_thread = threading.Thread(
        target=_run_public_trade_mirror,
        args=(tuple(symbols), trade_mirror, trade_mirror_stop, trade_mirror_ready),
        name="universal-entry-observer-public-trade-mirror",
        daemon=True,
    )
    oi_thread = threading.Thread(
        target=_run_rest_oi30s_worker,
        args=(oi_config, oi_results, oi_stop),
        name="universal-entry-observer-oi30s",
        daemon=True,
    )
    sock: Any | None = None
    facts_received = 0
    evaluations_count = 0
    signals_count = 0
    last_inputs_refresh = 0.0
    global_context: ObjectiveContext | None = None
    coin_contexts: dict[str, ObjectiveContext] = {}
    capacity: TradingCapacitySnapshot | None = None
    current_signature = signature
    provenance = FrozenPolicy.from_mapping(
        {
            "source": "universal_entry_multi_strategy_observer",
            "observer_epoch_id": epoch_id,
            "source_commit": LOADED_COMMIT,
            "fact_source_id": FACT_SOURCE_ID,
        }
    )

    def sensor_ready(now: datetime) -> bool:
        flow_ready = all(len(flow_minutes[symbol]) >= 5 for symbol in required_flow_symbols)
        oi_ready = required_oi_symbols.issubset(oi_seen)
        return now >= warmup_until and flow_ready and oi_ready

    def refresh_inputs(fact_at: datetime) -> None:
        nonlocal last_inputs_refresh, global_context, coin_contexts, capacity
        now_mono = time.monotonic()
        if now_mono - last_inputs_refresh < 1.0:
            return
        global_context, coin_contexts, capacity = _observer_objective_inputs(
            connection, symbols, fact_at
        )
        last_inputs_refresh = now_mono

    def process_fact(fact: MarketFactEnvelope, cursor: TradeCursor | None = None) -> None:
        nonlocal facts_received, evaluations_count, signals_count
        if not deduper.accept(fact.fact_id):
            return
        if fact.event_kind == "PUBLIC_TRADE":
            if cursor is None:
                raise ContinuityNotProvable("observer PUBLIC_TRADE lacks exact cursor")
            previous = trade_cursors.get(fact.symbol)
            if previous is not None and cursor.seq < previous.seq:
                raise ContinuityNotProvable(
                    f"observer PUBLIC_TRADE sequence regressed for {fact.symbol}"
                )
            if previous is None or cursor.seq > previous.seq:
                trade_current_seq_exec_ids[fact.symbol] = {cursor.exec_id}
            else:
                trade_current_seq_exec_ids.setdefault(fact.symbol, set()).add(cursor.exec_id)
            trade_cursors[fact.symbol] = cursor
            trade_proof_monotonic[fact.symbol] = time.monotonic()
            flow_minutes[fact.symbol].add(
                fact.observed_at.astimezone(UTC).replace(second=0, microsecond=0)
            )
            cutoff = fact.observed_at - timedelta(minutes=10)
            flow_minutes[fact.symbol] = {
                minute for minute in flow_minutes[fact.symbol] if minute >= cutoff
            }
        elif fact.event_kind == "OPEN_INTEREST":
            oi_seen.add(fact.symbol)
        elif fact.event_kind == "CANDLE_CLOSED":
            attrs = fact.attributes.to_dict()
            timeframe = str(attrs.get("timeframe") or "")
            boundary = datetime.fromisoformat(str(attrs["closed_at"])).astimezone(UTC)
            prior = closed_boundaries.get((fact.symbol, timeframe))
            if prior is not None and boundary < prior:
                raise ContinuityNotProvable(
                    f"observer CANDLE_CLOSED regressed for {fact.symbol}:{timeframe}"
                )
            closed_boundaries[(fact.symbol, timeframe)] = boundary
        refresh_inputs(fact.observed_at)
        contexts: dict[str, ObjectiveContext] = {}
        if global_context is not None and global_context.observed_at <= fact.observed_at:
            contexts["dispatcher.global"] = global_context
        coin = coin_contexts.get(fact.symbol)
        if coin is not None and coin.observed_at <= fact.observed_at:
            contexts["dispatcher.coin"] = coin
        if fact.event_kind == "PUBLIC_TRADE":
            attrs = fact.attributes.to_dict()
            paper.on_public_trade(
                symbol=fact.symbol,
                price=Decimal(str(attrs["price"])),
                observed_at=fact.observed_at,
                contexts=contexts,
            )
        readiness = TechnicalReadiness(
            True,
            fact.observed_at,
            "multi-Strategy observer causal transport is continuous",
        )
        evaluations = engine.process(
            fact,
            contexts=contexts,
            capacity=capacity,
            technical_readiness=readiness,
            account_ref="BYBIT:UNIFIED",
            allow_new_signals=sensor_ready(fact.observed_at),
        )
        facts_received += 1
        for evaluation in evaluations:
            store.record_evaluation(evaluation, provenance=provenance)
            if evaluation.execution_request is not None:
                bundle = bundle_by_entry_plan.get(
                    evaluation.execution_request.entry_plan_fingerprint
                )
                if bundle is None:
                    raise RuntimeError("paper runtime exact EntryPlan bundle is missing")
                paper.create_order(evaluation, bundle, now=fact.observed_at)
            evaluations_count += 1
            signals_count += 1

    try:
        trade_mirror_thread.start()
        if not trade_mirror_ready.wait(MIRROR_READY_TIMEOUT_SECONDS):
            raise ContinuityNotProvable(
                "observer publicTrade mirror did not become ready before startup deadline"
            )
        if trade_mirror.snapshot()["state"] != "ACTIVE":
            raise ContinuityNotProvable("observer publicTrade mirror continuity is not active")
        oi_thread.start()
        sock, _ready_local, _ready_server, buffered = _connect_and_subscribe(symbols, intervals)
        buffered_messages = list(buffered)
        heartbeat = PublicWsHeartbeat(
            PING_INTERVAL_SECONDS,
            PONG_TIMEOUT_SECONDS,
            started_monotonic=time.monotonic(),
        )
        next_status = 0.0
        next_signature = 0.0
        _observer_status(
            state="WARMUP" if not sensor_ready(datetime.now(UTC)) else "RUNNING",
            epoch_id=epoch_id,
            active_bundles=bundles,
            symbols=symbols,
            warmup_until=warmup_until,
            reason="causal seed complete",
        )
        while not stopping():
            now_mono = time.monotonic()
            if now_mono >= next_signature:
                latest_signature = _observer_light_signature(connection)
                if latest_signature != current_signature:
                    _observer_status(
                        state="RELOADING",
                        epoch_id=epoch_id,
                        active_bundles=bundles,
                        symbols=symbols,
                        facts_received=facts_received,
                        evaluations=evaluations_count,
                        signals=signals_count,
                        warmup_until=warmup_until,
                        reason="StrategyActivation set changed",
                    )
                    return "RELOAD"
                next_signature = now_mono + 1.0
            stale = tuple(
                symbol
                for symbol, cursor in trade_cursors.items()
                if now_mono - trade_proof_monotonic.get(symbol, now_mono)
                >= TRADE_SILENCE_AUDIT_SECONDS
            )
            if stale:
                try:
                    _audit_public_trade_silence(stale, trade_cursors, trade_current_seq_exec_ids)
                except PublicTradeStreamBehind:
                    cutoff_at = datetime.now(UTC)
                    recovered = trade_mirror.recover_after(
                        trade_cursors,
                        cutoff_at=cutoff_at,
                    )
                    if not recovered:
                        raise ContinuityNotProvable(
                            "observer mirror recovery returned no exact PUBLIC_TRADE events"
                        )
                    for event in recovered:
                        if event.symbol not in stale:
                            continue
                        process_fact(
                            _replay_trade_fact(event.as_replay_trade(), event.received_at),
                            TradeCursor(
                                event.symbol,
                                event.exec_id,
                                event.seq,
                                event.traded_at,
                            ),
                        )
                proven_at = time.monotonic()
                for symbol in stale:
                    trade_proof_monotonic[symbol] = proven_at
            while True:
                try:
                    oi_result = oi_results.get_nowait()
                except queue.Empty:
                    break
                oi_health.accept(oi_result)
                if oi_result.state is not OiSlotState.COMPLETE:
                    raise ContinuityNotProvable(
                        f"observer OI30S slot {oi_result.slot_id} is {oi_result.state.value}"
                    )
                for fact in oi_result.facts:
                    process_fact(fact)
            message: dict[str, Any] | None = None
            received_at = datetime.now(UTC)
            if buffered_messages:
                message = buffered_messages.pop(0)
            else:
                try:
                    raw = sock.recv()
                    received_at = datetime.now(UTC)
                    if raw in (None, ""):
                        raise websocket.WebSocketConnectionClosedException(
                            "observer public WebSocket closed"
                        )
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        message = cast(dict[str, Any], parsed)
                except websocket.WebSocketTimeoutException:
                    pass
            now_mono = time.monotonic()
            if message is not None:
                if message.get("op") == "pong" or message.get("ret_msg") == "pong":
                    heartbeat.note_pong(now_mono)
                elif message.get("op") != "subscribe":
                    heartbeat.note_market_frame(now_mono)
                topic = str(message.get("topic") or "")
                if topic.startswith("publicTrade."):
                    data = message.get("data")
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, Mapping):
                                symbol = str(item.get("s") or "").upper()
                                if symbol in symbols:
                                    process_fact(
                                        _trade_fact(item, received_at),
                                        _trade_cursor(item),
                                    )
                elif topic.startswith("kline."):
                    for fact in _kline_facts(message, received_at):
                        if fact.symbol in symbols:
                            process_fact(fact)
            if heartbeat.deadline_exceeded(now_mono):
                raise ContinuityNotProvable("observer application heartbeat deadline exceeded")
            if heartbeat.ping_due(now_mono):
                sock.send('{"op":"ping"}')
                heartbeat.note_ping_sent(now_mono)
            if now_mono >= next_status:
                running = sensor_ready(datetime.now(UTC))
                _observer_status(
                    state="RUNNING" if running else "WARMUP",
                    epoch_id=epoch_id,
                    active_bundles=bundles,
                    symbols=symbols,
                    facts_received=facts_received,
                    evaluations=evaluations_count,
                    signals=signals_count,
                    warmup_until=warmup_until,
                    reason=(
                        "all active StrategyPlans consume one causal fact stream"
                        if running
                        else "waiting for plan-owned pre-start influence/sensor completeness"
                    ),
                )
                next_status = now_mono + 2.0
        return "STOP"
    finally:
        oi_stop.set()
        oi_thread.join(timeout=2.0)
        trade_mirror_stop.set()
        trade_mirror_thread.join(timeout=2.0)
        if sock is not None:
            with suppress(Exception):
                sock.close()


def _run_multi_strategy_observer() -> None:
    if not LOADED_COMMIT:
        raise RuntimeError("CRIPTA_U5_LOADED_COMMIT is required")
    if FACT_SOURCE_ID != REST_OI30S_SOURCE_ID or OI_SAMPLE_SECONDS != 30:
        raise RuntimeError(
            "multi-Strategy observer requires BYBIT_PUBLIC_REST_CURRENT_OI_30S_V1 at 30s"
        )
    OBSERVER_STATE_ROOT.mkdir(parents=True, exist_ok=True)
    stopping_flag = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping_flag
        stopping_flag = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with psycopg.connect(DATABASE_DSN, autocommit=True) as connection:
        while not stopping_flag:
            try:
                bundles = load_active_strategy_bundles(connection)
                if not bundles:
                    _observer_status(
                        state="IDLE",
                        epoch_id=None,
                        active_bundles=(),
                        symbols=(),
                        reason="no enabled StrategyActivation",
                    )
                    time.sleep(1.0)
                    continue
                outcome = _run_observer_epoch(
                    connection, cast(tuple[object, ...], bundles), lambda: stopping_flag
                )
                if outcome == "STOP":
                    break
            except Exception as exc:
                _observer_status(
                    state="ERROR",
                    epoch_id=None,
                    active_bundles=(),
                    symbols=(),
                    reason=f"{type(exc).__name__}: {exc}",
                )
                if stopping_flag:
                    break
                time.sleep(2.0)
    _observer_status(
        state="STOPPED",
        epoch_id=None,
        active_bundles=(),
        symbols=(),
        reason="service stopped",
    )


def _run_parity_main() -> None:
    if not LOADED_COMMIT:
        raise RuntimeError("CRIPTA_U5_LOADED_COMMIT is required")
    if OI_SAMPLE_SECONDS <= 0:
        raise RuntimeError("CRIPTA_U5_OI_SAMPLE_SECONDS must be positive")
    use_rest_oi30s = FACT_SOURCE_ID == REST_OI30S_SOURCE_ID
    if use_rest_oi30s and OI_SAMPLE_SECONDS != 30:
        raise RuntimeError("REST current OI source requires exact 30-second slots")
    bundle = load_v1_compatibility_bundle(PROJECT_ROOT)
    plan, _ = materialize_plans(bundle.card, bundle.activation)
    calibration_path = PROJECT_ROOT / bundle.calibration_relative_path
    calibration_size = calibration_path.stat().st_size
    if calibration_size != 4647:
        raise RuntimeError("frozen calibration size does not match published U4 evidence")
    symbols = bundle.card.symbols
    if len(symbols) != 10:
        raise RuntimeError("U5 frozen V1 Strategy scope must contain exactly 10 symbols")

    started_at = datetime.now(UTC)
    service_instance_id = f"{socket.gethostname()}:{os.getpid()}:{started_at.isoformat()}"
    parity_run_id = (
        "spr-"
        + fingerprint(
            {
                "strategy": bundle.card.strategy_config_fingerprint,
                "plan": plan.entry_plan_fingerprint,
                "service": service_instance_id,
                "started_at": started_at,
            }
        )[:32]
    )
    identity = ShadowRunIdentity(
        parity_run_id=parity_run_id,
        strategy_id=bundle.card.strategy_id,
        strategy_version=bundle.card.strategy_version,
        strategy_config_fingerprint=bundle.card.strategy_config_fingerprint,
        entry_plan_fingerprint=plan.entry_plan_fingerprint,
        calibration_sha256=bundle.calibration_sha256,
        calibration_size=calibration_size,
        baseline_source_commit=BASELINE_COMMIT,
        universal_source_commit=LOADED_COMMIT,
        fact_source_id=FACT_SOURCE_ID,
        service_instance_id=service_instance_id,
        started_at=started_at,
    )
    gate = ShadowComparabilityGate(
        started_at,
        required_warmup_seconds=derive_unknown_prestart_horizon_seconds(plan),
    )
    journal = DurableFactJournal(JOURNAL_PATH)
    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    connection = psycopg.connect(DATABASE_DSN, autocommit=True)
    store = ShadowParityStore(connection)
    store.ensure_policy_identity(bundle.card, plan)
    recovered_run_id = store.finalize_unfinished_run_for_restart(
        strategy_config_fingerprint=bundle.card.strategy_config_fingerprint,
        entry_plan_fingerprint=plan.entry_plan_fingerprint,
        fact_source_id=FACT_SOURCE_ID,
        occurred_at=started_at,
    )
    initial_summary: dict[str, object] = {
        "trading_effect": "NONE",
        "state": "WARMUP",
        "reason": "service startup",
        "recovered_unfinished_run_id": recovered_run_id,
        "symbols": list(symbols),
        "facts_received": 0,
        "comparable_events": 0,
        "matched_events": 0,
        "mismatches_by_category": {},
        "transport_reconnects": 0,
    }
    store.start_run(identity, summary=initial_summary)

    runners: dict[str, V1DeterministicParityRunner] = {}
    sock: Any | None = None
    oi_stop_event: threading.Event | None = None
    oi_thread: threading.Thread | None = None
    trade_mirror_stop: threading.Event | None = None
    trade_mirror_thread: threading.Thread | None = None
    try:
        closed_boundaries: dict[tuple[str, str], datetime] = {}
        for symbol in symbols:
            if stopping:
                break
            observed_at = datetime.now(UTC)
            history = _fetch_history(symbol, observed_at)
            oi_history = _fetch_oi_history(symbol)
            runner_history: dict[str, tuple[object, ...]] = {
                timeframe: tuple(rows) for timeframe, rows in history.items()
            }
            for timeframe, rows in history.items():
                if not rows:
                    raise RuntimeError(f"causal history seed missing {symbol}:{timeframe}")
                closed_boundaries[(symbol, timeframe)] = max(row.closed_at for row in rows)
            runners[symbol] = V1DeterministicParityRunner(
                bundle,
                symbol=symbol,
                candles=runner_history,
                oi_points=tuple(oi_history),
                observed_at=observed_at,
            )
        if len(runners) != len(symbols):
            raise RuntimeError("causal history seed incomplete")
        gate.mark_seed_complete()
        comparator = OnlineParityComparator(identity, runners)
        state = gate.state_at(datetime.now(UTC))
        last_state = state
        facts_received = 0
        flow_minutes: dict[str, set[datetime]] = {symbol: set() for symbol in symbols}
        last_oi_sample: dict[str, datetime] = {}
        trade_cursors: dict[str, TradeCursor] = {}
        trade_current_seq_exec_ids: dict[str, set[str]] = {}
        trade_proof_monotonic: dict[str, float] = {}
        oi_cursors: dict[str, OiSampleCursor] = {}
        bar_open_boundaries: dict[str, datetime] = {}
        deduper = ExactFactDeduper()
        transport_reconnects = 0
        trade_audits = 0
        trade_audit_reconnects = 0
        oi_config = (
            Oi30sConfig(
                fact_source_id=FACT_SOURCE_ID,
                required_symbols=tuple(symbols),
                slot_seconds=30,
                request_timeout_seconds=5.0,
                retry_interval_seconds=0.25,
            )
            if use_rest_oi30s
            else None
        )
        oi_health = Oi30sHealthTracker(oi_config) if oi_config is not None else None
        oi_results: queue.Queue[OiSlotResult] = queue.Queue()
        oi_stop_event = threading.Event()
        trade_mirror = PublicTradeMirrorBuffer(
            tuple(symbols),
            retention_seconds=MIRROR_RETENTION_SECONDS,
        )
        trade_mirror_stop = threading.Event()
        trade_mirror_ready = threading.Event()

        def write_status(
            *, transport_state: str = "CONNECTED", extra: Mapping[str, object] | None = None
        ) -> None:
            payload = _status_payload(
                identity=identity,
                state=gate.state_at(datetime.now(UTC)),
                gate=gate,
                comparator=comparator,
                facts_received=facts_received,
                seeded_symbols=len(runners),
                total_symbols=len(symbols),
            ) | {
                "transport_state": transport_state,
                "transport_reconnects": transport_reconnects,
                "service_instance_id": identity.service_instance_id,
                "ws_ping_interval_seconds": PING_INTERVAL_SECONDS,
                "ws_pong_timeout_seconds": PONG_TIMEOUT_SECONDS,
                "trade_silence_audit_seconds": TRADE_SILENCE_AUDIT_SECONDS,
                "trade_audit_request_timeout_seconds": TRADE_AUDIT_REQUEST_TIMEOUT_SECONDS,
                "trade_audits": trade_audits,
                "trade_audit_reconnects": trade_audit_reconnects,
                "trade_proven_symbols": len(trade_proof_monotonic),
            }
            if oi_health is not None:
                payload.update(oi_health.snapshot())
            mirror_snapshot = trade_mirror.snapshot()
            payload.update(
                {
                    "trade_mirror_state": mirror_snapshot["state"],
                    "trade_mirror_epoch": mirror_snapshot["epoch"],
                    "trade_mirror_events": mirror_snapshot["events"],
                    "trade_mirror_duplicates": mirror_snapshot["duplicates"],
                    "trade_mirror_gaps": mirror_snapshot["gaps"],
                    "trade_mirror_last_received_at": mirror_snapshot["last_received_at"],
                    "trade_mirror_retention_seconds": MIRROR_RETENTION_SECONDS,
                }
            )
            if extra:
                payload.update(dict(extra))
            _atomic_json(STATUS_PATH, payload)

        def process_fact(
            fact: MarketFactEnvelope,
            *,
            trade_cursor: TradeCursor | None = None,
            oi_cursor: OiSampleCursor | None = None,
        ) -> bool:
            nonlocal facts_received, last_state
            if not deduper.accept(fact.fact_id):
                return True
            if fact.event_kind == "PUBLIC_TRADE":
                if trade_cursor is None:
                    raise ContinuityNotProvable(
                        "PUBLIC_TRADE accepted without exact transport cursor"
                    )
                previous_trade = trade_cursors.get(fact.symbol)
                if previous_trade is not None and trade_cursor.seq < previous_trade.seq:
                    raise ContinuityNotProvable(
                        f"PUBLIC_TRADE cross-sequence regressed for {fact.symbol}"
                    )
                if previous_trade is None or trade_cursor.seq > previous_trade.seq:
                    trade_current_seq_exec_ids[fact.symbol] = {trade_cursor.exec_id}
                else:
                    trade_current_seq_exec_ids.setdefault(fact.symbol, set()).add(
                        trade_cursor.exec_id
                    )
                trade_cursors[fact.symbol] = trade_cursor
            elif fact.event_kind == "OPEN_INTEREST":
                if use_rest_oi30s:
                    attrs = fact.attributes.to_dict()
                    if attrs.get("fact_source_id") != FACT_SOURCE_ID or not attrs.get("slot_id"):
                        raise ContinuityNotProvable(
                            "REST OPEN_INTEREST fact lacks exact source/slot identity"
                        )
                else:
                    if oi_cursor is None:
                        raise ContinuityNotProvable(
                            "OPEN_INTEREST accepted without exact OI30S cursor"
                        )
                    oi_cursors[fact.symbol] = oi_cursor
            elif fact.event_kind == "CANDLE_CLOSED":
                attrs = fact.attributes.to_dict()
                timeframe = str(attrs["timeframe"])
                boundary = datetime.fromisoformat(str(attrs["closed_at"])).astimezone(UTC)
                previous_closed = closed_boundaries.get((fact.symbol, timeframe))
                if previous_closed is not None and boundary < previous_closed:
                    raise ContinuityNotProvable(
                        f"CANDLE_CLOSED boundary regressed for {fact.symbol}:{timeframe}"
                    )
                closed_boundaries[(fact.symbol, timeframe)] = boundary
            elif fact.event_kind == "BAR_OPEN":
                attrs = fact.attributes.to_dict()
                boundary = datetime.fromisoformat(str(attrs["opened_at"])).astimezone(UTC)
                previous_bar = bar_open_boundaries.get(fact.symbol)
                if previous_bar is not None and boundary < previous_bar:
                    raise ContinuityNotProvable(f"BAR_OPEN boundary regressed for {fact.symbol}")
                bar_open_boundaries[fact.symbol] = boundary

            journal.append(fact)
            facts_received += 1
            if fact.event_kind == "PUBLIC_TRADE":
                flow_minutes[fact.symbol].add(
                    fact.observed_at.astimezone(UTC).replace(second=0, microsecond=0)
                )
            flow_ready = all(len(minutes) >= 5 for minutes in flow_minutes.values())
            oi_ready = True
            if oi_health is not None:
                oi_snapshot = oi_health.snapshot()
                oi_ready = (
                    int(str(oi_snapshot["complete_slots"])) > 0
                    and oi_snapshot["oi_source_state"] == "HEALTHY"
                )
            if flow_ready and oi_ready:
                gate.mark_live_sensor_complete()
            current_state = gate.state_at(fact.observed_at)
            if current_state is ShadowComparability.PARITY_COMPARABLE:
                observation = comparator.process(fact)
                store.record_observation(observation)
                store.update_summary(
                    identity,
                    {
                        "trading_effect": "NONE",
                        "state": current_state.value,
                        "facts_received": facts_received,
                        "transport_reconnects": transport_reconnects,
                        **comparator.summary(),
                    },
                )
            else:
                runners[fact.symbol].step(fact)
            if current_state is not last_state:
                store.transition_run(
                    identity,
                    status=current_state.value,
                    occurred_at=fact.observed_at,
                    summary={
                        "trading_effect": "NONE",
                        "state": current_state.value,
                        "reason": gate.reason,
                        "facts_received": facts_received,
                        "transport_reconnects": transport_reconnects,
                        **comparator.summary(),
                    },
                )
                last_state = current_state
            if comparator.first_mismatch is not None:
                final_summary = {
                    "trading_effect": "NONE",
                    "state": "FAIL",
                    "facts_received": facts_received,
                    "transport_reconnects": transport_reconnects,
                    **comparator.summary(),
                }
                store.transition_run(
                    identity,
                    status="FAIL",
                    occurred_at=fact.observed_at,
                    summary=final_summary,
                )
                write_status(transport_state="CONNECTED", extra={"run_status": "FAIL"})
                return False
            return True

        def collect_oi_results() -> list[OiSlotResult]:
            pending: list[OiSlotResult] = []
            while True:
                try:
                    pending.append(oi_results.get_nowait())
                except queue.Empty:
                    return pending

        def record_oi_slot(result: OiSlotResult) -> bool:
            if oi_health is None:
                raise RuntimeError("REST OI slot received without OI health tracker")
            oi_health.accept(result)
            occurred_at = result.response_received_at or result.slot_end_at
            _record_transport_event(
                connection,
                identity,
                category="OI30S_SLOT",
                occurred_at=occurred_at,
                payload={
                    "fact_source_id": result.fact_source_id,
                    "slot_id": result.slot_id,
                    "nominal_slot_at": result.nominal_slot_at.isoformat(),
                    "slot_end_at": result.slot_end_at.isoformat(),
                    "state": result.state.value,
                    "attempts": result.attempts,
                    "request_started_at": (
                        None
                        if result.request_started_at is None
                        else result.request_started_at.isoformat()
                    ),
                    "response_received_at": (
                        None
                        if result.response_received_at is None
                        else result.response_received_at.isoformat()
                    ),
                    "exchange_server_observed_at": (
                        None
                        if result.exchange_server_observed_at is None
                        else result.exchange_server_observed_at.isoformat()
                    ),
                    "delivery_delay_seconds": result.delivery_delay_seconds,
                    "missing_symbols": list(result.missing_symbols),
                    "invalid_symbols": list(result.invalid_symbols),
                    "accepted_symbols": [fact.symbol for fact in result.facts],
                    "fact_ids": [fact.fact_id for fact in result.facts],
                    "source_refs": [list(fact.source_refs) for fact in result.facts],
                    "provenance": result.provenance,
                    "reason": result.reason,
                    "health": oi_health.snapshot(),
                    "trading_effect": "NONE",
                },
            )
            if result.state is OiSlotState.COMPLETE:
                return True
            reason = f"OI30S source slot {result.slot_id} is {result.state.value}: {result.reason}"
            store.transition_run(
                identity,
                status="NOT_COMPARABLE",
                occurred_at=occurred_at,
                summary={
                    "trading_effect": "NONE",
                    "state": "NOT_COMPARABLE",
                    "reason": reason,
                    "facts_received": facts_received,
                    "transport_reconnects": transport_reconnects,
                    **oi_health.snapshot(),
                    **comparator.summary(),
                },
            )
            write_status(
                transport_state="CONNECTED",
                extra={"run_status": "NOT_COMPARABLE", "continuity_reason": reason},
            )
            return False

        def process_oi_results(results: list[OiSlotResult]) -> bool:
            for result in results:
                if not record_oi_slot(result):
                    return False
                for fact in result.facts:
                    if not process_fact(fact):
                        return False
            return True

        def message_items(
            message: Mapping[str, object], received_at: datetime
        ) -> tuple[tuple[MarketFactEnvelope, TradeCursor | None, OiSampleCursor | None], ...]:
            topic = str(message.get("topic") or "")
            result: list[tuple[MarketFactEnvelope, TradeCursor | None, OiSampleCursor | None]] = []
            if topic.startswith("publicTrade."):
                data = message.get("data")
                if isinstance(data, list):
                    for item in data:
                        if not isinstance(item, Mapping):
                            continue
                        symbol = str(item.get("s") or "").upper()
                        if symbol not in runners:
                            continue
                        trade_proof_monotonic[symbol] = time.monotonic()
                        result.append((_trade_fact(item, received_at), _trade_cursor(item), None))
            elif topic.startswith("kline."):
                result.extend(
                    (fact, None, None)
                    for fact in _kline_facts(message, received_at)
                    if fact.symbol in runners
                )
            elif not use_rest_oi30s and topic.startswith("tickers."):
                oi_result = _ticker_oi_fact(message, received_at, last_oi_sample)
                if oi_result is not None:
                    fact, cursor = oi_result
                    if fact.symbol in runners:
                        result.append((fact, None, cursor))
            return tuple(result)

        def process_message(message: Mapping[str, object], received_at: datetime) -> bool:
            if message.get("op") == "subscribe":
                if message.get("success") is False or message.get("retCode") not in (None, 0):
                    raise ConnectionError("U5 public subscription rejected")
                return True
            if message.get("op") == "pong" or message.get("ret_msg") == "pong":
                return True
            for fact, trade_cursor, oi_cursor in message_items(message, received_at):
                if not process_fact(fact, trade_cursor=trade_cursor, oi_cursor=oi_cursor):
                    return False
            return True

        trade_mirror_thread = threading.Thread(
            target=_run_public_trade_mirror,
            args=(tuple(symbols), trade_mirror, trade_mirror_stop, trade_mirror_ready),
            name="u5-public-trade-mirror",
            daemon=True,
        )
        trade_mirror_thread.start()
        if not trade_mirror_ready.wait(MIRROR_READY_TIMEOUT_SECONDS):
            raise ContinuityNotProvable(
                "publicTrade mirror did not become ready before startup deadline"
            )
        if trade_mirror.snapshot()["state"] != "ACTIVE":
            raise ContinuityNotProvable("publicTrade mirror startup continuity is not active")
        write_status()
        if oi_config is not None:
            oi_thread = threading.Thread(
                target=_run_rest_oi30s_worker,
                args=(oi_config, oi_results, oi_stop_event),
                name="u5-rest-oi30s",
                daemon=True,
            )
            oi_thread.start()
        sock, _initial_ready_local, _initial_ready_server, initial_buffer = _connect_and_subscribe(
            symbols
        )
        buffered_messages: list[dict[str, Any]] = list(initial_buffer)
        heartbeat = PublicWsHeartbeat(
            PING_INTERVAL_SECONDS,
            PONG_TIMEOUT_SECONDS,
            started_monotonic=time.monotonic(),
        )
        next_status = time.monotonic()
        transport_errors = (
            websocket.WebSocketConnectionClosedException,
            ConnectionError,
            OSError,
            ssl.SSLError,
        )

        while not stopping:
            try:
                audit_now = time.monotonic()
                stale_trade_symbols = tuple(
                    symbol
                    for symbol in symbols
                    if symbol in trade_cursors
                    and audit_now - trade_proof_monotonic.get(symbol, audit_now)
                    >= TRADE_SILENCE_AUDIT_SECONDS
                )
                if stale_trade_symbols:
                    try:
                        _audit_public_trade_silence(
                            stale_trade_symbols,
                            trade_cursors,
                            trade_current_seq_exec_ids,
                        )
                    except PublicTradeStreamBehind:
                        trade_audit_reconnects += 1
                        raise
                    except ContinuityNotProvable as audit_exc:
                        reason = str(audit_exc)
                        occurred_at = datetime.now(UTC)
                        _record_transport_event(
                            connection,
                            identity,
                            category="TRANSPORT_CONTINUITY",
                            occurred_at=occurred_at,
                            payload={
                                "verdict": "NOT_PROVABLE",
                                "source": "PUBLIC_TRADE_SILENCE_AUDIT",
                                "reason": reason,
                                "symbols": list(stale_trade_symbols),
                            },
                        )
                        store.transition_run(
                            identity,
                            status="NOT_COMPARABLE",
                            occurred_at=occurred_at,
                            summary={
                                "trading_effect": "NONE",
                                "state": "NOT_COMPARABLE",
                                "reason": reason,
                                "facts_received": facts_received,
                                "transport_reconnects": transport_reconnects,
                                "trade_audits": trade_audits,
                                **comparator.summary(),
                            },
                        )
                        write_status(
                            transport_state="NOT_PROVABLE",
                            extra={
                                "run_status": "NOT_COMPARABLE",
                                "continuity_reason": reason,
                            },
                        )
                        return
                    else:
                        trade_audits += 1
                        audit_proven_at = time.monotonic()
                        for symbol in stale_trade_symbols:
                            trade_proof_monotonic[symbol] = audit_proven_at
                if use_rest_oi30s and not process_oi_results(collect_oi_results()):
                    return
                message: dict[str, Any] | None = None
                received_at = datetime.now(UTC)
                if buffered_messages:
                    message = buffered_messages.pop(0)
                else:
                    try:
                        raw_message = sock.recv()
                        received_at = datetime.now(UTC)
                        if raw_message in (None, ""):
                            raise websocket.WebSocketConnectionClosedException(
                                "public WebSocket closed"
                            )
                        parsed = json.loads(raw_message)
                        if isinstance(parsed, dict):
                            message = cast(dict[str, Any], parsed)
                    except websocket.WebSocketTimeoutException:
                        pass
                now_monotonic = time.monotonic()
                if message is not None:
                    if message.get("op") == "pong" or message.get("ret_msg") == "pong":
                        heartbeat.note_pong(now_monotonic)
                    elif message.get("op") != "subscribe":
                        heartbeat.note_market_frame(now_monotonic)
                    if not process_message(message, received_at):
                        return
                if heartbeat.deadline_exceeded(now_monotonic):
                    raise websocket.WebSocketConnectionClosedException(
                        "application heartbeat deadline exceeded"
                    )
                if heartbeat.ping_due(now_monotonic):
                    sock.send('{"op":"ping"}')
                    heartbeat.note_ping_sent(now_monotonic)
                if now_monotonic >= next_status:
                    write_status()
                    next_status = now_monotonic + 2.0
            except transport_errors as exc:
                if stopping:
                    break
                disconnected_at = datetime.now(UTC)
                cursor_snapshot = _transport_cursor_payload(
                    trade_cursors=trade_cursors,
                    oi_cursors=oi_cursors,
                    closed_boundaries=closed_boundaries,
                    bar_open_boundaries=bar_open_boundaries,
                )
                _record_transport_event(
                    connection,
                    identity,
                    category="TRANSPORT_DISCONNECT",
                    occurred_at=disconnected_at,
                    payload={
                        "error": f"{type(exc).__name__}: {exc}",
                        "state": last_state.value,
                        "cursors": cursor_snapshot,
                    },
                )
                write_status(
                    transport_state="PAUSED",
                    extra={"transport_disconnect_at": disconnected_at.isoformat()},
                )
                with suppress(Exception):
                    sock.close()

                pending_gap_oi: list[OiSlotResult] = []
                try:
                    if use_rest_oi30s:
                        reconnect_errors = transport_errors + (websocket.WebSocketTimeoutException,)
                        while True:
                            for oi_result in collect_oi_results():
                                if not record_oi_slot(oi_result):
                                    return
                                pending_gap_oi.append(oi_result)
                            try:
                                new_sock, ready_local_at, ready_server_at, reconnect_buffer = (
                                    _connect_and_subscribe(symbols)
                                )
                                break
                            except reconnect_errors:
                                time.sleep(0.25)
                    else:
                        new_sock, ready_local_at, ready_server_at, reconnect_buffer = (
                            _reconnect_until_oi_safe(symbols, oi_cursors)
                        )
                except ContinuityNotProvable as gap_exc:
                    reason = str(gap_exc)
                    _record_transport_event(
                        connection,
                        identity,
                        category="TRANSPORT_CONTINUITY",
                        occurred_at=datetime.now(UTC),
                        payload={
                            "verdict": "NOT_PROVABLE",
                            "reason": reason,
                            "disconnect_at": disconnected_at.isoformat(),
                            "cursors": cursor_snapshot,
                        },
                    )
                    store.transition_run(
                        identity,
                        status="NOT_COMPARABLE",
                        occurred_at=datetime.now(UTC),
                        summary={
                            "trading_effect": "NONE",
                            "state": "NOT_COMPARABLE",
                            "reason": reason,
                            "facts_received": facts_received,
                            "transport_reconnects": transport_reconnects,
                            **comparator.summary(),
                        },
                    )
                    write_status(
                        transport_state="NOT_PROVABLE",
                        extra={"run_status": "NOT_COMPARABLE", "continuity_reason": reason},
                    )
                    return

                _record_transport_event(
                    connection,
                    identity,
                    category="TRANSPORT_RECONNECT",
                    occurred_at=ready_local_at,
                    payload={
                        "disconnect_at": disconnected_at.isoformat(),
                        "subscription_ready_local_at": ready_local_at.isoformat(),
                        "subscription_ready_server_at": ready_server_at.isoformat(),
                        "gap_seconds": max(0.0, (ready_local_at - disconnected_at).total_seconds()),
                        "cursors": cursor_snapshot,
                    },
                )
                trade_recovery_source = "REST_RECENT_TRADE"
                mirror_fallback_reason: str | None = None
                trade_override: tuple[tuple[MarketFactEnvelope, TradeCursor], ...] | None = None
                try:
                    mirror_cursors, mirror_seq_ids = trade_mirror.current_cursors()
                    if set(mirror_cursors) != set(symbols):
                        raise ContinuityNotProvable(
                            "publicTrade mirror lacks exact current cursors "
                            "for all required symbols"
                        )
                    # A continuously ACTIVE mirror epoch is itself the exact ordered
                    # publicTrade transport proof. Do not race it against a later REST
                    # snapshot here; that snapshot can contain newer trades and falsely
                    # disqualify a healthy mirror.
                    mirror_events = trade_mirror.recover_after(
                        trade_cursors,
                        cutoff_at=ready_server_at,
                    )
                    trade_override = tuple(
                        (
                            _replay_trade_fact(
                                event.as_replay_trade(),
                                event.received_at,
                            ),
                            TradeCursor(
                                event.symbol,
                                event.exec_id,
                                event.seq,
                                event.traded_at,
                            ),
                        )
                        for event in mirror_events
                    )
                    trade_recovery_source = "MIRROR_WS"
                except (ContinuityNotProvable, PublicTradeStreamBehind) as mirror_exc:
                    mirror_fallback_reason = f"{type(mirror_exc).__name__}: {mirror_exc}"

                try:
                    replay_items, replay_counts = _recover_public_gap(
                        symbols=symbols,
                        ready_local_at=ready_local_at,
                        ready_server_at=ready_server_at,
                        trade_cursors=trade_cursors,
                        oi_cursors=oi_cursors,
                        closed_boundaries=closed_boundaries,
                        bar_open_boundaries=bar_open_boundaries,
                        known_trade_exec_ids=trade_current_seq_exec_ids,
                        trade_replay_override=trade_override,
                    )
                except ContinuityNotProvable as gap_exc:
                    with suppress(Exception):
                        new_sock.close()
                    reason = str(gap_exc)
                    _record_transport_event(
                        connection,
                        identity,
                        category="TRANSPORT_CONTINUITY",
                        occurred_at=datetime.now(UTC),
                        payload={
                            "verdict": "NOT_PROVABLE",
                            "reason": reason,
                            "disconnect_at": disconnected_at.isoformat(),
                            "subscription_ready_server_at": ready_server_at.isoformat(),
                            "cursors": cursor_snapshot,
                        },
                    )
                    store.transition_run(
                        identity,
                        status="NOT_COMPARABLE",
                        occurred_at=datetime.now(UTC),
                        summary={
                            "trading_effect": "NONE",
                            "state": "NOT_COMPARABLE",
                            "reason": reason,
                            "facts_received": facts_received,
                            "transport_reconnects": transport_reconnects,
                            **comparator.summary(),
                        },
                    )
                    write_status(
                        transport_state="NOT_PROVABLE",
                        extra={"run_status": "NOT_COMPARABLE", "continuity_reason": reason},
                    )
                    return

                if use_rest_oi30s:
                    for oi_result in collect_oi_results():
                        if not record_oi_slot(oi_result):
                            with suppress(Exception):
                                new_sock.close()
                            return
                        pending_gap_oi.append(oi_result)
                    oi_gap_facts = [fact for result in pending_gap_oi for fact in result.facts]
                    replay_counts["OPEN_INTEREST"] = len(oi_gap_facts)
                    combined_replay = _merge_gap_oi_without_reordering_replay(
                        replay_items,
                        oi_gap_facts,
                    )
                else:
                    combined_replay = list(replay_items)
                for replay_fact, replay_trade_cursor in combined_replay:
                    if not process_fact(replay_fact, trade_cursor=replay_trade_cursor):
                        with suppress(Exception):
                            new_sock.close()
                        return
                recovery_proven_at = time.monotonic()
                for symbol in symbols:
                    trade_proof_monotonic[symbol] = recovery_proven_at
                transport_reconnects += 1
                _record_transport_event(
                    connection,
                    identity,
                    category="TRANSPORT_CONTINUITY",
                    occurred_at=datetime.now(UTC),
                    payload={
                        "verdict": "PROVEN_COMPLETE",
                        "disconnect_at": disconnected_at.isoformat(),
                        "subscription_ready_local_at": ready_local_at.isoformat(),
                        "subscription_ready_server_at": ready_server_at.isoformat(),
                        "same_parity_run_id": identity.parity_run_id,
                        "same_service_instance_id": identity.service_instance_id,
                        "same_started_at": identity.started_at.isoformat(),
                        "replay_counts": replay_counts,
                        "trade_recovery_source": trade_recovery_source,
                        "mirror_fallback_reason": mirror_fallback_reason,
                        "cursors_before": cursor_snapshot,
                        "cursors_after": _transport_cursor_payload(
                            trade_cursors=trade_cursors,
                            oi_cursors=oi_cursors,
                            closed_boundaries=closed_boundaries,
                            bar_open_boundaries=bar_open_boundaries,
                        ),
                    },
                )
                sock = new_sock
                buffered_messages = list(reconnect_buffer)
                heartbeat = PublicWsHeartbeat(
                    PING_INTERVAL_SECONDS,
                    PONG_TIMEOUT_SECONDS,
                    started_monotonic=time.monotonic(),
                )
                next_status = time.monotonic()
                write_status(
                    transport_state="CONNECTED",
                    extra={
                        "last_transport_continuity": "PROVEN_COMPLETE",
                        "last_reconnect_at": ready_local_at.isoformat(),
                        "last_trade_recovery_source": trade_recovery_source,
                    },
                )
                continue

        stopped_at = datetime.now(UTC)
        final_state = gate.state_at(stopped_at)
        if final_state is ShadowComparability.PARITY_COMPARABLE:
            final_status = "PASS" if comparator.first_mismatch is None else "FAIL"
        else:
            final_status = "NOT_COMPARABLE"
        store.transition_run(
            identity,
            status=final_status,
            occurred_at=stopped_at,
            summary={
                "trading_effect": "NONE",
                "state": final_status,
                "facts_received": facts_received,
                "transport_reconnects": transport_reconnects,
                **comparator.summary(),
            },
        )
    except ContinuityNotProvable as exc:
        failed_at = datetime.now(UTC)
        reason = f"{type(exc).__name__}: {exc}"
        with suppress(Exception):
            store.transition_run(
                identity,
                status="NOT_COMPARABLE",
                occurred_at=failed_at,
                summary={
                    "trading_effect": "NONE",
                    "state": "NOT_COMPARABLE",
                    "reason": reason,
                },
            )
        with suppress(Exception):
            _atomic_json(
                STATUS_PATH,
                {
                    "updated_at": failed_at.isoformat(),
                    "service": "cripta-universal-entry-shadow.service",
                    "trading_effect": "NONE",
                    "parity_run_id": identity.parity_run_id,
                    "state": "NOT_COMPARABLE",
                    "state_reason": reason,
                    "source_commit": identity.universal_source_commit,
                    "fact_source_id": identity.fact_source_id,
                    "service_instance_id": identity.service_instance_id,
                },
            )
        return
    except Exception as exc:
        failed_at = datetime.now(UTC)
        with suppress(Exception):
            store.transition_run(
                identity,
                status="NOT_COMPARABLE",
                occurred_at=failed_at,
                summary={
                    "trading_effect": "NONE",
                    "state": "NOT_COMPARABLE",
                    "reason": f"{type(exc).__name__}: {exc}",
                },
            )
        raise
    finally:
        if oi_stop_event is not None:
            oi_stop_event.set()
        if oi_thread is not None:
            oi_thread.join(timeout=2.0)
        if trade_mirror_stop is not None:
            trade_mirror_stop.set()
        if trade_mirror_thread is not None:
            trade_mirror_thread.join(timeout=2.0)
        if sock is not None:
            with suppress(Exception):
                sock.close()
        connection.close()


def main() -> None:
    if RUNTIME_MODE == "MULTI_STRATEGY_OBSERVER":
        _run_multi_strategy_observer()
        return
    if RUNTIME_MODE != "PARITY_V1":
        raise RuntimeError(f"unsupported CRIPTA_U5_RUNTIME_MODE={RUNTIME_MODE}")
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    _run_parity_main()


if __name__ == "__main__":
    main()
