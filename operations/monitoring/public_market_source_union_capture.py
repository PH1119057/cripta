"""Optional two-transport source capture INSIDE the existing Native MarketSource.

This is not a service or independently owned source. Both Bybit public WS
transports live inside one process holding the native producer's PostgreSQL
advisory lock. All upstream topics come from an exact reviewed consumer
manifest and are subscribed once per product. PublicTrade of P90 instruments
continues through the original durable native journal before cursor advance.
Non-P90 market facts currently emit *compact* second-level rows, not raw tape.
No consumer cutover, Strategy, account, Exchange, private API or LIVE right.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from datetime import UTC, datetime
from typing import Any

import psycopg
import websocket

from bybit_workbench.market_data.market_publication import (
    publish_market_source_second,
)
from bybit_workbench.market_data.public_source_event_codec import (
    decode_bybit_public_frame,
)
from bybit_workbench.market_data.public_source_second_store import (
    PublicSecondAccumulator,
    PublicSourceSecondUnavailable,
    persist_compact_source_seconds,
)
from bybit_workbench.market_data.source_required_feed_manifest import (
    PublicFeedUnion,
    RequiredPublicFeed,
    SourceManifestInvalid,
    union_public_feed_requirements,
)

_PUBLIC_WS = {
    "SPOT": "wss://stream.bybit.kz/v5/public/spot",
    "LINEAR": "wss://stream.bybit.kz/v5/public/linear",
}
_MAX_QUEUE = 8192


def parse_explicit_source_manifest(
    manifest: str, *, native_symbols: tuple[str, ...],
) -> PublicFeedUnion:
    """No guessed topic list; the operator must publish exact source consumers."""
    if not manifest or len(manifest) > 300000:
        raise SourceManifestInvalid("SOURCE_UNION_MANIFEST_MISSING")
    try:
        payload = json.loads(manifest)
        if not isinstance(payload, list) or not 1 <= len(payload) <= 5000:
            raise ValueError("list required")
        required = tuple(
            RequiredPublicFeed(**item) for item in payload
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise SourceManifestInvalid(
            "SOURCE_UNION_MANIFEST_PARSE_INVALID"
        ) from exc
    feeds = union_public_feed_requirements(required)
    # Do not advertise a physical capability that this source has not
    # implemented. REST-only facts and forming bar payload require their own
    # source adapters; spot kline is not yet stored by this producer.
    if any(item.required and item.dataset in (
        "ACCOUNT_RATIO", "INSTRUMENT_INFO", "FORMING_KLINE",
    ) for item in required):
        raise SourceManifestInvalid("SOURCE_REQUIRED_INPUT_ADAPTER_NOT_READY")
    if any(item.required and item.product == "SPOT"
           and item.dataset == "CLOSED_KLINE" for item in required):
        raise SourceManifestInvalid("SOURCE_REQUIRED_SPOT_KLINE_NOT_READY")
    linear = {
        entry.dataset_topic
        for entry in feeds.websocket_topics if entry.product == "LINEAR"
    }
    for symbol in native_symbols:
        if f"publicTrade.{symbol}" not in linear:
            raise SourceManifestInvalid("SOURCE_P90_NATIVE_TOPIC_MISSING")
    if not feeds.websocket_batches("SPOT"):
        raise SourceManifestInvalid("SOURCE_SPOT_TRANSPORT_MISSING")
    return feeds


def _connect_subscribe(
    *, product: str,
    union: PublicFeedUnion,
) -> tuple[Any, list[dict[str, object]]]:
    """Every subscription must be ACKED; buffer early original source frames."""
    if product not in _PUBLIC_WS:
        raise SourceManifestInvalid("SOURCE_CATEGORY_UNAUTHORIZED")
    batches = union.websocket_batches(product)
    if not batches:
        raise SourceManifestInvalid("SOURCE_EMPTY_TRANSPORT_UNAUTHORIZED")
    sock = websocket.create_connection(_PUBLIC_WS[product], timeout=10.0)
    sock.settimeout(1.0)
    pending: set[str] = set()
    for i, batch in enumerate(batches):
        request_id = f"cripta-source-{product.lower()}-{i}"
        sock.send(json.dumps({
            "op": "subscribe", "req_id": request_id,
            "args": list(batch),
        }, separators=(",", ":")))
        pending.add(request_id)
    buffered: list[dict[str, object]] = []
    expected_topics = {
        item for batch in batches for item in batch
    }
    deadline = time.monotonic() + 15
    try:
        while pending and time.monotonic() < deadline:
            try:
                raw = sock.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not raw:
                raise PublicSourceSecondUnavailable(
                    "PUBLIC_FEED_SUBSCRIBE_CLOSED"
                )
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                continue
            if msg.get("op") == "subscribe":
                request_id = msg.get("req_id")
                if (
                    request_id not in pending
                    or msg.get("success") is not True
                    or msg.get("retCode") not in (None, 0)
                ):
                    raise PublicSourceSecondUnavailable(
                        "PUBLIC_FEED_SUBSCRIBE_REJECTED"
                    )
                pending.remove(request_id)
            elif msg.get("topic") in expected_topics:
                buffered.append(msg)
                if len(buffered) > _MAX_QUEUE:
                    raise PublicSourceSecondUnavailable(
                        "PUBLIC_FEED_EARLY_FRAME_OVERFLOW"
                    )
        if pending:
            raise PublicSourceSecondUnavailable(
                "PUBLIC_FEED_SUBSCRIBE_TIMEOUT"
            )
        return sock, buffered
    except Exception:
        sock.close()
        raise


def run_source_union(
    connection: psycopg.Connection[Any], *,
    p90_symbols: tuple[str, ...],
    union: PublicFeedUnion,
    producer_release_commit: str,
    publish_seconds: bool,
) -> None:
    """Exact source-scope opt-in; all PG writes occur in this one source process."""
    # Avoid a second producer: the native module already owns the PostgreSQL
    # exclusive session lock. The only caller is its main() branch.
    from operations.monitoring import native_public_trade_source as native

    symbols = {
        product: frozenset(
            item.dataset_topic.rsplit(".", 1)[-1]
            for item in union.websocket_topics
            if item.product == product
        )
        for product in ("SPOT", "LINEAR")
    }
    intervals = tuple(sorted({
        item.dataset_topic.split(".")[1]
        for item in union.websocket_topics
        if item.product == "LINEAR"
        and item.dataset_topic.startswith("kline.")
    }, key=int))
    sock: dict[str, Any] = {}
    buffer: dict[str, list[dict[str, object]]] = {}
    for product in ("SPOT", "LINEAR"):
        sock[product], buffer[product] = _connect_subscribe(
            product=product, union=union,
        )
    session = native.uuid4().hex
    native._session_start(connection, session)
    native._status("ACTIVE", session=session)
    done = threading.Event()
    failures: list[str] = []
    pipe: queue.Queue[tuple[str, dict[str, object], datetime]] = queue.Queue(
        maxsize=_MAX_QUEUE,
    )
    last_received = {k: time.monotonic() for k in sock}

    def read(product: str) -> None:
        try:
            pending = list(buffer[product])
            last_ping = time.monotonic()
            while not done.is_set() and not native.stopping:
                if pending:
                    item = pending.pop(0)
                else:
                    try:
                        raw = sock[product].recv()
                    except websocket.WebSocketTimeoutException:
                        raw = None
                    if raw == "":
                        raise PublicSourceSecondUnavailable(
                            "PUBLIC_FEED_STREAM_CLOSED"
                        )
                    item = json.loads(raw) if raw is not None else None
                now = time.monotonic()
                if isinstance(item, dict):
                    if item.get("op") == "pong" or item.get("ret_msg") == "pong":
                        last_received[product] = now
                    elif item.get("topic"):
                        last_received[product] = now
                        pipe.put((product, item, datetime.now(UTC)), timeout=0.25)
                if now - last_ping > 20:
                    sock[product].send('{"op":"ping"}')
                    last_ping = now
                if now - last_received[product] > 65:
                    raise PublicSourceSecondUnavailable(
                        "PUBLIC_FEED_TRANSPORT_HEARTBEAT_LOST"
                    )
        except Exception as exc:
            failures.append(f"{product}:{type(exc).__name__}")
            done.set()

    workers = [
        threading.Thread(target=read, args=(product,), daemon=True)
        for product in ("SPOT", "LINEAR")
    ]
    for worker in workers:
        worker.start()

    aggregate = PublicSecondAccumulator(session=session)
    # R05 publication keeps per-consumer TTL but one physical feed/ref;
    # native P90 original publicTrade remains the separate exact journal.
    compact_requirements = tuple(
        item for item in union.requirements if item.dataset in (
            "PUBLIC_TRADE", "TICKER", "ORDERBOOK_50", "LIQUIDATION",
        )
    )
    frame_index = 0
    last_published_second: datetime | None = None
    try:
        while not done.is_set() and not native.stopping:
            try:
                product, item, received = pipe.get(timeout=0.10)
            except queue.Empty:
                product, item, received = "", {}, datetime.now(UTC)
            if product:
                topic = str(item.get("topic") or "")
                if product == "LINEAR" and topic.startswith("publicTrade."):
                    symbol = topic.split(".", 1)[1]
                    if symbol in p90_symbols:
                        frame_index += 1
                        facts = native.decode_native_trades(
                            item, symbols=frozenset(p90_symbols),
                            release=producer_release_commit,
                        )
                        native._persist_frame(
                            connection, session=session,
                            frame_number=frame_index, facts=facts,
                        )
                if product == "LINEAR" and topic.startswith("kline."):
                    native._persist_closed_kline_frame(
                        connection, message=item,
                        symbols=tuple(sorted(symbols["LINEAR"])),
                        intervals=intervals,
                    )
                for event in decode_bybit_public_frame(
                    item, product=product,
                    allowed_symbols=symbols[product],
                    received_at=received,
                ):
                    aggregate.accept(event, received_at=received)
            # Commit only completed seconds and their source provenance.
            current = datetime.now(UTC)
            ready = aggregate.ready_seconds(before=current)
            if ready:
                persist_compact_source_seconds(
                    connection, rows=ready,
                    producer_release_commit=producer_release_commit,
                )
                aggregate.mark_committed(ready)
            if publish_seconds:
                # A single immutable publication per UTC second; not a
                # redundant DB scan/ON CONFLICT every 100ms of idle polling.
                current_second = current.replace(microsecond=0)
                if current_second != last_published_second:
                    publish_market_source_second(
                        connection, source_session_id=session,
                        producer_release_commit=producer_release_commit,
                        symbols=p90_symbols,
                        timeframes=intervals or ("1", "5", "15"),
                        public_requirements=compact_requirements,
                        observed_at=current_second,
                    )
                    last_published_second = current_second
        if failures:
            raise PublicSourceSecondUnavailable(
                "PUBLIC_FEED_TRANSPORT_FAILED"
            )
        native._session_gap(connection, session, "OWNER_CONTROLLED_STOP")
        native._status("STOPPED", session=session)
    except Exception as exc:
        native._session_gap(connection, session, type(exc).__name__)
        native._status(
            "RECONNECTING", session=session, reason=type(exc).__name__,
        )
        raise
    finally:
        done.set()
        for s in sock.values():
            s.close()
        for worker in workers:
            worker.join(timeout=2)
