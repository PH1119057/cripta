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
from contextlib import nullcontext, suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

import psycopg
import websocket

from bybit_workbench.counterfactual import (
    AnalystCounterfactualStore,
    build_insufficient_funds_candidate,
)
from bybit_workbench.domain.models import Candle
from bybit_workbench.entry_admission import PostgresEntryAdmissionPort
from bybit_workbench.exchange.bybit.mappers import map_rest_klines, map_ws_klines
from bybit_workbench.lifecycle_ack import record_plan_consumption
from bybit_workbench.live_arm_readiness import (
    LiveArmContext,
    active_live_arm_session,
    evaluate_live_arm,
)
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
LOADED_COMMIT = os.environ.get("CRIPTA_RELEASE_COMMIT", "").strip().lower()
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
    gate = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    rows = connection.execute(
        """SELECT a.activation_id,a.strategy_id,a.strategy_version,
                  a.strategy_config_fingerprint,a.updated_at,
                  ep.entry_plan_fingerprint,xp.exit_plan_fingerprint,
                  coalesce(p.enabled,false),coalesce(p.updated_at::text,'')
             FROM strategy_entry.strategy_activations a
             JOIN strategy_entry.entry_plans ep
               ON ep.strategy_id=a.strategy_id
              AND ep.strategy_version=a.strategy_version
              AND ep.strategy_config_fingerprint=a.strategy_config_fingerprint
             JOIN strategy_entry.exit_plans xp
               ON xp.strategy_id=a.strategy_id
              AND xp.strategy_version=a.strategy_version
              AND xp.strategy_config_fingerprint=a.strategy_config_fingerprint
             LEFT JOIN strategy_entry.execution_permissions p
               ON p.strategy_id=a.strategy_id
              AND p.strategy_version=a.strategy_version
              AND p.strategy_config_fingerprint=a.strategy_config_fingerprint
            WHERE a.enabled=true
            ORDER BY a.activation_id,ep.entry_plan_fingerprint,xp.exit_plan_fingerprint"""
    ).fetchall()
    gate_enabled = bool(gate and gate[0])
    return (
        ("MAINNET_GATE", str(gate_enabled)),
        *(tuple(str(item) for item in row) for row in rows),
    )


def _real_execution_activation_ids(
    connection: Any,
    bundles: tuple[object, ...],
) -> frozenset[str]:
    gate = connection.execute(
        "SELECT enabled FROM control.execution_gates WHERE mode='mainnet'"
    ).fetchone()
    if not gate or not bool(gate[0]) or not LOADED_COMMIT:
        return frozenset()
    rows = connection.execute(
        """SELECT strategy_id,strategy_version,strategy_config_fingerprint
             FROM strategy_entry.execution_permissions
            WHERE enabled=true AND enabled_at IS NOT NULL"""
    ).fetchall()
    allowed = {tuple(str(value) for value in row) for row in rows}
    current = datetime.now(UTC)
    ready: set[str] = set()
    for raw_bundle in bundles:
        bundle = cast(Any, raw_bundle)
        identity = (
            bundle.card.strategy_id,
            bundle.card.strategy_version,
            bundle.card.strategy_config_fingerprint,
        )
        if identity not in allowed:
            continue
        symbols = tuple(str(symbol) for symbol in bundle.card.symbols)
        if not symbols:
            continue
        activation_ready = True
        for symbol in symbols:
            try:
                context = LiveArmContext(
                    strategy_id=bundle.card.strategy_id,
                    strategy_version=bundle.card.strategy_version,
                    strategy_config_fingerprint=bundle.card.strategy_config_fingerprint,
                    strategy_activation_id=bundle.activation.activation_id,
                    symbol=symbol,
                    release_commit=LOADED_COMMIT,
                )
            except ValueError:
                activation_ready = False
                break
            if not evaluate_live_arm(
                connection,
                context=context,
                now=current,
                require_owner_approval=True,
            ).ready:
                activation_ready = False
                break
            if active_live_arm_session(connection, context=context) is None:
                activation_ready = False
                break
        if activation_ready:
            ready.add(bundle.activation.activation_id)
    return frozenset(ready)


