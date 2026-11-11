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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import psycopg
import websocket

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data.market_publication import (
    publish_market_source_second,
)
from bybit_workbench.market_data.public_closed_kline_gap_repair import (
    ClosedGapPage,
    ClosedGapRepairUnavailable,
    commit_verified_closed_gap_pages,
    fetch_verified_bybit_closed_page,
    plan_missing_closed_pages,
)
from bybit_workbench.market_data.market_source_public_candles import (
    ClosedKlineSourceInvalid,
)
from bybit_workbench.market_data.public_forming_minute_source import (
    persist_forming_minute_fact,
)
from bybit_workbench.market_data.public_market_rest_source import (
    Dataset as RestDataset,
    PublicMarketRestFact,
    PublicMarketRestInvalid,
    fetch_public_market_rest,
)
from bybit_workbench.market_data.public_rest_observations import (
    persist_public_rest_fact,
)
from bybit_workbench.market_data.public_source_event_codec import (
    PublicSourceFrameInvalid,
    decode_bybit_public_frame,
)
from bybit_workbench.market_data.public_source_quality import (
    PublicSourceQualityEvent,
    Quality,
    persist_public_source_quality_events,
)
from bybit_workbench.market_data.public_source_second_store import (
    PublicSecondAccumulator,
    PublicSourceSecondUnavailable,
    persist_compact_source_seconds,
)
from bybit_workbench.market_data.shared_closed_candles import MarketCandleKey
from bybit_workbench.market_data.source_required_feed_manifest import (
    PublicFeedUnion,
    RequiredPublicFeed,
    SourceManifestInvalid,
    union_public_feed_requirements,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

_PUBLIC_WS = {
    "SPOT": "wss://stream.bybit.kz/v5/public/spot",
    "LINEAR": "wss://stream.bybit.kz/v5/public/linear",
}
_MAX_QUEUE = 8192
_REPAIR_PAGES_PER_WINDOW = 20
_REPAIR_RESCAN_SECONDS = 900
_REPAIR_PROVIDER_SPACING_SECONDS = 0.30
_REPAIR_PROVIDER_SETTLE_SECONDS = 10


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
    # Current capture activation cannot expand beyond the independently
    # reviewed native producer symbol set. Different SPOT/LINEAR products
    # of the SAME symbol remain valid; no new symbol can be silently
    # subscribed or REST-polled by an optional consumer requirement.
    approved = frozenset(native_symbols)
    if not approved or len(approved) != len(native_symbols):
        raise SourceManifestInvalid("SOURCE_NATIVE_SYMBOL_SCOPE_INVALID")
    if any(item.symbol not in approved for item in required):
        raise SourceManifestInvalid("SOURCE_OUTSIDE_ACTIVE_TRADING_UNIVERSE")
    feeds = union_public_feed_requirements(required)
    # Only the explicitly validated LINEAR one-minute forming adapter is
    # implemented. Confirmed SPOT closed bars use the *same canonical*
    # closed-candle store as LINEAR, never this forming adapter.
    if any(
        item.dataset == "FORMING_KLINE"
        and (item.product != "LINEAR" or item.timeframe != "1")
        for item in required
    ):
        raise SourceManifestInvalid("SOURCE_REQUIRED_FORMING_SCOPE_UNSUPPORTED")
    linear = {
        entry.dataset_topic
        for entry in feeds.websocket_topics if entry.product == "LINEAR"
    }
    for symbol in native_symbols:
        if f"publicTrade.{symbol}" not in linear:
            raise SourceManifestInvalid("SOURCE_P90_NATIVE_TOPIC_MISSING")
    # The five current P90 source consumers require exact confirmed LINEAR
    # 5m/15m close boundaries. A PUBLIC_TRADE or forming kline topic is not
    # a replacement, and an optional feed cannot claim a required window.
    # This only validates the declared feed contract: lookback/ATR/gap
    # readiness must still be proven by PostgreSQL at consumption time.
    confirmed = {
        (item.symbol, item.timeframe)
        for item in required
        if item.dataset == "CLOSED_KLINE"
        and item.product == "LINEAR" and item.required
    }
    if any(
        (symbol, timeframe) not in confirmed
        for symbol in native_symbols for timeframe in ("5", "15")
    ):
        raise SourceManifestInvalid("SOURCE_P90_REQUIRED_CLOSED_KLINE_MISSING")
    if not feeds.websocket_batches("SPOT"):
        raise SourceManifestInvalid("SOURCE_SPOT_TRANSPORT_MISSING")
    return feeds


def r03_closed_only_feed_union(
    approved: PublicFeedUnion, *, native_symbols: tuple[str, ...],
) -> PublicFeedUnion:
    """R03 stage: only native LINEAR original trades and confirmed 1/5/15.

    R04 spot/ticker/book/liquidation/REST and R05 publication remain inert.
    The exact input still comes from the full reviewed R01 operator contract.
    """
    if len(native_symbols) != 5 or len(set(native_symbols)) != 5:
        raise SourceManifestInvalid("R03_EXACT_FIVE_SYMBOL_SCOPE_REQUIRED")
    selected = tuple(
        item for item in approved.requirements
        if item.product == "LINEAR"
        and item.symbol in native_symbols
        and item.dataset in ("PUBLIC_TRADE", "CLOSED_KLINE")
    )
    physical = union_public_feed_requirements(selected)
    expected = {
        (symbol, dataset, timeframe)
        for symbol in native_symbols
        for dataset, timeframe in (
            ("PUBLIC_TRADE", ""), ("CLOSED_KLINE", "1"),
            ("CLOSED_KLINE", "5"), ("CLOSED_KLINE", "15"),
        )
    }
    actual = {
        (item.symbol, item.dataset, item.timeframe)
        for item in selected if item.required
    }
    if (
        not expected <= actual
        or physical.rest_inputs
        or physical.websocket_batches("SPOT")
        or len(physical.websocket_topics) != 20
    ):
        raise SourceManifestInvalid("R03_EXACT_CLOSED_ONLY_FEEDS_INVALID")
    return physical


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




def _scoped_quality_reason(exc: Exception, fallback: str) -> str:
    """Only validated typed codes may enter append-only quality evidence."""
    reason = str(exc)
    if (
        reason.startswith("PUBLIC_")
        and 3 <= len(reason) <= 128
        and all(ch == "_" or ch.isdigit() or "A" <= ch <= "Z"
                for ch in reason)
    ):
        return reason
    return fallback


def _invalidate_scoped_market(
    aggregate: PublicSecondAccumulator, *, topic: str, instrument_id: str,
) -> None:
    """No unrelated data-family reset from a scoped decoder/accumulator fault."""
    if topic.startswith("orderbook.50."):
        aggregate.invalidate_book(instrument_id)
    elif topic.startswith("tickers."):
        aggregate.invalidate_ticker(instrument_id)
    # publicTrade, kline, and liquidation have no derived book/ticker rights.


@dataclass(slots=True)
class _BookResubscribe:
    """One topic-only provider unsubscribe/subscribe with exact ACK fencing.

    No LINEAR transport reconnect and no second MarketSource writer. A
    successful subscribe ACK is NOT a snapshot/READY proof.
    """
    topic: str
    req_id: str
    phase: str
    deadline: float

    @classmethod
    def start(cls, topic: str, now: float) -> _BookResubscribe:
        if not topic.startswith("orderbook.50."):
            raise SourceManifestInvalid("PUBLIC_BOOK_RESUBSCRIBE_SCOPE_INVALID")
        return cls(
            topic=topic, req_id="cripta-book-off-" + uuid4().hex,
            phase="UNSUBSCRIBE", deadline=now + 12.0,
        )

    def command(self) -> str:
        return json.dumps({
            "op": ("unsubscribe" if self.phase == "UNSUBSCRIBE"
                   else "subscribe"),
            "args": [self.topic], "req_id": self.req_id,
        }, separators=(",", ":"))

    def on_ack(self, message: dict[str, object], now: float) -> str | None:
        if message.get("req_id") != self.req_id:
            return None
        if message.get("op") != (
            "unsubscribe" if self.phase == "UNSUBSCRIBE" else "subscribe"
        ) or message.get("success") is not True or message.get("retCode") not in (
            None, 0,
        ):
            raise PublicSourceSecondUnavailable(
                "PUBLIC_BOOK_RESUBSCRIBE_ACK_REJECTED"
            )
        if self.phase == "UNSUBSCRIBE":
            self.phase = "SUBSCRIBE"
            self.req_id = "cripta-book-on-" + uuid4().hex
            self.deadline = now + 12.0
            return self.command()
        if self.phase == "SUBSCRIBE":
            self.phase = "SNAPSHOT"
            self.deadline = now + 12.0
        return None

    def allows(self, message: dict[str, object]) -> bool:
        return (
            self.phase == "SNAPSHOT"
            and message.get("topic") == self.topic
            and message.get("type") == "snapshot"
        )


@dataclass(frozen=True, slots=True)
class _ClosedRepairWindow:
    key: MarketCandleKey
    required: int


@dataclass(frozen=True, slots=True)
class _ClosedRepairTask:
    window: _ClosedRepairWindow
    through: datetime
    pages: tuple[ClosedGapPage, ...]


def _closed_repair_windows(
    union: PublicFeedUnion, *, native_symbols: tuple[str, ...],
) -> tuple[_ClosedRepairWindow, ...]:
    """One real repair horizon per product/instrument/timeframe, not per consumer.

    The horizon must be explicit in each required consumer manifest. It is
    NEVER guessed from a Strategy name; optional-only feeds remain optional.
    """
    horizons: dict[tuple[str, str, str], int] = {}
    for item in union.requirements:
        if item.dataset != "CLOSED_KLINE" or not item.required:
            continue
        if (
            item.symbol not in native_symbols
            or not 1 <= item.lookback_bars <= 3000
        ):
            raise SourceManifestInvalid("SOURCE_REPAIR_WINDOW_NOT_DECLARED")
        k = (item.product, item.symbol, item.timeframe)
        horizons[k] = max(horizons.get(k, 0), item.lookback_bars)
    return tuple(
        _ClosedRepairWindow(
            key=MarketCandleKey(
                market_source_id=BYBIT_PUBLIC_MARKET_SOURCE_ID,
                venue_id="venue-bybit",
                instrument_id=bybit_instrument_id(product.lower(), symbol),
                external_symbol=symbol,
                product_category=product,
                timeframe=interval,
            ),
            required=required,
        )
        for (product, symbol, interval), required in sorted(horizons.items())
    )


def _last_settled_close(now: datetime, timeframe: str) -> datetime:
    """Avoid pretending a candle at a just-reached boundary is provider-ready."""
    if now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise ClosedGapRepairUnavailable("CLOSED_REPAIR_CLOCK_UNVERIFIED")
    step_seconds = int(timeframe) * 60
    settled = int((now - timedelta(seconds=_REPAIR_PROVIDER_SETTLE_SECONDS)).timestamp())
    return datetime.fromtimestamp(
        settled - settled % step_seconds, UTC,
    )


def _fetch_repair_task(
    task: _ClosedRepairTask, *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    fetch_page: Callable[..., tuple[Candle, ...]] = fetch_verified_bybit_closed_page,
    between_pages: Callable[[float], bool] = lambda _duration: False,
) -> tuple[tuple[Candle, ...], ...]:
    """Network-only: no PostgreSQL connection, service, Strategy or financial API."""
    result: list[tuple[Candle, ...]] = []
    for page in task.pages:
        if between_pages(0.0):
            raise ClosedGapRepairUnavailable("CLOSED_REPAIR_SOURCE_STOPPED")
        result.append(fetch_page(task.window.key, page, received_at=clock()))
        if between_pages(_REPAIR_PROVIDER_SPACING_SECONDS):
            raise ClosedGapRepairUnavailable("CLOSED_REPAIR_SOURCE_STOPPED")
    return tuple(result)


def run_source_union(
    connection: psycopg.Connection[Any], *,
    p90_symbols: tuple[str, ...],
    union: PublicFeedUnion,
    producer_release_commit: str,
    publish_seconds: bool,
    closed_only: bool = False,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """Exact stage-scoped source; all PG writes remain in one native process."""
    if closed_only and (
        publish_seconds
        or any(
            item.product != "LINEAR"
            or item.dataset not in ("PUBLIC_TRADE", "CLOSED_KLINE")
            for item in union.requirements
        )
        or union.rest_inputs
        or union.websocket_batches("SPOT")
    ):
        raise SourceManifestInvalid("R03_CLOSED_ONLY_SOURCE_SCOPE_DRIFT")
    products = ("LINEAR",) if closed_only else ("SPOT", "LINEAR")
    # Avoid a second producer: the native module already owns the PostgreSQL
    # exclusive session lock. The only caller is its main() branch.
    from operations.monitoring import native_public_trade_source as native

    # The physical producer is launched as __main__ by systemd; importing
    # it as a package creates a separate (inert) module stopping variable.
    # The active signal owner must be consulted before an explicit GAP.
    source_stopping = should_stop or (lambda: native.stopping)
    symbols = {
        product: frozenset(
            item.dataset_topic.rsplit(".", 1)[-1]
            for item in union.websocket_topics
            if item.product == product
        )
        for product in products
    }
    intervals = tuple(sorted({
        item.dataset_topic.split(".")[1]
        for item in union.websocket_topics
        if item.product == "LINEAR"
        and item.dataset_topic.startswith("kline.")
    }, key=int))
    spot_intervals = tuple(sorted({
        item.dataset_topic.split(".")[1]
        for item in union.websocket_topics
        if item.product == "SPOT"
        and item.dataset_topic.startswith("kline.")
    }, key=int))
    # Fail closed on an incomplete source/consumer horizon BEFORE opening
    # either public socket or creating an ACTIVE producer source epoch.
    repair_windows = _closed_repair_windows(union, native_symbols=p90_symbols)
    # R04: a required SPOT/REST feed cannot silently use optional isolation.
    if not closed_only and any(
        item.required and (
            item.product == "SPOT"
            or item.dataset in ("ACCOUNT_RATIO", "INSTRUMENT_INFO")
        )
        for item in union.requirements
    ):
        raise SourceManifestInvalid("R04_REQUIRED_OPTIONAL_TRANSPORT_UNSUPPORTED")
    sock: dict[str, Any] = {}
    buffer: dict[str, list[dict[str, object]]] = {}
    # The required LINEAR socket must be ready before the native session.
    # Optional SPOT connect/retry happens on its own worker after LINEAR start.
    sock["LINEAR"], buffer["LINEAR"] = _connect_subscribe(
        product="LINEAR", union=union,
    )
    session = native.uuid4().hex
    native._session_start(connection, session)
    native._status("ACTIVE", session=session)
    done = threading.Event()
    failures: list[str] = []  # Required LINEAR transport only.
    optional_faults: queue.Queue[PublicSourceQualityEvent] = queue.Queue(
        maxsize=128,
    )
    optional_audit_overflow = threading.Event()
    # A scoped book fault must never tear down LINEAR native publicTrade.
    # Only the existing owning network reader sends topic control messages.
    book_requests: dict[str, queue.Queue[str]] = {
        product: queue.Queue(maxsize=max(5, len(symbols[product])))
        for product in products
    }
    book_request_after: dict[tuple[str, str], float] = {}
    book_waiting_snapshot: dict[tuple[str, str], datetime] = {}

    def request_book_snapshot(
        product: str, topic: str, *, received_at: datetime,
    ) -> None:
        key = (product, topic)
        if key not in book_waiting_snapshot:
            book_waiting_snapshot[key] = received_at
        now = time.monotonic()
        if now < book_request_after.get(key, -1.0):
            return
        # Coalesce up to 50 book deltas/sec into ONE bounded repair attempt.
        book_request_after[key] = now + 45.0
        try:
            book_requests[product].put_nowait(topic)
        except queue.Full:
            note_quality(
                product=product, topic=topic,
                symbol=topic.rsplit(".", 1)[-1], quality="UNKNOWN",
                reason="PUBLIC_BOOK_RESUBSCRIBE_QUEUE_FULL",
            )

    transport_epochs: dict[str, str] = {"LINEAR": session}
    fault_seen: dict[tuple[str, str, str, str, str, str], float] = {}
    fault_lock = threading.Lock()

    def note_quality(
        *, product: str, topic: str, symbol: str = "",
        quality: str, reason: str, epoch: str | None = None,
    ) -> None:
        # Network/REST workers enqueue immutable evidence; ONLY the native
        # advisory-lock owner is authorized to persist it in PostgreSQL.
        transport_epoch = epoch or transport_epochs.get(product, session)
        key = (product, topic, symbol, quality, reason, transport_epoch)
        now = time.monotonic()
        with fault_lock:
            if now - fault_seen.get(key, -100000.0) < 60.0:
                return
            if len(fault_seen) >= 512:
                fault_seen.clear()
            fault_seen[key] = now
        event = PublicSourceQualityEvent.create(
            source_session_id=session, transport_epoch_id=transport_epoch,
            product=product, topic=topic, symbol=symbol,
            quality=cast(Quality, quality), reason=reason,
            observed_at=datetime.now(UTC),
        )
        try:
            optional_faults.put_nowait(event)
        except queue.Full:
            # Explicitly signal missing quality audit, not a false healthy
            # status or unrelated LINEAR transport GAP.
            optional_audit_overflow.set()

    pipe: queue.Queue[
        tuple[str, str, dict[str, object], datetime]
    ] = queue.Queue(maxsize=_MAX_QUEUE)
    # Network workers cannot write PG, so the source session/PG advisory lock
    # remains the single writer across public WS and REST.
    rest_pipe: queue.Queue[PublicMarketRestFact] = queue.Queue(maxsize=256)
    rest_needs = tuple(sorted({
        (item.product, item.symbol, item.dataset)
        for item in union.requirements
        if item.dataset in ("ACCOUNT_RATIO", "INSTRUMENT_INFO")
    }))
    rest_freshness = {
        feed: min(
            item.freshness_seconds for item in union.requirements
            if (item.product, item.symbol, item.dataset) == feed
        )
        for feed in rest_needs
    }
    forming_needs = frozenset({
        (item.product, item.symbol, item.timeframe)
        for item in union.requirements
        if item.dataset == "FORMING_KLINE"
    })
    last_received = {k: time.monotonic() for k in sock}
    # R03: bounded restoration uses a separate network-only worker. All PG
    # planning, original trade journaling and candle writes stay on THIS
    # advisory-lock-owning source thread. Work/results each have one slot.
    repair_jobs: queue.Queue[_ClosedRepairTask] = queue.Queue(maxsize=1)
    repair_results: queue.Queue[
        tuple[_ClosedRepairTask, tuple[tuple[Candle, ...], ...], str]
    ] = queue.Queue(maxsize=1)

    def read(product: str) -> None:
        pending = list(buffer.get(product, []))
        last_ping = time.monotonic()
        book_states: dict[str, _BookResubscribe] = {}
        known_books = frozenset(
            item.dataset_topic for item in union.websocket_topics
            if item.product == product
            and item.dataset_topic.startswith("orderbook.50.")
        )
        while not done.is_set() and not source_stopping():
            try:
                if product not in sock:
                    # Every reconnect is a NEW, independent transport epoch;
                    # it never repairs source gaps from a previous epoch.
                    next_epoch = uuid4().hex
                    transport_epochs[product] = next_epoch
                    current_sock, early = _connect_subscribe(
                        product=product, union=union,
                    )
                    sock[product] = current_sock
                    pending.extend(early)
                    last_ping = time.monotonic()
                    last_received[product] = last_ping
                    if product == "SPOT":
                        note_quality(
                            product=product, topic="*", quality="RECOVERING",
                            reason="PUBLIC_SPOT_CONNECTED_NOT_CONTINUOUS",
                        )
                # Do not stop/reconnect the native LINEAR reader when a
                # derived book loses a snapshot: resubscribe ONLY its topic.
                try:
                    requested_topic = book_requests[product].get_nowait()
                except queue.Empty:
                    requested_topic = ""
                if requested_topic and requested_topic in known_books:
                    if requested_topic not in book_states:
                        state = _BookResubscribe.start(
                            requested_topic, time.monotonic(),
                        )
                        try:
                            sock[product].send(state.command())
                            book_states[requested_topic] = state
                        except (OSError, websocket.WebSocketException):
                            note_quality(
                                product=product, topic=requested_topic,
                                symbol=requested_topic.rsplit(".", 1)[-1],
                                quality="UNKNOWN",
                                reason="PUBLIC_BOOK_RESUBSCRIBE_SEND_FAILED",
                            )
                for stale_topic, state in tuple(book_states.items()):
                    if time.monotonic() > state.deadline:
                        note_quality(
                            product=product, topic=stale_topic,
                            symbol=stale_topic.rsplit(".", 1)[-1],
                            quality="UNKNOWN",
                            reason="PUBLIC_BOOK_RESUBSCRIBE_TIMEOUT",
                        )
                        del book_states[stale_topic]
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
                    elif item.get("op") in ("unsubscribe", "subscribe"):
                        last_received[product] = now
                        for topic, state in tuple(book_states.items()):
                            if item.get("req_id") != state.req_id:
                                continue
                            try:
                                next_command = state.on_ack(item, now)
                                if next_command:
                                    sock[product].send(next_command)
                            except (
                                PublicSourceSecondUnavailable, OSError,
                                websocket.WebSocketException,
                            ):
                                note_quality(
                                    product=product, topic=topic,
                                    symbol=topic.rsplit(".", 1)[-1],
                                    quality="UNKNOWN",
                                    reason="PUBLIC_BOOK_RESUBSCRIBE_ACK_INVALID",
                                )
                                del book_states[topic]
                            break
                    elif item.get("topic"):
                        last_received[product] = now
                        topic = str(item["topic"])
                        recovery = book_states.get(topic)
                        if recovery is not None and not recovery.allows(item):
                            # No stale book delta/old socket frame may satisfy
                            # a new snapshot requirement.
                            continue
                        pipe.put(
                            (product, transport_epochs[product],
                             item, datetime.now(UTC)),
                            timeout=0.25,
                        )
                        if recovery is not None:
                            # Only the source writer may assert verified
                            # snapshot after decoding + applying this frame.
                            del book_states[topic]
                if now - last_ping > 20:
                    sock[product].send('{"op":"ping"}')
                    last_ping = now
                if now - last_received[product] > 65:
                    raise PublicSourceSecondUnavailable(
                        "PUBLIC_FEED_TRANSPORT_HEARTBEAT_LOST"
                    )
            except Exception as exc:
                if product == "LINEAR":
                    failures.append(f"{product}:{type(exc).__name__}")
                    done.set()
                    return
                # Only SPOT is optional in the exact approved R01 manifest.
                # Never GAP the original LINEAR journal for its outage.
                prior_epoch = transport_epochs.get(product, session)
                was_connected = product in sock
                # Invalidate queued frames IMMEDIATELY on disconnect.
                # Even while retry is waiting, the previous epoch must not
                # be accepted into the current compact market source.
                transport_epochs[product] = uuid4().hex
                note_quality(
                    product="SPOT", topic="*",
                    quality=("GAP" if was_connected else "NO_DATA"),
                    reason="PUBLIC_SPOT_TRANSPORT_UNAVAILABLE",
                    epoch=prior_epoch,
                )
                previous = sock.pop(product, None)
                if previous is not None:
                    try:
                        previous.close()
                    except Exception:
                        note_quality(
                            product="SPOT", topic="*", quality="UNKNOWN",
                            reason="PUBLIC_SPOT_SOCKET_CLOSE_UNVERIFIED",
                            epoch=prior_epoch,
                        )
                pending.clear()
                book_states.clear()
                if done.wait(5.0):
                    return

    workers = [
        threading.Thread(target=read, args=(product,), daemon=True)
        for product in products
    ]

    def read_rest() -> None:
        # No implicit burst retry: one source-owned bounded poll cadence per
        # explicit physical REST feed. A failure leaves missing/stale evidence
        # in the shared publication. The original WS reader is uninterrupted.
        due = {feed: 0.0 for feed in rest_needs}
        last_received_rest: dict[tuple[str, str, str], float] = {}
        while not done.is_set() and not source_stopping():
            for product, symbol, dataset in rest_needs:
                if done.is_set() or source_stopping():
                    return
                key = (product, symbol, dataset)
                now = time.monotonic()
                if now < due[key]:
                    continue
                due[key] = now + (
                    300.0 if dataset == "ACCOUNT_RATIO" else 900.0
                )
                try:
                    fact = fetch_public_market_rest(
                        dataset=cast(RestDataset, dataset),
                        product=product, symbol=symbol,
                    )
                    rest_pipe.put(fact, timeout=0.25)
                    last_received_rest[key] = time.monotonic()
                except PublicMarketRestInvalid:
                    # No false healthy response, no silence on optional REST.
                    note_quality(
                        product=product, symbol=symbol,
                        topic=("REST:market/account-ratio:" + symbol
                               if dataset == "ACCOUNT_RATIO"
                               else "REST:market/instruments-info:" + symbol),
                        quality=(
                            "STALE"
                            if key in last_received_rest and (
                                now - last_received_rest[key]
                                > rest_freshness[key]
                            )
                            else "NO_DATA"
                        ),
                        reason="PUBLIC_REST_UNVERIFIED",
                    )
                    continue
                except (OSError, TimeoutError):
                    note_quality(
                        product=product, symbol=symbol,
                        topic=("REST:market/account-ratio:" + symbol
                               if dataset == "ACCOUNT_RATIO"
                               else "REST:market/instruments-info:" + symbol),
                        quality=(
                            "STALE"
                            if key in last_received_rest and (
                                now - last_received_rest[key]
                                > rest_freshness[key]
                            )
                            else "NO_DATA"
                        ),
                        reason="PUBLIC_REST_UNAVAILABLE",
                    )
                    continue
                except queue.Full:
                    note_quality(
                        product=product, symbol=symbol,
                        topic=("REST:market/account-ratio:" + symbol
                               if dataset == "ACCOUNT_RATIO"
                               else "REST:market/instruments-info:" + symbol),
                        quality="UNKNOWN", reason="PUBLIC_REST_PIPE_OVERFLOW",
                    )
                    continue
            done.wait(0.20)

    if rest_needs:
        workers.append(threading.Thread(target=read_rest, daemon=True))

    def read_closed_gap_repair() -> None:
        # Strictly no DB session/connection in network workers. A failed
        # provider page is a scoped R03 repair fault, not a reason to stop
        # original native publicTrade.
        while not done.is_set() and not source_stopping():
            try:
                task = repair_jobs.get(timeout=0.20)
            except queue.Empty:
                continue
            try:
                batches = _fetch_repair_task(
                    task, between_pages=done.wait,
                )
                reason = ""
            except (ClosedGapRepairUnavailable, OSError, ValueError) as exc:
                batches, reason = (), str(exc)
            except Exception:
                batches, reason = (), "CLOSED_REPAIR_PROVIDER_UNVERIFIED"
            while not done.is_set() and not source_stopping():
                try:
                    repair_results.put((task, batches, reason), timeout=0.20)
                    break
                except queue.Full:
                    continue

    if repair_windows:
        workers.append(threading.Thread(
            target=read_closed_gap_repair, daemon=True,
        ))
    for worker in workers:
        worker.start()

    aggregate = PublicSecondAccumulator(session=session)
    # R05 publication keeps per-consumer TTL but one physical feed/ref;
    # native P90 original publicTrade remains the separate exact journal.
    public_requirements = tuple(
        item for item in union.requirements if item.dataset in (
            "PUBLIC_TRADE", "TICKER", "ORDERBOOK_50", "LIQUIDATION",
            "ACCOUNT_RATIO", "INSTRUMENT_INFO", "FORMING_KLINE",
            "CLOSED_KLINE",
        )
    )
    def flush_quality() -> None:
        # Durable scoped quality audit, written only by the one native
        # PostgreSQL owner, never by network workers or REST threads.
        quality_batch: list[PublicSourceQualityEvent] = []
        for _ in range(32):
            try:
                quality_batch.append(optional_faults.get_nowait())
            except queue.Empty:
                break
        if optional_audit_overflow.is_set():
            quality_batch.append(PublicSourceQualityEvent.create(
                source_session_id=session,
                transport_epoch_id=transport_epochs.get("SPOT", session),
                product="SPOT", topic="*", quality="UNKNOWN",
                reason="PUBLIC_OPTIONAL_QUALITY_QUEUE_OVERFLOW",
                observed_at=datetime.now(UTC),
            ))
            optional_audit_overflow.clear()
        if quality_batch:
            persist_public_source_quality_events(
                connection, events=tuple(quality_batch),
                producer_release_commit=producer_release_commit,
            )
            for fault in quality_batch:
                if fault.product == "SPOT" and fault.topic == "*":
                    for symbol in symbols.get("SPOT", ()):
                        aggregate.invalidate_market(
                            bybit_instrument_id("spot", symbol)
                        )
                native._status(
                    "ACTIVE", session=session,
                    reason=(
                        "R04_OPTIONAL:" + fault.product + ":"
                        + fault.quality + ":" + fault.reason
                    )[:160],
                )

    frame_index = 0
    last_published_second: datetime | None = None
    repair_index = 0
    next_repair_scan_at = 0.0
    # A worker can consume its queued job before posting its result; both
    # queue.empty() values then appear true. The owner thread must retain
    # a separate causal inflight gate until that precise result is handled.
    repair_inflight = False
    try:
        while not done.is_set() and not source_stopping():
            # Retry a bounded topic-only snapshot request even when the
            # provider sends only deltas (which are intentionally ignored
            # until a fresh snapshot). Never wait for another decoder fault.
            for (book_product, book_topic), since in tuple(
                book_waiting_snapshot.items()
            ):
                if time.monotonic() >= book_request_after.get(
                    (book_product, book_topic), -1.0,
                ):
                    request_book_snapshot(
                        book_product, book_topic, received_at=since,
                    )
            try:
                product, frame_epoch, item, received = pipe.get(timeout=0.10)
            except queue.Empty:
                product, frame_epoch, item, received = (
                    "", "", {}, datetime.now(UTC),
                )
            if product:
                # Buffered frames from an already disconnected SPOT epoch
                # cannot populate the new transport's current read-model.
                if (
                    product == "SPOT"
                    and frame_epoch != transport_epochs.get("SPOT")
                ):
                    continue
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
                key = (product, topic)
                if key in book_waiting_snapshot and (
                    received < book_waiting_snapshot[key]
                    or item.get("type") != "snapshot"
                ):
                    # Ignore only the stale book frame. Do not skip the
                    # source loop's REST/quality/compact/repair maintenance.
                    item = {}
                    topic = ""
                if topic.startswith("kline.") and product in (
                    "LINEAR", "SPOT",
                ):
                    try:
                        native._persist_closed_kline_frame(
                            connection, message=item,
                            symbols=tuple(sorted(symbols[product])),
                            intervals=(
                                intervals if product == "LINEAR"
                                else spot_intervals
                            ),
                            product=product,
                        )
                    except ClosedKlineSourceInvalid:
                        if product != "SPOT":
                            raise
                        note_quality(
                            product="SPOT", topic=topic,
                            symbol=topic.rsplit(".", 1)[-1],
                            quality="UNKNOWN",
                            reason="PUBLIC_SPOT_CLOSED_KLINE_UNVERIFIED",
                            epoch=frame_epoch,
                        )
                        continue
                # Physical required=True belongs to one specific consumer.
                # A compact/ticker/book decoder fault blocks that dataset,
                # not the already committed original LINEAR trade journal.
                scoped_topic = any(
                    feed.product == product and feed.dataset_topic == topic
                    for feed in union.websocket_topics
                )
                try:
                    events = (() if closed_only else decode_bybit_public_frame(
                        item, product=product,
                        allowed_symbols=symbols[product],
                        received_at=received,
                    ))
                except PublicSourceFrameInvalid as exc:
                    if not scoped_topic:
                        raise
                    instrument = bybit_instrument_id(
                        product.lower(), topic.rsplit(".", 1)[-1],
                    )
                    _invalidate_scoped_market(
                        aggregate, topic=topic, instrument_id=instrument,
                    )
                    if topic.startswith("orderbook.50."):
                        request_book_snapshot(
                            product, topic, received_at=received,
                        )
                    # A stale/malformed FORMING_KLINE can NEVER destroy an
                    # unrelated valid book, ticker or original native trade.
                    reason = _scoped_quality_reason(
                        exc, "PUBLIC_TOPIC_FRAME_UNVERIFIED",
                    )
                    note_quality(
                        product=product, topic=topic,
                        symbol=topic.rsplit(".", 1)[-1],
                        quality="UNKNOWN",
                        reason=reason,
                        epoch=frame_epoch,
                    )
                    events = ()
                for event in events:
                    if event.kind == "FORMING_KLINE":
                        if (
                            event.product, event.symbol,
                            event.payload["timeframe"],
                        ) in forming_needs:
                            persist_forming_minute_fact(
                                connection, source_session_id=session,
                                producer_release_commit=producer_release_commit,
                                fact=event, received_at=received,
                            )
                    else:
                        try:
                            aggregate.accept(event, received_at=received)
                        except (PublicSourceFrameInvalid,
                                PublicSourceSecondUnavailable) as exc:
                            if not scoped_topic:
                                raise
                            _invalidate_scoped_market(
                                aggregate, topic=topic,
                                instrument_id=event.instrument_id,
                            )
                            if event.kind == "BOOK":
                                request_book_snapshot(
                                    product, topic, received_at=received,
                                )
                            # Trade/liquidation faults cannot erase a good
                            # ticker/book for the same InstrumentIdentity.
                            reason = _scoped_quality_reason(
                                exc, "PUBLIC_TOPIC_ACCUMULATOR_UNVERIFIED",
                            )
                            note_quality(
                                product=product, topic=topic,
                                symbol=event.symbol, quality="UNKNOWN",
                                reason=reason,
                                epoch=frame_epoch,
                            )
                        else:
                            if (
                                event.kind == "BOOK"
                                and event.frame_type == "snapshot"
                                and key in book_waiting_snapshot
                            ):
                                del book_waiting_snapshot[key]
                                note_quality(
                                    product=product, topic=topic,
                                    symbol=event.symbol, quality="RECOVERING",
                                    reason="PUBLIC_BOOK_SNAPSHOT_RESTORED",
                                    epoch=frame_epoch,
                                )
            flush_quality()
            # Handle bounded REST receipts only on the one PG source writer.
            # Never insert the REST fact directly from its network worker.
            for _ in range(8):
                try:
                    rest_fact = rest_pipe.get_nowait()
                except queue.Empty:
                    break
                persist_public_rest_fact(
                    connection, source_session_id=session,
                    producer_release_commit=producer_release_commit,
                    fact=rest_fact,
                )
                dataset_topic = (
                    "REST:market/account-ratio:" + rest_fact.symbol
                    if rest_fact.dataset == "ACCOUNT_RATIO"
                    else "REST:market/instruments-info:" + rest_fact.symbol
                )
                is_not_listed = rest_fact.values.get("status") == "NOT_LISTED"
                # A committed REST fact permits *scoped* causal recovery
                # evidence. RECOVERING is intentionally not global READY;
                # publication must still verify each consumer freshness TTL.
                persist_public_source_quality_events(
                    connection,
                    events=(PublicSourceQualityEvent.create(
                        source_session_id=session,
                        transport_epoch_id=session,
                        product=rest_fact.product, topic=dataset_topic,
                        symbol=rest_fact.symbol,
                        quality=("NOT_LISTED" if is_not_listed
                                 else "RECOVERING"),
                        reason=("PUBLIC_INSTRUMENT_NOT_LISTED"
                                if is_not_listed
                                else "PUBLIC_REST_FACT_COMMITTED"),
                        observed_at=rest_fact.received_at,
                    ),),
                    producer_release_commit=producer_release_commit,
                )
            # Preserve original native WS priority: only after processing
            # its frame and REST receipts can one complete gap result commit.
            if repair_windows:
                try:
                    task, batches, repair_reason = repair_results.get_nowait()
                except queue.Empty:
                    pass
                else:
                    repair_inflight = False
                    if repair_reason:
                        native._status(
                            "ACTIVE", session=session,
                            reason="CLOSED_REPAIR_BLOCKED:" + repair_reason[:120],
                        )
                    else:
                        try:
                            commit_verified_closed_gap_pages(
                                connection, key=task.window.key,
                                native_symbols=p90_symbols,
                                source_session_id=session,
                                producer_release_commit=producer_release_commit,
                                through=task.through,
                                required=task.window.required,
                                pages=task.pages, batches=batches,
                                observed_at=datetime.now(UTC),
                            )
                        except (ClosedGapRepairUnavailable, RuntimeError) as exc:
                            # Not a false green history claim, and never a
                            # global trading/MarketSource shutdown for a
                            # scoped closed-bar source-quality fault.
                            native._status(
                                "ACTIVE", session=session,
                                reason=(
                                    "CLOSED_REPAIR_BLOCKED:"
                                    + str(exc)[:120]
                                ),
                            )
            # Schedule at most one PG-only gap plan per loop iteration.
            # Upstream REST is always asynchronous and finite.
            if (
                repair_windows
                and time.monotonic() >= next_repair_scan_at
                and not repair_inflight
                and repair_jobs.empty() and repair_results.empty()
            ):
                window = repair_windows[repair_index]
                repair_index += 1
                if repair_index == len(repair_windows):
                    repair_index = 0
                    next_repair_scan_at = (
                        time.monotonic() + _REPAIR_RESCAN_SECONDS
                    )
                current_utc = datetime.now(UTC)
                through = _last_settled_close(
                    current_utc, window.key.timeframe,
                )
                try:
                    pages = plan_missing_closed_pages(
                        connection, key=window.key,
                        native_symbols=p90_symbols,
                        through=through, required=window.required,
                        observed_at=current_utc,
                    )
                    if len(pages) > _REPAIR_PAGES_PER_WINDOW:
                        raise ClosedGapRepairUnavailable(
                            "CLOSED_REPAIR_PAGE_BUDGET_EXCEEDED"
                        )
                    if pages:
                        repair_jobs.put_nowait(
                            _ClosedRepairTask(window, through, pages)
                        )
                        repair_inflight = True
                except (ClosedGapRepairUnavailable, queue.Full) as exc:
                    native._status(
                        "ACTIVE", session=session,
                        reason="CLOSED_REPAIR_BLOCKED:" + str(exc)[:120],
                    )
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
                        public_requirements=public_requirements,
                        observed_at=current_second,
                    )
                    last_published_second = current_second
        # Drain source-quality events before the native epoch turns GAP.
        # A clean owner stop cannot erase the final optional failure.
        flush_quality()
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
