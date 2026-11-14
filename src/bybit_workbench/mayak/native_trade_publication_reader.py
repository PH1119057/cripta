"""R11: Original LINEAR publicTrade page, source-pinned to one PG publication.

Five native symbols only. No Bybit public REST/WS, no independent market
writer, and no live MAYAK mutation. Page/cursor identity is independently
verified; CATCH_UP is NOT source continuity, SPOT/BTC/ETH parity or permission
to retire MAYAK's seven-coin collector. A silent market can have zero rows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

_OWNED_FIVE = frozenset((
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
))
_SESSION = re.compile(r"^[0-9a-f]{32}$")
_RELEASE = re.compile(r"^[0-9a-f]{40}$")
_MAX_PAGE = 512
_MAX_PUBLICATION_AGE = timedelta(seconds=30)


class R11OriginalNativeUnverified(RuntimeError):
    """No independently provable original-native publication page."""


@dataclass(frozen=True, slots=True)
class R11OriginalTrade:
    symbol: str
    source_session_id: str
    frame_number: int
    frame_index: int
    seq: int
    exec_id: str
    traded_at: datetime
    accepted_at: datetime
    price: Decimal
    quantity: Decimal
    side: str

    @property
    def source_ref(self) -> str:
        return f"bybit:publicTrade:{self.symbol}:{self.exec_id}"


@dataclass(frozen=True, slots=True)
class R11OriginalNativePage:
    symbol: str
    publication_id: int
    source_session_id: str
    producer_release_commit: str
    publication_as_of: datetime
    publication_cursor: tuple[int, int]
    read_after: tuple[int, int]
    next_cursor: tuple[int, int]
    original_events: tuple[R11OriginalTrade, ...]
    more_pages: bool
    last_event_at_publication_cursor: bool
    # Never infer a complete, live seven-coin source from a bounded PG page.
    source_continuity_verified: bool = False
    mayak_event_parity_verified: bool = False
    mayak_cutover_ready: bool = False


def _need(value: bool, code: str) -> None:
    if not value:
        raise R11OriginalNativeUnverified("R11_ORIGINAL_" + code)


def _utc(value: object, code: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise R11OriginalNativeUnverified("R11_ORIGINAL_" + code) from exc
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise R11OriginalNativeUnverified("R11_ORIGINAL_" + code)
    return value.astimezone(UTC)


def _positive(value: object) -> Decimal:
    try:
        if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
            raise ValueError("not numeric")
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0:
            raise ValueError("invalid original number")
        return amount
    except (InvalidOperation, ValueError) as exc:
        raise R11OriginalNativeUnverified(
            "R11_ORIGINAL_PRICE_OR_QUANTITY_INVALID"
        ) from exc


def read_r11_original_linear_page(
    connection: Any, *,
    symbol: str,
    observed_at: datetime,
    read_after: tuple[int, int] = (0, 0),
    max_events: int = _MAX_PAGE,
) -> R11OriginalNativePage:
    """Bounded original PG event page. Caller owns REPEATABLE READ READ ONLY.

    An explicit nonzero pagination cursor MUST physically exist for this exact
    symbol/source epoch. No caller-supplied "coverage complete" flag.
    """
    now = _utc(observed_at, "NOW_NOT_UTC")
    _need(symbol in _OWNED_FIVE, "SYMBOL_OUTSIDE_FIVE_NATIVE_CAPTURE")
    _need(
        type(max_events) is int and 1 <= max_events <= _MAX_PAGE
        and type(read_after) is tuple and len(read_after) == 2
        and all(type(x) is int and x >= 0 for x in read_after),
        "CURSOR_OR_PAGE_BOUND_INVALID",
    )
    _need(
        connection.execute("SHOW transaction_isolation").fetchone()
        == ("repeatable read",)
        and connection.execute("SHOW transaction_read_only").fetchone()
        == ("on",),
        "ATOMIC_READ_ONLY_SNAPSHOT_REQUIRED",
    )
    sessions = connection.execute(
        """SELECT source_session_id,producer_release_commit
             FROM analytics.native_public_trade_source_sessions
            WHERE market_source_id=%s AND state='ACTIVE'
            ORDER BY subscribed_at DESC LIMIT 2""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID,),
    ).fetchall()
    _need(
        len(sessions) == 1
        and isinstance(sessions[0][0], str)
        and _SESSION.fullmatch(sessions[0][0]) is not None
        and isinstance(sessions[0][1], str)
        and _RELEASE.fullmatch(sessions[0][1]) is not None,
        "ONE_ACTIVE_SOURCE_EPOCH_REQUIRED",
    )
    session, commit = sessions[0]
    publication = connection.execute(
        """SELECT publication_id,source_session_id,producer_release_commit,
                  as_of_second,publication_status,native_watermarks
             FROM analytics.market_publications
            WHERE market_source_id=%s AND source_session_id=%s
            ORDER BY as_of_second DESC,publication_id DESC LIMIT 1""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID, session),
    ).fetchone()
    _need(
        isinstance(publication, (tuple, list)) and len(publication) == 6,
        "PUBLICATION_MISSING",
    )
    pid, pub_session, pub_commit, as_of, state, refs = publication
    published = _utc(as_of, "PUBLICATION_TIME_INVALID")
    _need(
        type(pid) is int and pid >= 1
        and pub_session == session and pub_commit == commit
        and state == "WATERMARKS_ONLY"
        and not published.microsecond
        and timedelta(0) <= now - published <= _MAX_PUBLICATION_AGE
        and isinstance(refs, dict),
        "PUBLICATION_IDENTITY_OR_AGE_INVALID",
    )
    mark = refs.get(symbol)
    if not isinstance(mark, dict):
        raise R11OriginalNativeUnverified(
            "R11_ORIGINAL_PUBLICATION_NATIVE_CURSOR_MISSING"
        )
    inst = bybit_instrument_id("linear", symbol)
    frame, index, seq, exec_id = (
        mark.get("frame_number"), mark.get("frame_index"),
        mark.get("exchange_sequence"), mark.get("exec_id"),
    )
    _need(
        mark.get("instrument_id") == inst
        and type(frame) is int and frame >= 1
        and type(index) is int and index >= 0
        and type(seq) is int and seq >= 0
        and isinstance(exec_id, str) and bool(exec_id)
        and _utc(mark.get("event_at"), "PUBLICATION_NATIVE_TIME_INVALID")
        <= published
        and _utc(mark.get("accepted_at"), "PUBLICATION_ACCEPTED_TIME_INVALID")
        <= published + timedelta(seconds=5),
        "PUBLICATION_NATIVE_IDENTITY_INVALID",
    )
    watermark: tuple[int, int] = (int(frame), int(index))
    _need(read_after <= watermark, "CURSOR_BESIDE_PUBLICATION")
    if read_after != (0, 0):
        anchor = connection.execute(
            """SELECT exchange_sequence,exec_id
                 FROM analytics.native_public_trade_source_events
                WHERE source_session_id=%s AND market_source_id=%s
                  AND instrument_id=%s AND external_symbol=%s
                  AND frame_number=%s AND frame_index=%s""",
            (
                session, BYBIT_PUBLIC_MARKET_SOURCE_ID, inst, symbol,
                read_after[0], read_after[1],
            ),
        ).fetchone()
        _need(
            isinstance(anchor, (tuple, list))
            and len(anchor) == 2 and type(anchor[0]) is int
            and isinstance(anchor[1], str) and bool(anchor[1]),
            "CURSOR_NOT_IN_ORIGINAL_EPOCH",
        )
    rows = connection.execute(
        """SELECT e.frame_number,e.frame_index,e.market_source_id,
                  e.instrument_id,e.external_symbol,e.exchange_sequence,
                  e.exec_id,e.event_at,e.accepted_at,
                  n.event_at AS journal_at,n.price,n.quantity,n.taker_side,
                  n.source_transport,n.producer_release_commit
             FROM analytics.native_public_trade_source_events e
             JOIN analytics.native_public_trade_events n
               ON n.market_source_id=e.market_source_id
              AND n.instrument_id=e.instrument_id
              AND n.exchange_sequence=e.exchange_sequence
              AND n.exec_id=e.exec_id
            WHERE e.source_session_id=%s AND e.market_source_id=%s
              AND e.instrument_id=%s AND e.external_symbol=%s
              AND (e.frame_number,e.frame_index)>(%s,%s)
              AND (e.frame_number,e.frame_index)<=(%s,%s)
            ORDER BY e.frame_number,e.frame_index LIMIT %s""",
        (
            session, BYBIT_PUBLIC_MARKET_SOURCE_ID, inst, symbol,
            read_after[0], read_after[1], frame, index, max_events + 1,
        ),
    ).fetchall()
    _need(len(rows) <= max_events + 1, "QUERY_PAGE_BUDGET_EXCEEDED")
    complete_page = len(rows) <= max_events
    collected: list[R11OriginalTrade] = []
    prev_pos = read_after
    prev_at: datetime | None = None
    prev_seq = -1
    seen: set[tuple[int, str]] = set()
    unique_exec: set[str] = set()
    for row in rows[:max_events]:
        _need(
            isinstance(row, (tuple, list)) and len(row) == 15,
            "ORIGINAL_ROW_SHAPE_INVALID",
        )
        (
            fnum, idx, source, instrument, external, event_seq, ident,
            traded_at, accepted_at, journal_at, price, qty, side,
            transport, released,
        ) = row
        position = (fnum, idx)
        traded = _utc(traded_at, "ORIGINAL_EVENT_TIME_INVALID")
        accepted = _utc(accepted_at, "ORIGINAL_RECEIVED_TIME_INVALID")
        _need(
            type(fnum) is int and type(idx) is int
            and fnum >= 1 and idx >= 0 and prev_pos < position <= watermark
            and source == BYBIT_PUBLIC_MARKET_SOURCE_ID
            and instrument == inst and external == symbol
            and type(event_seq) is int and event_seq >= prev_seq
            and isinstance(ident, str) and bool(ident)
            and (event_seq, ident) not in seen and ident not in unique_exec
            and (prev_at is None or prev_at <= traded)
            and traded <= published
            and traded == _utc(journal_at, "ORIGINAL_JOURNAL_TIME_INVALID")
            and traded - timedelta(seconds=2) <= accepted
            and accepted <= published + timedelta(seconds=5)
            and side in ("Buy", "Sell")
            and transport == "BYBIT_WS_PUBLIC_TRADE" and released == commit,
            "ORIGINAL_SEQUENCE_OR_PROVENANCE_DRIFT",
        )
        collected.append(R11OriginalTrade(
            symbol=symbol, source_session_id=session,
            frame_number=fnum, frame_index=idx,
            seq=event_seq, exec_id=ident, traded_at=traded,
            accepted_at=accepted, price=_positive(price),
            quantity=_positive(qty), side=side,
        ))
        prev_pos = position
        prev_at = traded
        prev_seq = event_seq
        seen.add((event_seq, ident))
        unique_exec.add(ident)
    tail_matches = bool(collected) and (
        (collected[-1].frame_number, collected[-1].frame_index) == watermark
        and collected[-1].seq == seq and collected[-1].exec_id == exec_id
    )
    if complete_page and collected and prev_pos == watermark:
        _need(tail_matches, "PUBLICATION_LATEST_NATIVE_DRIFT")
    return R11OriginalNativePage(
        symbol=symbol, publication_id=pid, source_session_id=session,
        producer_release_commit=commit, publication_as_of=published,
        publication_cursor=watermark, read_after=read_after,
        next_cursor=prev_pos, original_events=tuple(collected),
        more_pages=not complete_page,
        last_event_at_publication_cursor=tail_matches,
    )
