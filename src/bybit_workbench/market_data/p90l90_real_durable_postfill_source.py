"""Independent original MarketSource + venue-REST first-fill proof.

This is an ALTERNATIVE short postfill source reader, not a substitute for
P90 old-epoch restart, historical seq coverage, or independent Exit safety.
The credential-free MarketSource has already committed original trade rows.
The current Bybit recent-trade page must independently cover the ENTIRE
fill-forward <=15s window, with a real pre-fill trade and exact immutable
row-for-row match to the source journal. If it does not, fail closed.

The only consumer is credential-local Universal Exit on the exact error
that the older Observer-owned source IPC transport is unavailable. No
position/plan, Strategy, Exchange, gate or source checkpoint writes occur.
"""
from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
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
from bybit_workbench.universal_exit.p90l90_real_observation import (
    P90RealPostfillSourceWindow,
)

_SOURCE = BYBIT_PUBLIC_MARKET_SOURCE_ID
_LIMIT = 1000
_MAX_POSTFILL_NATIVE = 256
_WINDOW = timedelta(seconds=15)
_FRESH = timedelta(seconds=5)
_PUBLIC_REST = "https://api.bybit.kz"
FetchRecent = Callable[[str], tuple[Sequence[Mapping[str, object]], datetime]]


class P90IndependentDurablePostfillUnverified(RuntimeError):
    """Original short postfill interval is not independently verified."""


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise P90IndependentDurablePostfillUnverified(
            "P90_REAL_DURABLE_POSTFILL_" + code
        )


def _utc(value: datetime) -> datetime:
    _need(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(),
        "TIME_INVALID",
    )
    return value.astimezone(UTC)


def _value(raw: object) -> Decimal:
    try:
        number = Decimal(str(raw))
    except (ValueError, TypeError, InvalidOperation):
        raise P90IndependentDurablePostfillUnverified(
            "P90_REAL_DURABLE_POSTFILL_PRICE_INVALID"
        ) from None
    _need(number.is_finite() and number > 0, "PRICE_INVALID")
    return number


