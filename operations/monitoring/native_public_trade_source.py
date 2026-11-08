"""Credential-free independent Bybit publicTrade capture (Issue #514).

This is a single physical append-only MarketSource producer, not an Entry,
Exit, Strategy or permission owner. Separate WebSocket sessions are NEVER
asserted continuous merely because fresh trades arrive. The production
journal row and session cursor commit in one PostgreSQL transaction.
"""
from __future__ import annotations

import json
import os
import re
import signal
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import FrameType
from typing import Any
from uuid import uuid4

import psycopg
import websocket

from bybit_workbench.market_data.native_public_trade_journal import (
    NativePublicTradeJournal,
    NativeTradeEvidence,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

DATABASE_DSN = os.environ.get(
    "CRIPTA_U5_DATABASE_DSN",
    "dbname=cripta user=cripta host=/var/run/postgresql",
)
PUBLIC_WS = os.environ.get(
    "CRIPTA_U5_PUBLIC_WS", "wss://stream.bybit.kz/v5/public/linear",
)
RELEASE = os.environ.get("CRIPTA_RELEASE_COMMIT", "").strip().lower()
STATE_ROOT = Path("/var/lib/cripta/native_public_trade_source")
STATUS_PATH = STATE_ROOT / "status.json"
LOCK_KEY = "cripta:market-source:native-public-trade:one-producer:v1"
_ALLOWED_SYMBOLS = frozenset(
    part.strip().upper()
    for part in os.environ.get(
        "CRIPTA_NATIVE_PUBLIC_TRADE_SYMBOLS",
        "APTUSDT,ARBUSDT,DOTUSDT,INJUSDT,LTCUSDT",
    ).split(",")
    if part.strip()
)
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{2,32}$")
stopping = False


class NativeSourceInputUnverified(RuntimeError):
    """Invalid source, session, producer identity or provider frame."""


def _checked_symbols(symbols: frozenset[str]) -> tuple[str, ...]:
    if (
        not 1 <= len(symbols) <= 40
        or any(not _SYMBOL.fullmatch(s) for s in symbols)
    ):
        raise NativeSourceInputUnverified("NATIVE_SOURCE_SYMBOL_MANIFEST_INVALID")
    return tuple(sorted(symbols))


def decode_native_trades(
    message: dict[str, object], *, symbols: frozenset[str], release: str,
) -> tuple[NativeTradeEvidence, ...]:
    """Accept original Bybit WS cursor identity, not synthetic candles/IDs."""
    if not isinstance(release, str) or _SHA.fullmatch(release) is None:
        raise NativeSourceInputUnverified("NATIVE_SOURCE_RELEASE_INVALID")
    topic = message.get("topic")
    if not isinstance(topic, str) or not topic.startswith("publicTrade."):
        return ()
    symbol = topic.removeprefix("publicTrade.")
    if symbol not in symbols or not _SYMBOL.fullmatch(symbol):
        raise NativeSourceInputUnverified("NATIVE_SOURCE_TOPIC_UNAUTHORIZED")
    rows = message.get("data")
    if not isinstance(rows, list):
        raise NativeSourceInputUnverified("NATIVE_SOURCE_PROVIDER_DATA_INVALID")
    result: list[NativeTradeEvidence] = []
    seen: set[tuple[int, str]] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise NativeSourceInputUnverified("NATIVE_SOURCE_PROVIDER_ROW_INVALID")
        try:
            seq = raw["seq"]
            epoch_ms = raw["T"]
            if type(seq) is not int or type(epoch_ms) is not int:
                raise ValueError("native integers required")
            if seq < 0 or not 946684800000 <= epoch_ms <= 4102444800000:
                raise ValueError("native event time/sequence out of bounds")
            if raw["s"] != symbol:
                raise ValueError("instrument differs from topic")
            event_id = raw["i"]
            if not isinstance(event_id, str) or not event_id:
                raise ValueError("missing provider exec ID")
            price = Decimal(str(raw["p"]))
            qty = Decimal(str(raw["v"]))
            evidence = NativeTradeEvidence(
                market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
                instrument_id=bybit_instrument_id("linear", symbol),
                external_symbol=symbol, exchange_sequence=seq,
                exec_id=event_id,
                event_at=datetime.fromtimestamp(epoch_ms / 1000, UTC),
                price=price, quantity=qty, taker_side=str(raw["S"]),
                source_transport="BYBIT_WS_PUBLIC_TRADE",
                producer_release_commit=release,
            )
            identity = (seq, event_id)
            if identity in seen:
                raise ValueError("duplicate provider identity within one frame")
            seen.add(identity)
        except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
            raise NativeSourceInputUnverified(
                "NATIVE_SOURCE_PROVIDER_IDENTITY_INVALID"
            ) from exc
        result.append(evidence)
    return tuple(result)


def _status(state: str, *, session: str = "", reason: str = "") -> None:
    # Read-model only. PostgreSQL transactions remain the source of truth.
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {
        "service": "cripta-native-public-trade-source.service",
        "state": state, "reason": reason,
        "session": session, "source_commit": RELEASE,
        "observed_at": datetime.now(UTC).isoformat(),
        "trading_effect": "NONE",
    }
    tmp = STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    tmp.chmod(0o640)
    tmp.replace(STATUS_PATH)


def _acquire_exclusive_producer(connection: psycopg.Connection[Any]) -> None:
    # Session-scoped advisory lock survives each journaling COMMIT; released
    # automatically on process/DB-connection death.
    row = connection.execute(
        "SELECT pg_try_advisory_lock(hashtextextended(%s,0))",
        (LOCK_KEY,),
    ).fetchone()
    connection.commit()
    if row != (True,):
        raise NativeSourceInputUnverified("NATIVE_SOURCE_WRITER_ALREADY_OWNED")


def _session_start(connection: psycopg.Connection[Any], session: str) -> None:
    with connection.transaction():
        connection.execute(
            """INSERT INTO analytics.native_public_trade_source_sessions
                (source_session_id,market_source_id,producer_release_commit,
                 state,subscribed_at)
               VALUES (%s,%s,%s,'ACTIVE',clock_timestamp())""",
            (session, BYBIT_PUBLIC_MARKET_SOURCE_ID, RELEASE),
        )


def _session_gap(
    connection: psycopg.Connection[Any], session: str, reason: str,
) -> None:
    with connection.transaction():
        connection.execute(
            """UPDATE analytics.native_public_trade_source_sessions
               SET state='GAP',ended_at=clock_timestamp(),reason=%s
               WHERE source_session_id=%s AND state='ACTIVE'""",
            (reason, session),
        )


def _persist_frame(
    connection: psycopg.Connection[Any], *, session: str,
    frame_number: int, facts: tuple[NativeTradeEvidence, ...],
) -> None:
    """Native evidence and exact source cursor commit atomically."""
    if not facts:
        return
    with connection.transaction():
        journal = NativePublicTradeJournal(connection)
        for index, event in enumerate(facts):
            prior = connection.execute(
                """SELECT exchange_sequence,event_at
                     FROM analytics.native_public_trade_source_cursors
                    WHERE source_session_id=%s AND external_symbol=%s
                    FOR UPDATE""",
                (session, event.external_symbol),
            ).fetchone()
            if prior is not None and (
                event.exchange_sequence < prior[0]
                or event.event_at < prior[1]
            ):
                raise NativeSourceInputUnverified(
                    "NATIVE_SOURCE_ORDER_REGRESSION"
                )
            journal.append(event)
            connection.execute(
                """INSERT INTO analytics.native_public_trade_source_cursors
                   (source_session_id,market_source_id,instrument_id,
                    external_symbol,frame_number,frame_index,exchange_sequence,
                    exec_id,event_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (source_session_id,external_symbol)
                   DO UPDATE SET
                     frame_number=EXCLUDED.frame_number,
                     frame_index=EXCLUDED.frame_index,
                     exchange_sequence=EXCLUDED.exchange_sequence,
                     exec_id=EXCLUDED.exec_id,
                     event_at=EXCLUDED.event_at
                   WHERE (native_public_trade_source_cursors.frame_number,
                          native_public_trade_source_cursors.frame_index)
                       < (EXCLUDED.frame_number,EXCLUDED.frame_index)""",
                (session, event.market_source_id, event.instrument_id,
                 event.external_symbol, frame_number, index,
                 event.exchange_sequence, event.exec_id, event.event_at),
            )
        connection.execute(
            """UPDATE analytics.native_public_trade_source_sessions
               SET last_accepted_at=clock_timestamp()
               WHERE source_session_id=%s AND state='ACTIVE'""",
            (session,),
        )


def _subscribe(symbols: tuple[str, ...]) -> tuple[Any, tuple[dict[str, object], ...]]:
    sock = websocket.create_connection(PUBLIC_WS, timeout=10.0)
    sock.settimeout(1.0)
    request_id = "cripta-native-source-v1"
    sock.send(json.dumps({
        "op": "subscribe", "req_id": request_id,
        "args": ["publicTrade." + symbol for symbol in symbols],
    }, separators=(",", ":")))
    buffered: list[dict[str, object]] = []
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        try:
            raw = sock.recv()
        except websocket.WebSocketTimeoutException:
            continue
        if not raw:
            sock.close()
            raise NativeSourceInputUnverified("NATIVE_SOURCE_SUBSCRIBE_CLOSED")
        message = json.loads(raw)
        if not isinstance(message, dict):
            continue
        if message.get("op") == "subscribe":
            if (
                message.get("req_id") == request_id
                and message.get("success") is True
                and message.get("retCode") in (None, 0)
            ):
                return sock, tuple(buffered)
            sock.close()
            raise NativeSourceInputUnverified("NATIVE_SOURCE_SUBSCRIBE_REJECTED")
        if str(message.get("topic") or "").startswith("publicTrade."):
            buffered.append(message)
    sock.close()
    raise NativeSourceInputUnverified("NATIVE_SOURCE_SUBSCRIBE_TIMEOUT")


def _run_stream(
    connection: psycopg.Connection[Any], symbols: tuple[str, ...],
) -> None:
    sock, initial = _subscribe(symbols)
    session = uuid4().hex
    _session_start(connection, session)
    _status("ACTIVE", session=session)
    last_provider_frame = time.monotonic()
    last_ping = last_provider_frame
    frame_number = 0
    try:
        pending = list(initial)
        while not stopping:
            message: dict[str, object] | None = None
            if pending:
                message = pending.pop(0)
            else:
                try:
                    raw = sock.recv()
                    if raw in (None, ""):
                        raise NativeSourceInputUnverified(
                            "NATIVE_SOURCE_STREAM_CLOSED"
                        )
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        message = parsed
                except websocket.WebSocketTimeoutException:
                    pass
            now = time.monotonic()
            if message is not None:
                if (
                    message.get("op") == "pong"
                    or message.get("ret_msg") == "pong"
                    or str(message.get("topic") or "").startswith("publicTrade.")
                ):
                    last_provider_frame = now
                if str(message.get("topic") or "").startswith("publicTrade."):
                    frame_number += 1
                    facts = decode_native_trades(
                        message, symbols=frozenset(symbols), release=RELEASE,
                    )
                    _persist_frame(
                        connection, session=session,
                        frame_number=frame_number, facts=facts,
                    )
            if now - last_ping >= 20.0:
                sock.send('{"op":"ping"}')
                last_ping = now
            if now - last_provider_frame > 65.0:
                raise NativeSourceInputUnverified(
                    "NATIVE_SOURCE_HEARTBEAT_UNVERIFIED"
                )
        _session_gap(connection, session, "OWNER_CONTROLLED_STOP")
        _status("STOPPED", session=session)
    except Exception as exc:
        # Typed category only; never provider payloads or private state.
        reason = type(exc).__name__
        _session_gap(connection, session, reason)
        _status("RECONNECTING", session=session, reason=reason)
        raise
    finally:
        sock.close()


def _stop(_signal: int, _frame: FrameType | None) -> None:
    global stopping
    stopping = True


def main() -> int:
    if not _SHA.fullmatch(RELEASE):
        raise NativeSourceInputUnverified("NATIVE_SOURCE_RELEASE_INVALID")
    symbols = _checked_symbols(_ALLOWED_SYMBOLS)
    os.umask(0o027)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    _status("STARTING")
    with psycopg.connect(DATABASE_DSN, connect_timeout=5) as connection:
        _acquire_exclusive_producer(connection)
        while not stopping:
            try:
                _run_stream(connection, symbols)
            except (websocket.WebSocketException, OSError, TimeoutError,
                    NativeSourceInputUnverified):
                if stopping:
                    break
                time.sleep(2.0)
            except Exception:
                _status("BLOCKED", reason="PRODUCER_DATABASE_OR_SOURCE_INVALID")
                raise
    _status("STOPPED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
