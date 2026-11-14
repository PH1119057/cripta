"""R09 preparatory READ-ONLY original publicTrade postfill proof from MarketPublication.

This is NOT an Entry gate, REST fallback, or an autonomous Exit readiness
claim. In particular, a quiet/no-native-trade interval is BLOCKED: a
publication cursor alone never proves that zero executions occurred.
Integration with signed OPEN Exit and runtime parity is a separate R09 gate.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)
from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import TradeDirection
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)

_HASH = re.compile(r"^[0-9a-f]{40}$")
_SESSION = re.compile(r"^[0-9a-f]{32}$")
_SOURCE = BYBIT_PUBLIC_MARKET_SOURCE_ID
_MAX_EVENTS = 256
_FIRST_FILL_HORIZON = timedelta(seconds=15)
_MAX_PUBLICATION_AGE = timedelta(seconds=5)
_ANCHOR_LOOKBACK = timedelta(seconds=60)


class P90PGPostfillUnverified(RuntimeError):
    """Insufficient original PG/publication evidence for protected first fill."""


@dataclass(frozen=True, slots=True)
class R09NativePublicationCandidate:
    """Read-only original facts, NOT certified MFE coverage or Exit readiness.

    Source-row continuity, no-trade coverage, pending Exchange mutation and
    current signed OPEN/SL/TP need independent proofs before a financial Exit.
    """

    position_id: str
    symbol: str
    source_session_id: str
    publication_id: int
    fill_at: datetime
    covered_through: datetime
    source_commit: str
    producer_release_commit: str
    last_native_price: Decimal
    observed_favorable_price: Decimal
    native_event_count: int
    source_refs: tuple[str, ...]
    native_continuity_verified: bool = False
    exit_ready: bool = False


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise P90PGPostfillUnverified("R09_PG_POSTFILL_" + code)


def _utc(value: object, code: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise P90PGPostfillUnverified("R09_PG_POSTFILL_" + code) from exc
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise P90PGPostfillUnverified("R09_PG_POSTFILL_" + code)
    return value.astimezone(UTC)


def _positive(value: object) -> Decimal:
    try:
        if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
            raise ValueError("not numeric")
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0:
            raise ValueError("not positive")
        return amount
    except (ValueError, InvalidOperation) as exc:
        raise P90PGPostfillUnverified("R09_PG_POSTFILL_PRICE_INVALID") from exc


def _fields(row: object, *names: str) -> tuple[Any, ...]:
    if isinstance(row, Mapping):
        try:
            return tuple(row[name] for name in names)
        except KeyError as exc:
            raise P90PGPostfillUnverified("R09_PG_POSTFILL_ROW_MALFORMED") from exc
    if isinstance(row, (tuple, list)) and len(row) == len(names):
        return tuple(row)
    raise P90PGPostfillUnverified("R09_PG_POSTFILL_ROW_MALFORMED")


def read_r09_native_publication_postfill(
    connection: Any,
    *,
    position: StrategyPosition,
    source_epoch_id: str,
    source_commit: str,
    accepted_producer_commits: frozenset[str],
    observed_at: datetime,
) -> R09NativePublicationCandidate:
    """Read one original-native first-fill suffix in one immutable PG view.

    Caller MUST provide a separate REPEATABLE READ READ ONLY connection.
    It MUST separately attest signed OPEN account/position/fill/SL/TP and
    replay/Exit behavior before considering any Exchange trailing mutation.
    No REST, no source/Strategy checkpoint write and no zero-trade attestation.
    """
    now = _utc(observed_at, "CLOCK_INVALID")
    fill = _utc(position.fill_at, "FILL_CLOCK_INVALID")
    protected = _utc(
        position.initial_protection_confirmed_at, "PROTECTION_CLOCK_INVALID",
    )
    _need(
        position.strategy_id in ALLOWED_SYMBOLS
        and position.strategy_version == VERSION
        and position.symbol in ALLOWED_SYMBOLS[position.strategy_id]
        and position.identity_status == "PROVEN"
        and bool(position.trading_account_id)
        and bool(position.strategy_execution_binding_id)
        and isinstance(source_epoch_id, str)
        and source_epoch_id.startswith("p90-real-epoch-")
        and len(source_epoch_id) == 55
        and isinstance(source_commit, str)
        and _HASH.fullmatch(source_commit) is not None
        and bool(accepted_producer_commits)
        and all(isinstance(item, str) and _HASH.fullmatch(item)
                for item in accepted_producer_commits)
        and fill <= protected <= now
        and now - protected <= _FIRST_FILL_HORIZON
        and _positive(position.actual_avg_fill) > 0,
        "SCOPE_OR_PROTECTED_FIRST_FILL_INVALID",
    )
    _need(
        connection.execute("SHOW transaction_isolation").fetchone()
        == ("repeatable read",)
        and connection.execute("SHOW transaction_read_only").fetchone()
        == ("on",),
        "ATOMIC_READ_ONLY_SNAPSHOT_REQUIRED",
    )
    epoch = connection.execute(
        """SELECT source_commit,status
             FROM strategy_entry.p90l90_real_source_epochs
            WHERE epoch_id=%s""",
        (source_epoch_id,),
    ).fetchone()
    _need(epoch is not None and _fields(epoch, "source_commit", "status")
          == (source_commit, "ACTIVE"), "STRATEGY_SOURCE_EPOCH_INVALID")

    sessions = connection.execute(
        """SELECT source_session_id,market_source_id,producer_release_commit,
                  state,subscribed_at,last_accepted_at,ended_at,reason
             FROM analytics.native_public_trade_source_sessions
            WHERE market_source_id=%s AND state='ACTIVE'
            ORDER BY subscribed_at DESC LIMIT 2""",
        (_SOURCE,),
    ).fetchall()
    _need(len(sessions) == 1, "SOURCE_EPOCH_AMBIGUOUS")
    (session, market, producer, state, subscribed, last_accepted, ended, reason) = (
        _fields(
            sessions[0], "source_session_id", "market_source_id",
            "producer_release_commit", "state", "subscribed_at",
            "last_accepted_at", "ended_at", "reason",
        )
    )
    _need(
        isinstance(session, str)
        and _SESSION.fullmatch(session) is not None
        and market == _SOURCE and producer in accepted_producer_commits
        and state == "ACTIVE" and ended is None and reason is None
        and _utc(subscribed, "SOURCE_START_INVALID") <= fill,
        "SOURCE_SESSION_INVALID",
    )
    row = connection.execute(
        """SELECT publication_id,source_session_id,producer_release_commit,
                  as_of_second,publication_status,native_watermarks
             FROM analytics.market_publications
            WHERE market_source_id=%s AND source_session_id=%s
            ORDER BY as_of_second DESC,publication_id DESC LIMIT 1""",
        (_SOURCE, session),
    ).fetchone()
    _need(row is not None, "PUBLICATION_MISSING")
    pid, pub_session, pub_producer, as_of, pub_state, native_refs = _fields(
        row, "publication_id", "source_session_id", "producer_release_commit",
        "as_of_second", "publication_status", "native_watermarks",
    )
    pub_at = _utc(as_of, "PUBLICATION_TIME_INVALID")
    _need(
        type(pid) is int and pid > 0
        and pub_session == session and pub_producer == producer
        and pub_state == "WATERMARKS_ONLY"
        and pub_at.microsecond == 0
        and protected <= pub_at <= now
        and now - pub_at <= _MAX_PUBLICATION_AGE
        and _utc(last_accepted, "SOURCE_LAST_ACCEPTED_INVALID")
        >= pub_at - _MAX_PUBLICATION_AGE
        and isinstance(native_refs, dict),
        "PUBLICATION_EPOCH_OR_AGE_INVALID",
    )
    native = native_refs.get(position.symbol)
    instrument = bybit_instrument_id("linear", position.symbol)
    _need(isinstance(native, dict), "ORIGINAL_NATIVE_REF_MISSING")
    frame, index, seq, ident = (
        native.get("frame_number"), native.get("frame_index"),
        native.get("exchange_sequence"), native.get("exec_id"),
    )
    cursor_at = _utc(native.get("event_at"), "CURSOR_TIME_INVALID")
    cursor_accepted = _utc(native.get("accepted_at"), "CURSOR_ACCEPTED_INVALID")
    _need(
        native.get("instrument_id") == instrument
        and type(frame) is int and frame >= 1
        and type(index) is int and index >= 0
        and type(seq) is int and seq >= 0
        and isinstance(ident, str) and bool(ident)
        and protected <= cursor_at <= pub_at
        and pub_at - cursor_at <= _MAX_PUBLICATION_AGE
        and cursor_at <= cursor_accepted <= pub_at + timedelta(seconds=5),
        "LATEST_NATIVE_CURSOR_NOT_FRESH_OR_IDENTICAL",
    )
    # A real prefill original trade anchors the start of coverage in this
    # exact session; a source-only as-of second cannot invent old executions.
    anchor = connection.execute(
        """SELECT frame_number,frame_index,exchange_sequence,event_at
             FROM analytics.native_public_trade_source_events
            WHERE source_session_id=%s AND market_source_id=%s
              AND instrument_id=%s AND external_symbol=%s AND event_at<%s
            ORDER BY frame_number DESC,frame_index DESC LIMIT 1""",
        (session, _SOURCE, instrument, position.symbol, fill),
    ).fetchone()
    _need(anchor is not None, "PREFILL_ORIGINAL_ANCHOR_MISSING")
    a_frame, a_index, a_seq, a_at = _fields(
        anchor, "frame_number", "frame_index", "exchange_sequence", "event_at",
    )
    _need(
        type(a_frame) is int and type(a_index) is int
        and type(a_seq) is int and a_frame >= 1 and a_index >= 0 and a_seq >= 0
        and fill - _ANCHOR_LOOKBACK <= _utc(a_at, "ANCHOR_TIME_INVALID") < fill,
        "PREFILL_ORIGINAL_ANCHOR_STALE",
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
              AND e.event_at >= %s AND e.event_at <= %s
            ORDER BY e.frame_number,e.frame_index
            LIMIT %s""",
        (session, _SOURCE, instrument, position.symbol, fill, pub_at,
         _MAX_EVENTS + 1),
    ).fetchall()
    # Never claim "zero market trade" based solely on a cursor/watermark.
    _need(bool(rows), "POSTFILL_ORIGINAL_TRADE_MISSING")
    _need(len(rows) <= _MAX_EVENTS, "POSTFILL_NATIVE_BUDGET_EXCEEDED")
    prices: list[Decimal] = []
    refs: list[str] = []
    seen: set[tuple[int, str]] = set()
    previous_point = (a_frame, a_index)
    previous_seq, previous_at = a_seq, fill
    last_execution: str | None = None
    for item in rows:
        (
            fnum, idx, market, inst, symbol, exchange_seq, execution,
            event_at, accepted_at, journal_at, price, qty, side,
            transport, release,
        ) = _fields(
            item, "frame_number", "frame_index", "market_source_id",
            "instrument_id", "external_symbol", "exchange_sequence",
            "exec_id", "event_at", "accepted_at", "journal_at",
            "price", "quantity", "taker_side", "source_transport",
            "producer_release_commit",
        )
        event_time = _utc(event_at, "ORIGINAL_EVENT_TIME_INVALID")
        quantity = _positive(qty)
        original_price = _positive(price)
        point = (fnum, idx)
        _need(
            type(fnum) is int and type(idx) is int
            and point > previous_point and fnum >= 1 and idx >= 0
            and market == _SOURCE and inst == instrument and symbol == position.symbol
            and type(exchange_seq) is int and exchange_seq >= previous_seq
            and isinstance(execution, str) and bool(execution)
            and (exchange_seq, execution) not in seen
            and fill <= event_time <= pub_at and event_time >= previous_at
            and _utc(accepted_at, "ORIGINAL_RECEIPT_INVALID")
            >= event_time - timedelta(seconds=2)
            and _utc(accepted_at, "ORIGINAL_RECEIPT_INVALID")
            <= pub_at + timedelta(seconds=5)
            and _utc(journal_at, "ORIGINAL_JOURNAL_TIME_INVALID") == event_time
            and quantity > 0 and side in ("Buy", "Sell")
            and transport == "BYBIT_WS_PUBLIC_TRADE"
            and release == producer,
            "ORIGINAL_NATIVE_SEQUENCE_OR_JOURNAL_DRIFT",
        )
        seen.add((exchange_seq, execution))
        prices.append(original_price)
        refs.append(f"bybit:publicTrade:{position.symbol}:{execution}")
        previous_point, previous_seq, previous_at = point, exchange_seq, event_time
        last_execution = execution
    _need(
        previous_point == (frame, index)
        and previous_seq == seq
        and last_execution == ident,
        "PUBLICATION_LATEST_ORIGINAL_CURSOR_DRIFT",
    )
    entry_price = _positive(position.actual_avg_fill)
    best = (
        max([entry_price, *prices])
        if position.direction is TradeDirection.LONG
        else min([entry_price, *prices])
    )
    return R09NativePublicationCandidate(
        position_id=position.strategy_position_id,
        symbol=position.symbol,
        source_session_id=session,
        publication_id=pid,
        fill_at=fill,
        covered_through=pub_at,
        source_commit=source_commit,
        producer_release_commit=producer,
        last_native_price=prices[-1],
        observed_favorable_price=best,
        native_event_count=len(prices),
        source_refs=tuple(refs),
    )