def fetch_bybit_recent_public_trades(
    symbol: str,
) -> tuple[Sequence[Mapping[str, object]], datetime]:
    """Bounded credential-free Bybit KZ V5 call; no Venue POST or auth."""
    _need(symbol in set().union(*ALLOWED_SYMBOLS.values()), "SYMBOL_INVALID")
    query = urllib.parse.urlencode({
        "category": "linear", "symbol": symbol, "limit": _LIMIT,
    })
    request = urllib.request.Request(
        f"{_PUBLIC_REST}/v5/market/recent-trade?{query}",
        headers={"User-Agent": "Cripta-P90-Independent-Exit-Source/1"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=2.5, context=ssl.create_default_context(),
        ) as response:
            raw = response.read(_LIMIT * 1400)
            data = json.loads(raw)
        _need(isinstance(data, dict) and data.get("retCode") == 0, "VENUE_REJECTED")
        payload = data.get("result")
        rows = payload.get("list") if isinstance(payload, dict) else None
        _need(isinstance(rows, list) and 1 <= len(rows) <= _LIMIT,
              "VENUE_WINDOW_MISSING")
        server_at = datetime.fromtimestamp(int(str(data.get("time"))) / 1000, UTC)
        _utc(server_at)
        return rows, server_at
    except P90IndependentDurablePostfillUnverified:
        raise
    except (OSError, ValueError, TypeError, OverflowError, KeyError):
        raise P90IndependentDurablePostfillUnverified(
            "P90_REAL_DURABLE_POSTFILL_VENUE_UNAVAILABLE"
        ) from None


def _fields(record: object, *names: str) -> tuple[object, ...]:
    """Support the account Exit dict_row connection and tuple-row test ports."""
    if isinstance(record, Mapping):
        try:
            return tuple(record[name] for name in names)
        except KeyError:
            raise P90IndependentDurablePostfillUnverified(
                "P90_REAL_DURABLE_POSTFILL_DATABASE_ROW_INVALID"
            ) from None
    if isinstance(record, (tuple, list)) and len(record) == len(names):
        return tuple(record)
    raise P90IndependentDurablePostfillUnverified(
        "P90_REAL_DURABLE_POSTFILL_DATABASE_ROW_INVALID"
    )


def _source_session(connection: Any, *, source_commit: str,
                    producer_commits: frozenset[str],
                    fill_at: datetime) -> str:
    rows = connection.execute(
        """SELECT source_session_id,market_source_id,producer_release_commit,
                  state,subscribed_at,last_accepted_at,ended_at,reason
             FROM analytics.native_public_trade_source_sessions
            WHERE market_source_id=%s AND state='ACTIVE'
            ORDER BY subscribed_at DESC LIMIT 2""",
        (_SOURCE,),
    ).fetchall()
    _need(len(rows) == 1, "SOURCE_SESSION_AMBIGUOUS")
    session, market, producer, state, subscribed, accepted, ended, reason = (
        _fields(rows[0], "source_session_id", "market_source_id",
                "producer_release_commit", "state", "subscribed_at",
                "last_accepted_at", "ended_at", "reason")
    )
    _need(
        market == _SOURCE and producer in producer_commits
        and state == "ACTIVE" and ended is None and reason is None
        and isinstance(session, str) and len(session) == 32
        and _utc(subscribed) <= fill_at
        and _utc(accepted) >= fill_at,
        "SOURCE_SESSION_INCOMPLETE",
    )
    return session


def _rest_window(
    rows: Sequence[Mapping[str, object]], *,
    symbol: str, fill_at: datetime, through: datetime,
) -> dict[tuple[int, str], tuple[datetime, Decimal, Decimal, str]]:
    _need(1 <= len(rows) <= _LIMIT, "VENUE_WINDOW_MISSING")
    all_ids: set[str] = set()
    seen: dict[tuple[int, str], tuple[datetime, Decimal, Decimal, str]] = {}
    older = False
    prev_seq: int | None = None
    prev_at: datetime | None = None
    for row in rows:
        _need(
            isinstance(row, Mapping) and row.get("symbol") == symbol,
            "VENUE_IDENTITY_INVALID",
        )
        ident = row.get("execId")
        try:
            seq = int(str(row["seq"]))
            when = datetime.fromtimestamp(
                int(str(row["time"])) / 1000, UTC,
            )
            qty = _value(row["size"])
            price = _value(row["price"])
        except (KeyError, ValueError, TypeError, OverflowError):
            raise P90IndependentDurablePostfillUnverified(
                "P90_REAL_DURABLE_POSTFILL_VENUE_IDENTITY_INVALID"
            ) from None
        side = row.get("side")
        _need(
            isinstance(ident, str) and ident and ident not in all_ids
            and seq >= 0 and side in ("Buy", "Sell")
            and (prev_seq is None or (
                seq <= prev_seq
                and prev_at is not None and when <= prev_at
            )),
            "VENUE_ORDER_OR_DUPLICATE",
        )
        all_ids.add(ident)
        prev_seq, prev_at = seq, when
        if when < fill_at:
            older = True
        elif when <= through:
            seen[(seq, ident)] = (when, price, qty, side)
    _need(older, "VENUE_PREFILL_ANCHOR_EVICTED")
    _need(len(seen) <= _MAX_POSTFILL_NATIVE, "SOURCE_BUDGET_EXCEEDED")
    return seen


def read_independent_durable_postfill_window(
    connection: Any, *,
    position: StrategyPosition,
    source_epoch_id: str,
    source_commit: str,
    accepted_producer_commits: frozenset[str],
    observed_at: datetime,
    fetch_recent: FetchRecent = fetch_bybit_recent_public_trades,
) -> P90RealPostfillSourceWindow:
    """Fresh receipt corroborated by Bybit history, not Observer readiness.

    No caller-provided `complete_native_window` or native trade identifier is
    accepted. An unverified missing broker stays a block unless the separate
    immutable original producer and independent Bybit venue agree EXACTLY.
    """
    at = _utc(observed_at)
    fill = _utc(position.fill_at)
    protected = _utc(position.initial_protection_confirmed_at)
    _need(
        position.strategy_id in ALLOWED_SYMBOLS
        and position.strategy_version == VERSION
        and position.symbol in ALLOWED_SYMBOLS[position.strategy_id]
        and position.identity_status == "PROVEN"
        and bool(position.trading_account_id)
        and bool(position.strategy_execution_binding_id)
        and source_epoch_id.startswith("p90-real-epoch-")
        and len(source_epoch_id) == 55
        and len(source_commit) == 40
        and accepted_producer_commits
        and all(len(item) == 40 for item in accepted_producer_commits)
        and fill <= protected <= at
        and at - protected <= _WINDOW,
        "EXACT_SCOPE_OR_FILL_INVALID",
    )
    epoch = connection.execute(
        """SELECT source_commit,status
             FROM strategy_entry.p90l90_real_source_epochs
            WHERE epoch_id=%s""",
        (source_epoch_id,),
    ).fetchone()
    _need(
        epoch is not None
        and _fields(epoch, "source_commit", "status")
        == (source_commit, "ACTIVE"),
        "SOURCE_EPOCH_UNVERIFIED",
    )
    session = _source_session(
        connection, source_commit=source_commit,
        producer_commits=accepted_producer_commits,
        fill_at=fill,
    )

    recent, server_at = fetch_recent(position.symbol)
    venue_at = _utc(server_at)
    _need(
        timedelta() <= venue_at - at <= _FRESH,
        "VENUE_TIME_UNVERIFIED",
    )
    native = _rest_window(recent, symbol=position.symbol,
                          fill_at=fill, through=at)
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
            WHERE e.source_session_id=%s AND e.external_symbol=%s
              AND e.event_at >= %s AND e.event_at <= %s
            ORDER BY e.frame_number,e.frame_index
            LIMIT %s""",
        (session, position.symbol, fill, at, _MAX_POSTFILL_NATIVE + 1),
    ).fetchall()
    _need(len(rows) <= _MAX_POSTFILL_NATIVE, "SOURCE_BUDGET_EXCEEDED")
    recorded: set[tuple[int, str]] = set()
    last_point = (0, 0)
    previous_seq = -1
    previous_at = fill
    prices: list[Decimal] = []
    ids: list[str] = []
    for row in rows:
        (
            frame, index, market, instrument, symbol, seq, ident, when,
            accepted, journal_when, price, qty, side, transport, producer,
        ) = _fields(
            row, "frame_number", "frame_index", "market_source_id",
            "instrument_id", "external_symbol", "exchange_sequence",
            "exec_id", "event_at", "accepted_at", "journal_at", "price",
            "quantity", "taker_side", "source_transport",
            "producer_release_commit",
        )
        point = (frame, index)
        when = _utc(when)
        _need(
            type(frame) is int and type(index) is int
            and frame >= 1 and index >= 0 and point > last_point
            and market == _SOURCE
            and instrument == bybit_instrument_id("linear", position.symbol)
            and symbol == position.symbol
            and type(seq) is int and seq >= previous_seq
            and when >= previous_at
            and isinstance(ident, str) and bool(ident)
            and (seq, ident) not in recorded
            and journal_when == when
            and isinstance(price, Decimal) and price.is_finite() and price > 0
            and isinstance(qty, Decimal) and qty.is_finite() and qty > 0
            and side in ("Buy", "Sell")
            and transport == "BYBIT_WS_PUBLIC_TRADE"
            and producer in accepted_producer_commits
            and _utc(accepted) >= when - timedelta(seconds=2)
            and native.get((seq, ident)) == (when, price, qty, side),
            "SOURCE_VENUE_NATIVE_DRIFT",
        )
        recorded.add((seq, ident))
        prices.append(price)
        ids.append(ident)
        last_point, previous_seq, previous_at = point, seq, when
    _need(recorded == set(native), "SOURCE_VENUE_NATIVE_MISSING")
    second = _source_session(
        connection, source_commit=source_commit,
        producer_commits=accepted_producer_commits,
        fill_at=fill,
    )
    _need(second == session, "SOURCE_CHANGED_DURING_PROOF")
    if not prices:
        return P90RealPostfillSourceWindow(
            position_id=position.strategy_position_id,
            symbol=position.symbol, fill_at=fill, covered_through=at,
            source_commit=source_commit,
            last_native_price=position.actual_avg_fill,
            best_favorable_price=position.actual_avg_fill,
            source_refs=(
                f"bybit:recentTrade:{position.symbol}:zero-after-fill:"
                f"{venue_at.isoformat()}",
            ),
            complete_native_window=True,
            no_new_native_trade_rest_verified=True,
        )
    best = (
        max([position.actual_avg_fill, *prices])
        if position.direction is TradeDirection.LONG else
        min([position.actual_avg_fill, *prices])
    )
    return P90RealPostfillSourceWindow(
        position_id=position.strategy_position_id,
        symbol=position.symbol, fill_at=fill, covered_through=at,
        source_commit=source_commit, last_native_price=prices[-1],
        best_favorable_price=best,
        source_refs=tuple(
            f"bybit:publicTrade:{position.symbol}:{ident}" for ident in ids
        ),
        complete_native_window=True,
    )