def _real_entry_technical_readiness(
    connection: Any,
    *,
    observed_at: datetime,
) -> TechnicalReadiness:
    raw_max_age = os.environ.get("CRIPTA_REAL_ENTRY_ACCOUNT_STATE_MAX_AGE_SECONDS", "").strip()
    if not raw_max_age:
        return TechnicalReadiness(
            False,
            observed_at,
            "real Entry account-state freshness policy is not configured",
        )
    try:
        max_age = int(raw_max_age)
    except ValueError:
        return TechnicalReadiness(False, observed_at, "invalid real Entry account-state max age")
    if max_age <= 0:
        return TechnicalReadiness(
            False,
            observed_at,
            "real Entry account-state max age must be positive",
        )

    now_ms = int(observed_at.astimezone(UTC).timestamp() * 1000)
    reconciliation = connection.execute(
        """SELECT finished_at_epoch_ms,ok
             FROM runtime.reconciliation_runs
            ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    if (
        reconciliation is None
        or not bool(reconciliation[1])
        or now_ms - int(reconciliation[0]) < 0
        or now_ms - int(reconciliation[0]) > max_age * 1000
    ):
        return TechnicalReadiness(False, observed_at, "fresh Exchange reconciliation is required")

    wallet = connection.execute(
        "SELECT refreshed_at_epoch_ms FROM runtime.wallet_latest WHERE singleton=1"
    ).fetchone()
    if (
        wallet is None
        or now_ms - int(wallet[0]) < 0
        or now_ms - int(wallet[0]) > max_age * 1000
    ):
        return TechnicalReadiness(False, observed_at, "fresh account wallet state is required")

    critical_fault = connection.execute(
        """SELECT fault_code
             FROM runtime.lifecycle_faults
            WHERE state='OPEN' AND severity='CRITICAL'
            ORDER BY detected_at LIMIT 1"""
    ).fetchone()
    if critical_fault is not None:
        return TechnicalReadiness(
            False,
            observed_at,
            f"open critical lifecycle fault: {critical_fault[0]}",
        )
    return TechnicalReadiness(
        True,
        observed_at,
        "required account state and reconciliation are fresh",
    )


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
    sensor_status: Mapping[str, object] | None = None,
    strategy_monitors: tuple[Mapping[str, object], ...] = (),
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
            "sensor_status": dict(sensor_status or {}),
            "strategy_monitors": [dict(item) for item in strategy_monitors],
        },
    )


def _observer_unknown_prestart_warmup_seconds(
    bundles: tuple[object, ...], service_started_at: datetime
) -> int:
    horizons = [
        derive_unknown_prestart_horizon_seconds(cast(Any, bundle).entry_plan)
        if cast(Any, bundle).activation.enabled_at < service_started_at
        else 0
        for bundle in bundles
    ]
    return max(horizons, default=0)


def _run_observer_epoch(
    connection: Any,
    bundles: tuple[object, ...],
    stopping: Callable[[], bool],
    service_started_at: datetime,
) -> str:
    epoch_started = datetime.now(UTC)
    signature = _observer_light_signature(connection)
    if not signature:
        return "RELOAD"
    registry = ActivePlanRegistry()
    consumer_instance_id = (
        f"universal-entry-observer:{socket.gethostname()}:{os.getpid()}:"
        f"{service_started_at.isoformat()}"
    )
    for raw_bundle in bundles:
        bundle = cast(Any, raw_bundle)
        registry.register_card(bundle.card)
        materialized = registry.activate(bundle.activation)
        if materialized.entry_plan_fingerprint != bundle.entry_plan.entry_plan_fingerprint:
            raise RuntimeError("observer materialized EntryPlan fingerprint mismatch")
        record_plan_consumption(
            connection,
            plan_kind="ENTRY",
            plan_fingerprint=bundle.entry_plan.entry_plan_fingerprint,
            strategy_activation_id=bundle.activation.activation_id,
            consumer_instance_id=consumer_instance_id,
            seen_at=epoch_started,
            status="LOADED",
            payload={
                "source": "universal_entry_multi_strategy_observer",
                "source_commit": LOADED_COMMIT,
            },
        )
    engine = UniversalEntryEngine(registry)
    store = StrategyEntryStore(connection)
    counterfactual_store = AnalystCounterfactualStore(connection)
    paper = PaperTradeRuntime(connection)
    real_admission_required_for = _real_execution_activation_ids(connection, bundles)
    entry_admission_port = (
        PostgresEntryAdmissionPort(connection)
        if real_admission_required_for
        else None
    )
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
    warmup_seconds = _observer_unknown_prestart_warmup_seconds(bundles, service_started_at)
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
    last_trade_prices: dict[str, Decimal] = {}
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

    def sensor_status(now: datetime) -> dict[str, object]:
        flow_counts = {
            symbol: len(flow_minutes[symbol]) for symbol in sorted(required_flow_symbols)
        }
        missing_flow = [symbol for symbol, count in flow_counts.items() if count < 5]
        missing_oi = sorted(required_oi_symbols.difference(oi_seen))
        return {
            "time_ready": now >= warmup_until,
            "flow_required_symbols": sorted(required_flow_symbols),
            "flow_minute_counts": flow_counts,
            "flow_missing_symbols": missing_flow,
            "oi_required_symbols": sorted(required_oi_symbols),
            "oi_seen_symbols": sorted(oi_seen),
            "oi_missing_symbols": missing_oi,
        }

    def sensor_ready(now: datetime) -> bool:
        status = sensor_status(now)
        return bool(
            status["time_ready"]
            and not status["flow_missing_symbols"]
            and not status["oi_missing_symbols"]
        )

    def strategy_monitor_rows(now: datetime) -> tuple[Mapping[str, object], ...]:
        rows: list[Mapping[str, object]] = []
        for raw_bundle in bundles:
            bundle = cast(Any, raw_bundle)
            plan = bundle.entry_plan
            for symbol in plan.symbols:
                watch = engine.watch_snapshot(plan.entry_plan_fingerprint, symbol)
                lifecycle = engine.lifecycle_snapshot(
                    plan.entry_plan_fingerprint, symbol, account_ref="BYBIT:UNIFIED"
                )
                cooldown_until = engine.candidate_cooldown_until(
                    plan.entry_plan_fingerprint, symbol, account_ref="BYBIT:UNIFIED"
                )
                current_price = last_trade_prices.get(symbol)
                geometry = {
                    timeframe: {
                        "range_high": str(zone.range_high),
                        "range_low": str(zone.range_low),
                        "atr": str(zone.atr),
                        "resistance_top": str(zone.resistance_top),
                        "resistance_bottom": str(zone.resistance_bottom),
                        "support_top": str(zone.support_top),
                        "support_bottom": str(zone.support_bottom),
                        "effective_lookback": zone.effective_lookback,
                        "regime_reset_at": (
                            None
                            if zone.regime_reset_at is None
                            else zone.regime_reset_at.isoformat()
                        ),
                    }
                    for timeframe, zone in watch.geometry.items()
                }
                for direction in plan.directions:
                    entry_price = (
                        watch.long_entry if direction.value == "LONG" else watch.short_entry
                    )
                    distance_pct: Decimal | None = None
                    if current_price is not None and entry_price is not None and entry_price > 0:
                        if direction.value == "LONG":
                            distance_pct = (
                                (current_price - entry_price) / entry_price * Decimal("100")
                            )
                        else:
                            distance_pct = (
                                (entry_price - current_price) / entry_price * Decimal("100")
                            )
                    embargo_active = (
                        lifecycle.entry_embargo_until is not None
                        and now < lifecycle.entry_embargo_until
                    )
                    cooldown_active = cooldown_until is not None and now < cooldown_until
                    if embargo_active:
                        state = "EMBARGO"
                    elif cooldown_active:
                        state = "COOLDOWN"
                    elif watch.hourly_swing_blocked:
                        state = "SWING_BLOCK"
                    elif entry_price is None:
                        state = "WAITING"
                    elif distance_pct is not None and distance_pct <= 0:
                        state = "AT_OR_BEYOND_ENTRY"
                    elif distance_pct is not None and distance_pct <= Decimal("1"):
                        state = "APPROACH"
                    else:
                        state = "WATCH"
                    rows.append(
                        {
                            "strategy_key": f"{plan.strategy_id}::{plan.strategy_version}",
                            "strategy_id": plan.strategy_id,
                            "strategy_version": plan.strategy_version,
                            "strategy_name": bundle.card.name,
                            "strategy_config_fingerprint": plan.strategy_config_fingerprint,
                            "entry_plan_fingerprint": plan.entry_plan_fingerprint,
                            "activation_id": plan.strategy_activation_id,
                            "symbol": symbol,
                            "direction": direction.value,
                            "state": state,
                            "current_price": None if current_price is None else str(current_price),
                            "entry_price": None if entry_price is None else str(entry_price),
                            "distance_pct": None if distance_pct is None else str(distance_pct),
                            "candidate_id": watch.candidate_id,
                            "candidate_bar_at": (
                                None
                                if watch.candidate_bar_at is None
                                else watch.candidate_bar_at.isoformat()
                           