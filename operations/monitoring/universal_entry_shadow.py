from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

import psycopg
import websocket

from bybit_workbench.domain.models import Candle
from bybit_workbench.exchange.bybit.mappers import map_rest_klines, map_ws_klines
from bybit_workbench.universal_entry import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_entry.fingerprint import fingerprint
from bybit_workbench.universal_entry.market_watch import GenericOiPoint
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.parity import V1DeterministicParityRunner
from bybit_workbench.universal_entry.shadow_runtime import (
    DurableFactJournal,
    OnlineParityComparator,
    ShadowComparability,
    ShadowComparabilityGate,
    ShadowRunIdentity,
    derive_unknown_prestart_horizon_seconds,
)
from bybit_workbench.universal_entry.storage import ShadowParityStore
from bybit_workbench.universal_entry.transport_continuity import (
    ContinuityAction,
    ContinuityNotProvable,
    ExactFactDeduper,
    OiSampleCursor,
    ReplayTrade,
    TradeCursor,
    assess_oi30s_continuity,
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
OI_SAMPLE_SECONDS = int(os.environ.get("CRIPTA_U5_OI_SAMPLE_SECONDS", "30"))
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
        if int(payload.get("retCode", -1)) != 0:
            raise RuntimeError(
                f"public endpoint failed: retCode={payload.get('retCode')} "
                f"retMsg={payload.get('retMsg')}"
            )
        return cast(dict[str, Any], payload)
    raise RuntimeError("unreachable public REST retry state")


def _fetch_history(symbol: str, observed_at: datetime) -> dict[str, tuple[Candle, ...]]:
    history: dict[str, tuple[Candle, ...]] = {}
    for interval in ("5", "15", "60"):
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


def _topics(symbols: tuple[str, ...]) -> tuple[str, ...]:
    topics: list[str] = []
    for symbol in symbols:
        for interval in ("5", "15", "60"):
            topics.append(f"kline.{interval}.{symbol}")
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
) -> tuple[Any, datetime, datetime, tuple[dict[str, Any], ...]]:
    sock = websocket.create_connection(PUBLIC_WS, timeout=10.0, enable_multithread=False)
    with suppress(AttributeError):
        sock.settimeout(1.0)
    pending: set[str] = set()
    topics = _topics(symbols)
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
) -> tuple[tuple[tuple[MarketFactEnvelope, TradeCursor | None], ...], dict[str, int]]:
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
    for symbol in symbols:
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
        )
        for row in rows:
            cursor = TradeCursor(row.symbol, row.exec_id, row.seq, row.traded_at)
            replay.append((_replay_trade_fact(row, ready_local_at), cursor))
            counts["PUBLIC_TRADE"] += 1

        cached: dict[str, tuple[Candle, ...]] = {}
        for timeframe in ("5", "15", "60"):
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
            item[0].event_at,
            priority[item[0].event_kind],
            -int(str(item[0].attributes.to_dict().get("timeframe") or 0)),
            -1 if item[1] is None else item[1].seq,
            item[0].fact_id,
        )
    )
    return tuple(replay), counts


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


def main() -> None:
    if not LOADED_COMMIT:
        raise RuntimeError("CRIPTA_U5_LOADED_COMMIT is required")
    if OI_SAMPLE_SECONDS <= 0:
        raise RuntimeError("CRIPTA_U5_OI_SAMPLE_SECONDS must be positive")
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
        oi_cursors: dict[str, OiSampleCursor] = {}
        bar_open_boundaries: dict[str, datetime] = {}
        deduper = ExactFactDeduper()
        transport_reconnects = 0

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
            }
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
                trade_cursors[fact.symbol] = trade_cursor
            elif fact.event_kind == "OPEN_INTEREST":
                if oi_cursor is None:
                    raise ContinuityNotProvable("OPEN_INTEREST accepted without exact OI30S cursor")
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
            if all(len(minutes) >= 5 for minutes in flow_minutes.values()):
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
                        result.append((_trade_fact(item, received_at), _trade_cursor(item), None))
            elif topic.startswith("kline."):
                result.extend(
                    (fact, None, None)
                    for fact in _kline_facts(message, received_at)
                    if fact.symbol in runners
                )
            elif topic.startswith("tickers."):
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

        write_status()
        sock, _initial_ready_local, _initial_ready_server, initial_buffer = _connect_and_subscribe(
            symbols
        )
        buffered_messages: list[dict[str, Any]] = list(initial_buffer)
        next_ping = time.monotonic() + 20.0
        next_status = time.monotonic()
        transport_errors = (
            websocket.WebSocketConnectionClosedException,
            ConnectionError,
            OSError,
            ssl.SSLError,
        )

        while not stopping:
            try:
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
                if message is not None and not process_message(message, received_at):
                    return
                now_monotonic = time.monotonic()
                if now_monotonic >= next_ping:
                    sock.send('{"op":"ping"}')
                    next_ping = now_monotonic + 20.0
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

                try:
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
                try:
                    replay_items, replay_counts = _recover_public_gap(
                        symbols=symbols,
                        ready_local_at=ready_local_at,
                        ready_server_at=ready_server_at,
                        trade_cursors=trade_cursors,
                        oi_cursors=oi_cursors,
                        closed_boundaries=closed_boundaries,
                        bar_open_boundaries=bar_open_boundaries,
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

                for replay_fact, replay_trade_cursor in replay_items:
                    if not process_fact(replay_fact, trade_cursor=replay_trade_cursor):
                        with suppress(Exception):
                            new_sock.close()
                        return
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
                next_ping = time.monotonic() + 20.0
                next_status = time.monotonic()
                write_status(
                    transport_state="CONNECTED",
                    extra={
                        "last_transport_continuity": "PROVEN_COMPLETE",
                        "last_reconnect_at": ready_local_at.isoformat(),
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
        if sock is not None:
            with suppress(Exception):
                sock.close()
        connection.close()


if __name__ == "__main__":
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    main()
