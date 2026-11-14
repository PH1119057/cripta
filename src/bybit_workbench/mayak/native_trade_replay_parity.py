"""R11 source-only parity between verified native LINEAR trade pages and MAYAK.

This is a *pure* projection for disposable shadow/replay tests, not a runtime
source activation. It preserves each original same-seq execId, enforces one
ordered publication/epoch/cursor chain and never fabricates trade aggregates.
The five-symbol native producer still lacks MAYAK's SPOT and BTC/ETH scope;
other features (book deltas, liquidation, ratio) remain outside this contract.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.mayak.native_trade_publication_reader import (
    R11OriginalNativePage,
    R11OriginalTrade,
)

_LAST_IDS_LIMIT = 1024
_MAX_ORIGINAL_EVENTS = 512


class R11TradeReplayUnverified(RuntimeError):
    """Source epoch, original identity, ordering or numeric parity unproven."""


@dataclass(frozen=True, slots=True)
class R11MayakTradeInput:
    """Exact legacy LiveMayakEngine.on_trade positional arguments with ID."""

    symbol: str
    timestamp: float
    side: str
    price: float
    quantity: float
    frame_number: int
    frame_index: int
    seq: int
    exec_id: str
    market: str = "linear"

    def on_trade_args(self) -> tuple[str, str, float, str, float, float]:
        return (
            self.market, self.symbol, self.timestamp, self.side,
            self.price, self.quantity,
        )


@dataclass(frozen=True, slots=True)
class R11ReplayCursor:
    symbol: str
    source_session_id: str
    producer_release_commit: str
    publication_id: int
    publication_as_of: datetime
    publication_watermark: tuple[int, int]
    cursor: tuple[int, int]
    last_seq: int
    last_event_at: datetime | None
    recent_exec_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class R11TradeParityBatch:
    publication_id: int
    cursor: R11ReplayCursor
    events: tuple[R11MayakTradeInput, ...]
    has_more_pages: bool
    # A bounded PG page can never prove consumer/source equivalence by itself.
    source_continuity_verified: bool = False
    seven_symbol_parity_verified: bool = False
    safe_to_switch_mayak_public_socket: bool = False


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise R11TradeReplayUnverified("R11_MAYAK_REPLAY_" + reason)


def _finite_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(
        value, (str, int, float, Decimal),
    ):
        raise R11TradeReplayUnverified("R11_MAYAK_REPLAY_FLOAT_INVALID")
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise R11TradeReplayUnverified("R11_MAYAK_REPLAY_FLOAT_INVALID") from exc
    _require(math.isfinite(numeric) and numeric > 0, "FLOAT_INVALID")
    return numeric


def _utc(value: datetime) -> datetime:
    _require(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0),
        "EVENT_CLOCK_NOT_UTC",
    )
    return value.astimezone(UTC)


def project_r11_native_linear_trade_page(
    page: R11OriginalNativePage,
    *,
    previous: R11ReplayCursor | None = None,
) -> R11TradeParityBatch:
    """Project verified page into original MAYAK trade inputs *in memory*.

    On a resume the caller MUST supply an independently checkpointed prior
    cursor. Without one only (0,0) is a valid start. A missing next page,
    changed epoch, reordered native frame, duplicate execution or publication
    watermark regression blocks the batch. No readiness claim is derived.
    """
    _require(
        isinstance(page, R11OriginalNativePage)
        and page.symbol in (
            "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
        )
        and type(page.publication_id) is int and page.publication_id > 0
        and all(
            type(mark) is tuple and len(mark) == 2
            and all(type(i) is int and i >= 0 for i in mark)
            for mark in (
                page.read_after, page.publication_cursor, page.next_cursor,
            )
        )
        and page.read_after <= page.next_cursor <= page.publication_cursor
        and type(page.more_pages) is bool
        and type(page.last_event_at_publication_cursor) is bool
        and isinstance(page.original_events, tuple)
        and len(page.original_events) <= _MAX_ORIGINAL_EVENTS
        and all(
            isinstance(item, R11OriginalTrade)
            for item in page.original_events
        )
        and isinstance(page.source_session_id, str)
        and re.fullmatch(r"[0-9a-f]{32}", page.source_session_id) is not None
        and isinstance(page.producer_release_commit, str)
        and re.fullmatch(
            r"[0-9a-f]{40}", page.producer_release_commit,
        ) is not None,
        "PAGE_SHAPE_OR_SCOPE_INVALID",
    )
    published = _utc(page.publication_as_of)
    if previous is None:
        _require(page.read_after == (0, 0), "UNVERIFIED_INITIAL_CURSOR")
        last_seq = -1
        last_time: datetime | None = None
        seen: list[str] = []
    else:
        _require(
            page.symbol == previous.symbol
            and page.source_session_id == previous.source_session_id
            and page.producer_release_commit == previous.producer_release_commit
            and page.read_after == previous.cursor
            and page.publication_id >= previous.publication_id
            and published >= previous.publication_as_of
            and page.publication_cursor >= previous.publication_watermark,
            "EPOCH_OR_CURSOR_CHAIN_BREAK",
        )
        last_seq = previous.last_seq
        last_time = previous.last_event_at
        seen = list(previous.recent_exec_ids)
    _require(
        not page.more_pages or (
            bool(page.original_events)
            and page.next_cursor < page.publication_cursor
        ),
        "FALSE_MORE_PAGES",
    )
    if page.original_events:
        final = page.original_events[-1]
        tail_matches = (
            (final.frame_number, final.frame_index) == page.publication_cursor
        )
        _require(
            page.next_cursor == (final.frame_number, final.frame_index)
            and (not page.last_event_at_publication_cursor or tail_matches)
            and (not tail_matches or page.last_event_at_publication_cursor)
            and (not page.more_pages or not tail_matches),
            "PAGE_TAIL_NOT_VERIFIED",
        )
    else:
        _require(
            page.next_cursor == page.read_after
            and page.next_cursor == page.publication_cursor
            and not page.more_pages
            and not page.last_event_at_publication_cursor,
            "MISSING_OR_FABRICATED_SILENT_EVENTS",
        )
    projected: list[R11MayakTradeInput] = []
    cursor = page.read_after
    for event in page.original_events:
        _require(isinstance(event, R11OriginalTrade), "TRADE_TYPE_INVALID")
        trade_at = _utc(event.traded_at)
        accepted_at = _utc(event.accepted_at)
        place = (event.frame_number, event.frame_index)
        _require(
            event.symbol == page.symbol
            and event.source_session_id == page.source_session_id
            and type(event.frame_number) is int and event.frame_number > 0
            and type(event.frame_index) is int and event.frame_index >= 0
            and cursor < place <= page.publication_cursor
            and type(event.seq) is int and event.seq >= last_seq
            and isinstance(event.exec_id, str) and bool(event.exec_id)
            and event.exec_id not in seen
            and event.side in ("Buy", "Sell")
            and trade_at <= published
            and (last_time is None or trade_at >= last_time)
            and trade_at - timedelta(seconds=2) <= accepted_at
            and accepted_at <= published + timedelta(seconds=5),
            "ORIGINAL_TRADE_IDENTITY_OR_ORDER_INVALID",
        )
        price = _finite_float(event.price)
        quantity = _finite_float(event.quantity)
        _finite_float(price * quantity)  # MAYAK multiplies price*size in float.
        projected.append(R11MayakTradeInput(
            symbol=event.symbol, timestamp=trade_at.timestamp(),
            side=event.side, price=price, quantity=quantity,
            frame_number=event.frame_number, frame_index=event.frame_index,
            seq=event.seq, exec_id=event.exec_id,
        ))
        cursor = place
        last_seq = event.seq
        last_time = trade_at
        seen.append(event.exec_id)
    next_state = R11ReplayCursor(
        symbol=page.symbol, source_session_id=page.source_session_id,
        producer_release_commit=page.producer_release_commit,
        publication_id=page.publication_id,
        publication_as_of=published,
        publication_watermark=page.publication_cursor,
        cursor=page.next_cursor,
        last_seq=last_seq,
        last_event_at=last_time,
        recent_exec_ids=tuple(seen[-_LAST_IDS_LIMIT:]),
    )
    return R11TradeParityBatch(
        publication_id=page.publication_id, cursor=next_state,
        events=tuple(projected), has_more_pages=page.more_pages,
    )
