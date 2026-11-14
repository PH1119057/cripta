"""#470 R11: pure original optional public frames -> legacy MAYAK event inputs.

All input rows must first pass the exact PG READ ONLY original-frame reader.
This adds no market sockets, PostgreSQL writer, source subscriptions or live
consumer hooks. SPOT TRADE, SPOT/LINEAR BOOK snapshots+deltas, LINEAR TICKER
partial updates and LINEAR LIQUIDATION preserve the *legacy* on_* arguments.
Original LINEAR TRADE is owned by a separate native replay adapter.

Each result remains source-only: a hash-verified DB page is not proof that
the provider delivered every frame, nor that MAYAK's seven-symbol/REST
inputs, warmup, coverage or Dispatcher context are equivalent. In particular
BOOK delta processing needs a *real* snapshot in the same transport epoch.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, cast

from bybit_workbench.market_data.public_source_event_codec import (
    PublicSourceFact,
    PublicSourceFrameInvalid,
    decode_bybit_public_frame,
)
from bybit_workbench.market_data.r11_optional_original_frame_journal import (
    R11OriginalFrameUnavailable,
    R11OriginalPublicFrame,
    make_r11_original_optional_frame,
)
from bybit_workbench.mayak.core.live import LiveMayakEngine
from bybit_workbench.mayak.optional_original_public_frame_reader import (
    R11OriginalFramePage,
)

_FIVE = frozenset(("APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT"))
_SHA32 = re.compile(r"^[0-9a-f]{32}$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA64 = re.compile(r"^[0-9a-f]{64}$")
_EVENT_LIMIT = 4096
_LADDER_LIMIT = 1024
_TICKER_NAMES = (
    ("openInterest", "open_interest"),
    ("openInterestValue", "open_interest_value"),
    ("fundingRate", "funding_rate"),
    ("lastPrice", "last_price"),
    ("markPrice", "mark_price"),
    ("indexPrice", "index_price"),
)
Event = Literal["TRADE", "BOOK", "TICKER", "LIQUIDATION"]


class R11OptionalMayakReplayUnverified(RuntimeError):
    """Original optional frame identity, sequence or MAYAK argument not proven."""


@dataclass(frozen=True, slots=True)
class R11MayakBookState:
    symbol: str
    source_sequence: int
    update_id: int
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class R11OptionalMayakCursor:
    source_session_id: str
    transport_epoch_id: str
    product_category: str
    producer_release_commit: str
    next_cursor: int
    next_digest: str | None
    books: tuple[R11MayakBookState, ...]


@dataclass(frozen=True, slots=True)
class R11OptionalMayakEvent:
    """All inputs for exactly ONE real legacy engine invocation."""

    event: Event
    product: str
    symbol: str
    timestamp: float
    source_frame_number: int
    source_fact_index: int
    source_sequence: int | None = None
    exec_id: str | None = None
    side: str | None = None
    price: float | None = None
    quantity: float | None = None
    bids: tuple[tuple[float, float], ...] = ()
    asks: tuple[tuple[float, float], ...] = ()
    ticker_updates: tuple[tuple[str, float], ...] = ()

    def apply_offline(self, engine: LiveMayakEngine) -> None:
        """Call actual MAYAK formulas in disposable parity/replay tests only."""
        market = self.product.lower()
        if self.event == "TRADE":
            if (
                self.side not in ("Buy", "Sell")
                or self.price is None or self.quantity is None
            ):
                raise R11OptionalMayakReplayUnverified(
                    "R11_OPTIONAL_MAYAK_TRADE_ARGUMENTS_INVALID"
                )
            engine.on_trade(
                market, self.symbol, self.timestamp, self.side,
                self.price, self.quantity,
            )
        elif self.event == "BOOK":
            _need(bool(self.bids) and bool(self.asks), "BOOK_ARGUMENTS_INVALID")
            engine.on_book(
                market, self.symbol, self.timestamp,
                list(self.bids), list(self.asks),
            )
        elif self.event == "TICKER":
            _need(self.product == "LINEAR", "TICKER_PRODUCT_INVALID")
            engine.on_ticker(
                self.symbol, self.timestamp, **dict(self.ticker_updates),
            )
        else:
            if (
                self.event != "LIQUIDATION"
                or self.product != "LINEAR"
                or self.side not in ("Buy", "Sell")
                or self.price is None or self.quantity is None
            ):
                raise R11OptionalMayakReplayUnverified(
                    "R11_OPTIONAL_MAYAK_LIQUIDATION_ARGUMENTS_INVALID"
                )
            engine.on_liquidation(
                self.symbol, self.timestamp, self.side,
                self.price, self.quantity,
            )


@dataclass(frozen=True, slots=True)
class R11OptionalMayakBatch:
    cursor: R11OptionalMayakCursor
    events: tuple[R11OptionalMayakEvent, ...]
    has_more_pages: bool
    # A source-hash checked replay is not a source-coverage proof.
    provider_transport_continuity_verified: bool = False
    seven_symbol_panel_verified: bool = False
    optional_rest_feature_parity_verified: bool = False
    safe_to_switch_mayak_public_socket: bool = False


def _need(ok: bool, code: str) -> None:
    if not ok:
        raise R11OptionalMayakReplayUnverified("R11_OPTIONAL_MAYAK_" + code)


def _numeric(value: object, *, zero: bool = False, signed: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(
        value, (str, int, Decimal, float),
    ):
        raise R11OptionalMayakReplayUnverified(
            "R11_OPTIONAL_MAYAK_NUMBER_TYPE_INVALID"
        )
    try:
        num = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise R11OptionalMayakReplayUnverified(
            "R11_OPTIONAL_MAYAK_NUMBER_INVALID"
        ) from exc
    _need(
        math.isfinite(num)
        and (True if signed else (num >= 0 if zero else num > 0)),
        "NUMBER_NONFINITE_OR_SIGN_INVALID",
    )
    return num


def _utc(at: datetime) -> datetime:
    _need(
        isinstance(at, datetime)
        and at.tzinfo is not None
        and at.utcoffset() == timedelta(0),
        "CLOCK_NOT_UTC",
    )
    return at.astimezone(UTC)


def _levels(
    value: object,
    *,
    allow_zero: bool,
) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, tuple) or len(value) > 100:
        raise R11OptionalMayakReplayUnverified(
            "R11_OPTIONAL_MAYAK_LEVELS_INVALID"
        )
    out: list[tuple[float, float]] = []
    seen: set[float] = set()
    for level in value:
        _need(
            isinstance(level, tuple) and len(level) == 2,
            "LEVEL_SHAPE_INVALID",
        )
        price = _numeric(level[0])
        size = _numeric(level[1], zero=allow_zero)
        _need(price not in seen and math.isfinite(price * size),
              "LEVEL_DUPLICATE_OR_OVERFLOW")
        out.append((price, size))
        seen.add(price)
    return tuple(out)


def _book_next(
    previous: R11MayakBookState | None,
    *,
    fact: PublicSourceFact,
) -> R11MayakBookState:
    snapshot = fact.frame_type == "snapshot"
    _need(
        fact.kind == "BOOK"
        and fact.exchange_sequence is not None
        and type(fact.exchange_sequence) is int
        and fact.exchange_sequence >= 0
        and fact.frame_type in ("snapshot", "delta"),
        "BOOK_FRAME_INVALID",
    )
    update = fact.payload.get("update_id")
    _need(type(update) is int and update >= 0, "BOOK_UPDATE_INVALID")
    if type(update) is not int:
        raise R11OptionalMayakReplayUnverified(
            "R11_OPTIONAL_MAYAK_BOOK_UPDATE_INVALID"
        )
    if type(fact.exchange_sequence) is not int:
        raise R11OptionalMayakReplayUnverified(
            "R11_OPTIONAL_MAYAK_BOOK_FRAME_INVALID"
        )
    if not snapshot:
        if previous is None:
            raise R11OptionalMayakReplayUnverified(
                "R11_OPTIONAL_MAYAK_BOOK_DELTA_WITHOUT_SNAPSHOT"
            )
        _need(
            update != 1
            and fact.exchange_sequence > previous.source_sequence
            and update > previous.update_id,
            "BOOK_SEQ_OR_RESET_UNVERIFIED",
        )
    current_bid = (
        {} if snapshot or previous is None else dict(previous.bids)
    )
    current_ask = (
        {} if snapshot or previous is None else dict(previous.asks)
    )
    bids = _levels(fact.payload.get("bids"), allow_zero=not snapshot)
    asks = _levels(fact.payload.get("asks"), allow_zero=not snapshot)
    for original, changes in ((current_bid, bids), (current_ask, asks)):
        for price, size in changes:
            if size == 0:
                original.pop(price, None)
            else:
                original[price] = size
    _need(
        0 < len(current_bid) <= _LADDER_LIMIT
        and 0 < len(current_ask) <= _LADDER_LIMIT
        and max(current_bid) < min(current_ask),
        "BOOK_EMPTY_CROSSED_OR_UNBOUNDED",
    )
    return R11MayakBookState(
        symbol=fact.symbol,
        source_sequence=fact.exchange_sequence,
        update_id=update,
        bids=tuple(sorted(current_bid.items(), reverse=True)),
        asks=tuple(sorted(current_ask.items())),
    )


def _canonical_frame(
    frame: R11OriginalPublicFrame,
    *,
    source: R11OriginalFramePage,
) -> tuple[PublicSourceFact, ...]:
    _need(isinstance(frame, R11OriginalPublicFrame), "FRAME_TYPE_INVALID")
    _need(
        frame.market_source_id == "ms-bybit-mainnet-public-v1"
        and frame.source_session_id == source.source_session_id
        and frame.transport_epoch_id == source.transport_epoch_id
        and frame.producer_release_commit == source.producer_release_commit
        and frame.product_category == source.product_category
        and frame.external_symbol in _FIVE
        and frame.transport_continuity_verified is False
        and frame.mayak_event_replay_ready is False,
        "FRAME_SOURCE_SCOPE_INVALID",
    )
    try:
        facts = decode_bybit_public_frame(
            frame.provider_frame,
            product=cast(Any, frame.product_category),
            allowed_symbols=frozenset((frame.external_symbol,)),
            received_at=frame.received_at,
        )
        recomputed = make_r11_original_optional_frame(
            source_session_id=source.source_session_id,
            transport_epoch_id=source.transport_epoch_id,
            producer_release_commit=source.producer_release_commit,
            product=source.product_category,
            source_frame_number=frame.source_frame_number,
            message=frame.provider_frame,
            facts=facts,
            received_at=frame.received_at,
        )
    except (
        PublicSourceFrameInvalid, R11OriginalFrameUnavailable,
        TypeError, ValueError,
    ) as exc:
        raise R11OptionalMayakReplayUnverified(
            "R11_OPTIONAL_MAYAK_FRAME_CODEC_REJECTED"
        ) from exc
    _need(
        recomputed == frame
        and len(facts) == len(frame.decoded_original_facts),
        "FRAME_HASH_OR_FACT_DRIFT",
    )
    return facts


def project_r11_optional_mayak_page(
    page: R11OriginalFramePage,
    *,
    previous: R11OptionalMayakCursor | None = None,
) -> R11OptionalMayakBatch:
    """Convert one verified original optional page, preserving replay order.

    The caller must persist the exact prior cursor and BOOK ladder checkpoint.
    An empty page cannot certify absence of real market activity. A restarted
    or changed source transport epoch requires fresh original BOOK snapshot.
    """
    _need(
        isinstance(page, R11OriginalFramePage)
        and isinstance(page.source_session_id, str)
        and _SHA32.fullmatch(page.source_session_id) is not None
        and isinstance(page.transport_epoch_id, str)
        and _SHA32.fullmatch(page.transport_epoch_id) is not None
        and isinstance(page.producer_release_commit, str)
        and _SHA40.fullmatch(page.producer_release_commit) is not None
        and page.product_category in ("SPOT", "LINEAR")
        and page.source_state in ("ACTIVE", "GAP")
        and type(page.requested_after) is int and page.requested_after >= 0
        and type(page.next_cursor) is int
        and page.next_cursor >= page.requested_after
        and type(page.more_pages) is bool
        and isinstance(page.frames, tuple)
        and len(page.frames) <= 128
        and page.market_publication_cursor_verified is False
        and page.provider_continuity_verified is False
        and page.seven_symbol_parity_verified is False
        and page.mayak_cutover_ready is False,
        "PAGE_IDENTITY_OR_FALSE_READINESS",
    )
    if previous is None:
        _need(
            page.requested_after == 0,
            "UNTRUSTED_INITIAL_CURSOR",
        )
        books: dict[str, R11MayakBookState] = {}
        expected_digest = None
    else:
        _need(
            isinstance(previous, R11OptionalMayakCursor)
            and page.source_session_id == previous.source_session_id
            and page.transport_epoch_id == previous.transport_epoch_id
            and page.product_category == previous.product_category
            and page.producer_release_commit == previous.producer_release_commit
            and page.requested_after == previous.next_cursor
            and (
                (previous.next_cursor == 0 and previous.next_digest is None)
                or (
                    previous.next_cursor > 0
                    and isinstance(previous.next_digest, str)
                    and _SHA64.fullmatch(previous.next_digest) is not None
                )
            ),
            "CURSOR_OR_TRANSPORT_EPOCH_CHANGED",
        )
        expected_digest = previous.next_digest
        books = {}
        for item in previous.books:
            _need(
                isinstance(item, R11MayakBookState)
                and item.symbol in _FIVE and item.symbol not in books,
                "CHECKPOINT_BOOK_SCOPE_INVALID",
            )
            books[item.symbol] = item
    _need(
        (page.requested_after == 0 and (
            previous is None or previous.next_cursor == 0
        )) or (
            page.requested_after > 0 and previous is not None
        ),
        "RESUME_CURSOR_MISMATCH",
    )
    projected: list[R11OptionalMayakEvent] = []
    cursor = page.requested_after
    for frame in page.frames:
        _need(
            type(frame.source_frame_number) is int
            and cursor < frame.source_frame_number <= page.next_cursor,
            "FRAME_ORDER_OR_PAGE_TAIL_INVALID",
        )
        facts = _canonical_frame(frame, source=page)
        for index, fact in enumerate(facts):
            _need(len(projected) < _EVENT_LIMIT, "EVENT_PAGE_BUDGET_EXCEEDED")
            when = _utc(fact.source_time).timestamp()
            if frame.fact_family == "SPOT_TRADE":
                _need(
                    frame.product_category == "SPOT"
                    and fact.kind == "TRADE"
                    and fact.exchange_sequence is not None
                    and fact.exec_id is not None,
                    "TRADE_ORIGINAL_ID_MISSING",
                )
                price = _numeric(fact.payload.get("price"))
                size = _numeric(fact.payload.get("qty"))
                _need(math.isfinite(price * size), "TRADE_NOTIONAL_OVERFLOW")
                side = fact.payload.get("side")
                _need(side in ("Buy", "Sell"), "TRADE_SIDE_INVALID")
                projected.append(R11OptionalMayakEvent(
                    event="TRADE", product=frame.product_category, symbol=fact.symbol,
                    timestamp=when, source_frame_number=frame.source_frame_number,
                    source_fact_index=index,
                    source_sequence=fact.exchange_sequence, exec_id=fact.exec_id,
                    side=cast(str, side), price=price, quantity=size,
                ))
            elif frame.fact_family == "ORDERBOOK_50":
                _need(fact.kind == "BOOK", "BOOK_CODEC_KIND_MISMATCH")
                book = _book_next(books.get(fact.symbol), fact=fact)
                books[fact.symbol] = book
                projected.append(R11OptionalMayakEvent(
                    event="BOOK", product=frame.product_category, symbol=fact.symbol,
                    timestamp=when, source_frame_number=frame.source_frame_number,
                    source_fact_index=index,
                    source_sequence=fact.exchange_sequence, exec_id=fact.exec_id,
                    bids=book.bids[:50], asks=book.asks[:50],
                ))
            elif frame.fact_family == "TICKER":
                _need(
                    frame.product_category == "LINEAR"
                    and fact.kind == "TICKER",
                    "TICKER_PRODUCT_OR_KIND_INVALID",
                )
                updates = tuple(
                    (legacy, _numeric(
                        fact.payload[source_key], signed=source_key == "fundingRate",
                        zero=source_key == "fundingRate",
                    ))
                    for source_key, legacy in _TICKER_NAMES
                    if source_key in fact.payload
                )
                projected.append(R11OptionalMayakEvent(
                    event="TICKER", product=frame.product_category, symbol=fact.symbol,
                    timestamp=when, source_frame_number=frame.source_frame_number,
                    source_fact_index=index,
                    source_sequence=fact.exchange_sequence, exec_id=fact.exec_id,
                    ticker_updates=updates,
                ))
            elif frame.fact_family == "LIQUIDATION":
                _need(
                    frame.product_category == "LINEAR"
                    and fact.kind == "LIQUIDATION",
                    "LIQUIDATION_PRODUCT_OR_KIND_INVALID",
                )
                price = _numeric(fact.payload.get("price"))
                size = _numeric(fact.payload.get("qty"))
                _need(math.isfinite(price * size), "LIQ_NOTIONAL_OVERFLOW")
                side = fact.payload.get("side")
                _need(side in ("Buy", "Sell"), "LIQ_SIDE_INVALID")
                projected.append(R11OptionalMayakEvent(
                    event="LIQUIDATION", product=frame.product_category, symbol=fact.symbol,
                    timestamp=when, source_frame_number=frame.source_frame_number,
                    source_fact_index=index,
                    source_sequence=fact.exchange_sequence, exec_id=fact.exec_id,
                    side=cast(str, side),
                    price=price, quantity=size,
                ))
            else:
                raise R11OptionalMayakReplayUnverified(
                    "R11_OPTIONAL_MAYAK_UNAUTHORIZED_FACT_FAMILY"
                )
        cursor = frame.source_frame_number
        expected_digest = frame.frame_content_sha256
    _need(
        (cursor == page.next_cursor)
        and (expected_digest == page.next_digest)
        and (not page.more_pages or bool(page.frames)),
        "PAGE_CURSOR_OR_DIGEST_MISMATCH",
    )
    checkpoint = R11OptionalMayakCursor(
        source_session_id=page.source_session_id,
        transport_epoch_id=page.transport_epoch_id,
        product_category=page.product_category,
        producer_release_commit=page.producer_release_commit,
        next_cursor=cursor,
        next_digest=expected_digest,
        books=tuple(books[symbol] for symbol in sorted(books)),
    )
    return R11OptionalMayakBatch(
        cursor=checkpoint, events=tuple(projected),
        has_more_pages=page.more_pages,
    )
