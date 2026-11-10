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
from bybit_workbench.market_data.market_source_public_candles import (
    decode_confirmed_bybit_klines,
)
from bybit_workbench.market_data.market_publication import (
    publish_market_source_second,
)
from bybit_workbench.market_data.shared_closed_candles import (
    MarketCandleKey,
    SharedClosedCandleStore,
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
# Both optional paths are INERT in the existing installed source and default
# OFF in any future release. Enabling either is a separate, proven source
# cutover with a registered canonical writer and consumer/data manifest.
_KLINE_CAPTURE_FLAG = os.environ.get("CRIPTA_SOURCE_CLOSED_KLINES", "0")
_PUBLICATION_FLAG = os.environ.get("CRIPTA_SOURCE_PUBLICATION", "0")
# R04 stays INERT. Existing 5-symbol original native producer has priority;
# enabling the union requires an exact operator-reviewed manifest on disk,
# dedicated release preflight and one-producer identity verification.
_UNION_CAPTURE_FLAG = os.environ.get("CRIPTA_SOURCE_UNION_CAPTURE", "0")
_UNION_MANIFEST_FILE = os.environ.get("CRIPTA_SOURCE_UNION_MANIFEST_FILE", "")
_KLINE_INTERVALS = frozenset(
    part.strip()
    for part in os.environ.get(
        "CRIPTA_SOURCE_KLINE_INTERVALS", "1,5,15",
    ).split(",") if part.strip()
)
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


def _source_feature_enabled(raw: str) -> bool:
    if raw not in ("0", "1"):
        raise NativeSourceInputUnverified("NATIVE_SOURCE_FEATURE_FLAG_INVALID")
    return raw == "1"


def _checked_kline_intervals(intervals: frozenset[str]) -> tuple[str, ...]:
    if not intervals or len(intervals) > 4 or not intervals <= {
        "1", "5", "15", "60",
    }:
        raise NativeSourceInputUnverified("NATIVE_SOURCE_KLINE_MANIFEST_INVALID")
    return tuple(sorted(intervals, key=int))


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
        "closed_klines_enabled": _source_feature_enabled(_KLINE_CAPTURE_FLAG),
        "publication_enabled": _source_feature_enabled(_PUBLICATION_FLAG),
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
    # The caller holds the one-producer PostgreSQL session advisory lock.
    # A power loss/SIGKILL leaves an ACTIVE row without a clean shutdown.
    # Close that previous epoch as an explicit gap, never as continuity.
    with connection.transaction():
        connection.execute(
            """UPDATE analytics.native_public_trade_source_sessions
                  SET state='GAP', ended_at=clock_timestamp(),
                      reason='PREVIOUS_WRITER_ENDED_UNCLEAN'
                WHERE market_source_id=%s AND state='ACTIVE'""",
            (BYBIT_PUBLIC_MARKET_SOURCE_ID,),
        )
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
            if not journal.append(event):
                # The same immutable native execId may be redelivered by
                # WebSocket. It is NOT a second causal trade and must not
                # advance the ordered checkpoint to a nonexistent event row.
                continue
            # Persist original provider WS ordering, including every
            # same-sequence distinct execId, before advancing a cursor.
            # Duplicates of the identical native identity are harmless;
            # a frame-position collision with DIFFERENT identity is not.
            connection.execute(
                """INSERT INTO analytics.native_public_trade_source_events
                   (source_session_id,frame_number,frame_index,
                    market_source_id,instrument_id,external_symbol,
                    exchange_sequence,exec_id,event_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (
                       source_session_id,market_source_id,instrument_id,
                       exchange_sequence,exec_id
                   ) DO NOTHING""",
                (session, frame_number, index, event.market_source_id,
                 event.instrument_id, event.external_symbol,
                 event.exchange_sequence, event.exec_id, event.event_at),
            )
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
                     event_at=EXCLUDED.event_at,
                     accepted_at=clock_timestamp()
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


def _persist_closed_kline_frame(
    connection: psycopg.Connection[Any], *,
    message: dict[str, object],
    symbols: tuple[str, ...],
    intervals: tuple[str, ...],
    product: str = "LINEAR",
) -> int:
    """The existing Native MarketSource also owns closed candle acquisition.

    This is one transport/source owner, not an Observer-specific writer. The
    shared canonical store rejects a conflict instead of masking it.
    """
    if product not in ("SPOT", "LINEAR"):
        raise NativeSourceInputUnverified("NATIVE_SOURCE_CANDLE_CATEGORY_INVALID")
    observed = datetime.now(UTC)
    candles = decode_confirmed_bybit_klines(
        message,
        symbols=frozenset(symbols),
        intervals=frozenset(intervals),
        received_at=observed,
    )
    inserted = 0
    for bar in candles:
        key = MarketCandleKey(
            market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
            venue_id="venue-bybit",
            instrument_id=bybit_instrument_id(product.lower(), bar.symbol),
            external_symbol=bar.symbol,
            product_category=product,
            timeframe=bar.timeframe,
        )
        inserted += SharedClosedCandleStore(connection, key).ingest(
            (bar,), producer_release_commit=RELEASE,
            transport="BYBIT_WS_KLINE", observed_at=observed,
        )
    return inserted


def _subscribe(
    symbols: tuple[str, ...], *,
    extra_topics: tuple[str, ...] = (),
) -> tuple[Any, tuple[dict[str, object], ...]]:
    sock = websocket.create_connection(PUBLIC_WS, timeout=10.0)
    sock.settimeout(1.0)
    request_id = "cripta-native-source-v1"
    topics = tuple("publicTrade." + symbol for symbol in symbols) + extra_topics
    sock.send(json.dumps({
        "op": "subscribe", "req_id": request_id,
        "args": list(topics),
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
        if str(message.get("topic") or "") in topics:
            buffered.append(message)
    sock.close()
    raise NativeSourceInputUnverified("NATIVE_SOURCE_SUBSCRIBE_TIMEOUT")


def _run_stream(
    connection: psycopg.Connection[Any], symbols: tuple[str, ...],
) -> None:
    capture_kline = _source_feature_enabled(_KLINE_CAPTURE_FLAG)
    publish_source = _source_feature_enabled(_PUBLICATION_FLAG)
    intervals = _checked_kline_intervals(_KLINE_INTERVALS) if capture_kline else ()
    extra_topics = tuple(
        f"kline.{tf}.{sym}" for sym in symbols for tf in intervals
    )
    sock, initial = _subscribe(symbols, extra_topics=extra_topics)
    session = uuid4().hex
    _session_start(connection, session)
    _status("ACTIVE", session=session)
    last_provider_frame = time.monotonic()
    last_ping = last_provider_frame
    frame_number = 0
    last_published_second: datetime | None = None
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
                topic = str(message.get("topic") or "")
                if topic.startswith("publicTrade."):
                    frame_number += 1
                    facts = decode_native_trades(
                        message, symbols=frozenset(symbols), release=RELEASE,
                    )
                    _persist_frame(
                        connection, session=session,
                        frame_number=frame_number, facts=facts,
                    )
                elif capture_kline and topic.startswith("kline."):
                    _persist_closed_kline_frame(
                        connection, message=message,
                        symbols=symbols, intervals=intervals,
                    )
            # Publish only after native and closed-candle transactions commit.
            # The immutable one-second row stores *references*, never a
            # second copy of native events or OHLCV.
            if publish_source:
                current_second = datetime.now(UTC).replace(microsecond=0)
                if current_second != last_published_second:
                    publish_market_source_second(
                        connection, source_session_id=session,
                        producer_release_commit=RELEASE, symbols=symbols,
                        timeframes=intervals or ("1", "5", "15"),
                        observed_at=current_second,
                    )
                    last_published_second = current_second
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
    union = None
    if _source_feature_enabled(_UNION_CAPTURE_FLAG):
        # The native producer itself remains the only process/lock owner.
        # No guessed topics and no silently ignored required REST facts.
        if _UNION_MANIFEST_FILE != (
            "/etc/cripta/market-source/required_public_feeds.json"
        ):
            raise NativeSourceInputUnverified(
                "NATIVE_SOURCE_UNION_MANIFEST_PATH_UNAPPROVED"
            )
        from operations.monitoring.public_market_source_union_capture import (
            parse_explicit_source_manifest,
        )
        manifest_path = Path(_UNION_MANIFEST_FILE)
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise NativeSourceInputUnverified(
                "NATIVE_SOURCE_UNION_MANIFEST_NOT_REGULAR"
            )
        union = parse_explicit_source_manifest(
            manifest_path.read_text(encoding="utf-8"),
            native_symbols=symbols,
        )
    os.umask(0o027)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    _status("STARTING")
    with psycopg.connect(DATABASE_DSN, connect_timeout=5) as connection:
        _acquire_exclusive_producer(connection)
        while not stopping:
            try:
                if union is None:
                    _run_stream(connection, symbols)
                else:
                    from operations.monitoring.public_market_source_union_capture import (
                        run_source_union,
                    )
                    run_source_union(
                        connection, p90_symbols=symbols, union=union,
                        producer_release_commit=RELEASE,
                        publish_seconds=_source_feature_enabled(_PUBLICATION_FLAG),
                    )
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
