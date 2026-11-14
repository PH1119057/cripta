"""R11: pure offline original optional frames -> existing MAYAK event semantics.

The input must come from the checked immutable PG original-frame page (R11
stage7) with all original Bybit events re-decoded. The only target is a
disposable LiveMayakEngine object owned by the caller; NO production MAYAK
consumer, source, thread, socket, DB or Exchange code calls this module.
This proves event-to-feature formula parity under supplied original frames,
NOT full provider continuity, MarketPublication coverage or seven-symbol
MAYAK cutover. BTC/ETH remain legacy-only reference anchors.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, cast

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

_FIVE = frozenset((
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
))
_TICKER_MAP = {
    "openInterest": "open_interest",
    "openInterestValue": "open_interest_value",
    "fundingRate": "funding_rate",
    "lastPrice": "last_price",
    "markPrice": "mark_price",
    "indexPrice": "index_price",
}
_MAX_IDS = 8192
_MAX_FRAME_EVENTS = 4096


class R11MayakOfflineParityUnverified(RuntimeError):
    """No exact original event identity/ordered book snapshot replay proof."""


@dataclass(frozen=True, slots=True)
class R11MayakOfflineReplayResult:
    source_session_id: str
    transport_epoch_id: str
    product_category: str
    next_cursor: int
    next_digest: str | None
    applied_trade_count: int
    applied_book_count: int
    applied_ticker_count: int
    applied_liquidation_count: int
    # Neither input original page nor this offline engine measures all
    # provider event receipt/7-symbol upstream consumer correctness.
    publication_native_event_watermark_verified: bool = False
    provider_transport_continuity_verified: bool = False
    seven_symbol_feature_parity_verified: bool = False
    mayak_live_consumer_cutover_authorized: bool = False


def _need(yes: bool, code: str) -> None:
    if not yes:
        raise R11MayakOfflineParityUnverified("R11_OFFLINE_MAYAK_" + code)


def _number(value: object, *, allow_zero: bool = False,
            allow_negative: bool = False) -> float:
    try:
        if not isinstance(value, (str, int, float, Decimal)) or (
            isinstance(value, bool)
        ):
            raise ValueError("wrong number type")
        raw = Decimal(str(value))
        if not raw.is_finite() or (
            raw < 0 and not allow_negative
        ) or (
            raw == 0 and not allow_zero and not allow_negative
        ):
            raise ValueError("invalid original")
        converted = float(raw)
        if not math.isfinite(converted):
            raise ValueError("float overflow")
        if raw != 0 and converted == 0:
            raise ValueError("float underflow")
        return converted
    except (ValueError, OverflowError, InvalidOperation) as exc:
        raise R11MayakOfflineParityUnverified(
            "R11_OFFLINE_MAYAK_NUMBER_UNREPRESENTABLE"
        ) from exc


def _levels(
    rows: object,
    *,
    deletion_allowed: bool,
) -> tuple[tuple[float, float], ...]:
    _need(
        isinstance(rows, tuple) and len(rows) <= 100,
        "BOOK_LEVEL_SHAPE_INVALID",
    )
    output: list[tuple[float, float]] = []
    used: set[float] = set()
    for row in rows:
        _need(
            isinstance(row, tuple) and len(row) == 2,
            "BOOK_LEVEL_SHAPE_INVALID",
        )
        p = _number(row[0])
        q = _number(row[1], allow_zero=deletion_allowed)
        _need(p not in used, "BOOK_DUPLICATE_FLOAT_PRICE")
        output.append((p, q))
        used.add(p)
    return tuple(output)


def _original_facts(page: R11OriginalFramePage) -> Iterable[
    tuple[R11OriginalPublicFrame, tuple[PublicSourceFact, ...]]
]:
    """Re-decode all staged originals before ANY disposable engine mutation."""
    for raw in page.frames:
        _need(
            raw.product_category == page.product_category
            and raw.source_session_id == page.source_session_id
            and raw.transport_epoch_id == page.transport_epoch_id
            and raw.producer_release_commit == page.producer_release_commit
            and raw.external_symbol in _FIVE,
            "FRAME_SCOPE_OR_SOURCE_EPOCH_MISMATCH",
        )
        try:
            decoded = decode_bybit_public_frame(
                raw.provider_frame,
                product=cast(Any, page.product_category),
                allowed_symbols=frozenset((raw.external_symbol,)),
                received_at=raw.received_at,
            )
            proved = make_r11_original_optional_frame(
                source_session_id=raw.source_session_id,
                transport_epoch_id=raw.transport_epoch_id,
                producer_release_commit=raw.producer_release_commit,
                product=raw.product_category,
                source_frame_number=raw.source_frame_number,
                message=raw.provider_frame,
                facts=decoded,
                received_at=raw.received_at,
            )
        except (
            PublicSourceFrameInvalid, R11OriginalFrameUnavailable,
            TypeError, ValueError,
        ) as exc:
            raise R11MayakOfflineParityUnverified(
                "R11_OFFLINE_MAYAK_FRAME_REDECODE_FAILED"
            ) from exc
        _need(
            proved == raw
            and len(decoded) <= _MAX_FRAME_EVENTS,
            "FRAME_OR_HASH_UNVERIFIED",
        )
        yield raw, decoded


class R11DisposableMayakOriginalReplayer:
    """Single product/transport epoch disposable MAYAK input projector.

    Calls on_trade,on_book,on_ticker,on_liquidation only after the entire
    page and its orderbook state have been checked, avoiding partial writes
    to the disposable engine if a later frame is malformed.
    """

    def __init__(self, engine: LiveMayakEngine, *, product: str) -> None:
        _need(isinstance(engine, LiveMayakEngine), "ENGINE_TYPE_INVALID")
        _need(product in ("SPOT", "LINEAR"), "PRODUCT_INVALID")
        _need(set(engine.symbols).issubset(_FIVE), "ENGINE_UNIVERSE_NOT_FIVE")
        self.engine = engine
        self.product = product
        self._session: str | None = None
        self._epoch: str | None = None
        self._producer: str | None = None
        self._cursor = 0
        self._digest: str | None = None
        self._books: dict[
            str, dict[str, dict[float, float]]
        ] = {}
        self._book_seq: dict[str, tuple[int, int]] = {}
        self._seen_ids: set[tuple[str, int, str]] = set()

    def accept(self, page: R11OriginalFramePage) -> R11MayakOfflineReplayResult:
        _need(
            isinstance(page, R11OriginalFramePage)
            and page.product_category == self.product
            and page.requested_after == self._cursor
            and (self._session is None
                 or page.source_session_id == self._session)
            and (self._epoch is None
                 or page.transport_epoch_id == self._epoch)
            and (self._producer is None
                 or page.producer_release_commit == self._producer)
            and (self._digest is None
                 or page.frames == ()
                 or page.requested_after > 0)
            and page.next_cursor >= page.requested_after
            and (
                not page.frames or (
                    page.frames[-1].source_frame_number == page.next_cursor
                    and page.frames[0].source_frame_number
                    > page.requested_after
                )
            )
            and not page.market_publication_cursor_verified
            and not page.provider_continuity_verified
            and not page.seven_symbol_parity_verified
            and not page.mayak_cutover_ready,
            "PAGE_CURSOR_OR_EPOCH_CHAIN_INVALID",
        )
        # The reader externally anchors each nonzero cursor via SHA; a
        # synthetic replacement page cannot claim the full provider tape.
        _need(
            self._digest is None
            or (
                page.requested_after == self._cursor
                and page.next_digest is not None
            ),
            "PAGE_SHA_RESUME_REQUIRED",
        )
        pending: list[tuple[str, tuple[Any, ...]]] = []
        books = {
            symbol: {
                side: dict(book[side]) for side in ("b", "a")
            }
            for symbol, book in self._books.items()
        }
        sequences = dict(self._book_seq)
        seen = set(self._seen_ids)
        previous_id = self._cursor
        for original, facts in _original_facts(page):
            _need(
                type(original.source_frame_number) is int
                and original.source_frame_number > previous_id
                and original.external_symbol in self.engine.symbols,
                "FRAME_ORDER_OR_ENGINE_INSTRUMENT_INVALID",
            )
            previous_id = original.source_frame_number
            for fact in facts:
                at = fact.source_time.timestamp()
                symbol = fact.symbol
                market = self.product.lower()
                if fact.kind == "TRADE":
                    _need(
                        self.product == "SPOT"
                        and type(fact.exchange_sequence) is int
                        and fact.exchange_sequence >= 0
                        and isinstance(fact.exec_id, str)
                        and bool(fact.exec_id),
                        "ORIGINAL_TRADE_SCOPE_INVALID",
                    )
                    trade_id = (
                        symbol, fact.exchange_sequence, fact.exec_id,
                    )
                    _need(trade_id not in seen, "ORIGINAL_TRADE_DUPLICATE")
                    seen.add(trade_id)
                    _need(
                        len(seen) <= _MAX_IDS,
                        "ORIGINAL_TRADE_REPLAY_ID_BUDGET",
                    )
                    price = _number(fact.payload["price"])
                    size = _number(fact.payload["qty"])
                    _number(price * size)
                    pending.append((
                        "trade", (
                            market, symbol, at, fact.payload["side"],
                            price, size,
                        ),
                    ))
                elif fact.kind == "LIQUIDATION":
                    _need(
                        self.product == "LINEAR",
                        "LIQUIDATION_PRODUCT_INVALID",
                    )
                    price = _number(fact.payload["price"])
                    size = _number(fact.payload["qty"])
                    _number(price * size)
                    pending.append((
                        "liquidation", (
                            symbol, at, fact.payload["side"],
                            price, size,
                        ),
                    ))
                elif fact.kind == "TICKER":
                    _need(
                        self.product == "LINEAR",
                        "TICKER_PRODUCT_INVALID",
                    )
                    values = {
                        target: _number(
                            fact.payload[source],
                            allow_negative=source == "fundingRate",
                        )
                        for source, target in _TICKER_MAP.items()
                        if source in fact.payload
                    }
                    _need(bool(values), "TICKER_EMPTY_OR_UNSUPPORTED")
                    pending.append(("ticker", (symbol, at, values)))
                elif fact.kind == "BOOK":
                    seq = fact.exchange_sequence
                    update = fact.payload.get("update_id")
                    _need(
                        type(seq) is int and seq >= 0
                        and type(update) is int and update >= 0,
                        "BOOK_SEQUENCE_OR_UPDATE_INVALID",
                    )
                    snap = fact.frame_type == "snapshot"
                    previous_seq = sequences.get(symbol)
                    if not snap:
                        _need(
                            symbol in books and previous_seq is not None
                            and update != 1
                            and seq > previous_seq[0]
                            and update > previous_seq[1],
                            "BOOK_DELTA_UNVERIFIED_OR_RESTART",
                        )
                    if snap:
                        books[symbol] = {"b": {}, "a": {}}
                    state = books[symbol]
                    for name, fact_key in (("b", "bids"), ("a", "asks")):
                        for price, qty in _levels(
                            fact.payload[fact_key],
                            deletion_allowed=not snap,
                        ):
                            if qty == 0:
                                state[name].pop(price, None)
                            else:
                                state[name][price] = qty
                    _need(
                        state["b"] and state["a"]
                        and max(state["b"]) < min(state["a"]),
                        "BOOK_EMPTY_OR_CROSSED",
                    )
                    sequences[symbol] = (seq, update)
                    bids = sorted(state["b"].items(), reverse=True)[:50]
                    asks = sorted(state["a"].items())[:50]
                    pending.append((
                        "book", (market, symbol, at, bids, asks),
                    ))
                else:
                    raise R11MayakOfflineParityUnverified(
                        "R11_OFFLINE_MAYAK_UNSUPPORTED_ORIGINAL_EVENT"
                    )
        _need(
            previous_id == page.next_cursor
            and (not page.frames or
                 page.next_digest == page.frames[-1].frame_content_sha256),
            "PAGE_TAIL_OR_DIGEST_MISMATCH",
        )
        for kind, args in pending:
            if kind == "trade":
                self.engine.on_trade(*args)
            elif kind == "liquidation":
                self.engine.on_liquidation(*args)
            elif kind == "ticker":
                symbol, at, values = args
                self.engine.on_ticker(symbol, at, **values)
            else:
                self.engine.on_book(*args)
        self._books = books
        self._book_seq = sequences
        self._seen_ids = seen
        self._session = page.source_session_id
        self._epoch = page.transport_epoch_id
        self._producer = page.producer_release_commit
        self._cursor = page.next_cursor
        self._digest = page.next_digest
        def count(key: str) -> int:
            return sum(k == key for k, _ in pending)
        return R11MayakOfflineReplayResult(
            source_session_id=page.source_session_id,
            transport_epoch_id=page.transport_epoch_id,
            product_category=page.product_category,
            next_cursor=self._cursor, next_digest=self._digest,
            applied_trade_count=count("trade"),
            applied_book_count=count("book"),
            applied_ticker_count=count("ticker"),
            applied_liquidation_count=count("liquidation"),
        )
