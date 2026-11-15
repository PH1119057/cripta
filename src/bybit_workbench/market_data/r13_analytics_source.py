"""R13 #470 downstream analytics/read-model access to the shared MarketSource.

This module creates no market connection, no Strategy decision and no trading
permission.  It reads committed PostgreSQL facts produced by the one canonical
public MarketSource.  Private signed Bybit safety remains a separate authority.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

_EPOCH = re.compile(r"^[0-9a-f]{32}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{2,32}$")
_PUBLICATION_MAX_AGE = timedelta(seconds=30)


class R13AnalyticsSourceUnavailable(RuntimeError):
    """Shared source/publication evidence is missing or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class R13SourceEpoch:
    source_session_id: str
    producer_release_commit: str
    subscribed_at: datetime
    last_accepted_at: datetime | None


@dataclass(frozen=True, slots=True)
class R13OrderedTrade:
    source_session_id: str
    producer_release_commit: str
    frame_number: int
    frame_index: int
    symbol: str
    exchange_sequence: int
    exec_id: str
    event_at: datetime
    price: Decimal
    quantity: Decimal
    taker_side: str


@dataclass(frozen=True, slots=True)
class R13PublicationTicker:
    symbol: str
    publication_id: int
    source_session_id: str
    producer_release_commit: str
    publication_as_of: datetime
    observed_second: datetime
    source_time_max: datetime
    last_price: Decimal
    mark_price: Decimal | None
    bid_price: Decimal | None
    ask_price: Decimal | None
    turnover_24h: Decimal | None
    open_interest_value: Decimal | None
    funding_rate: Decimal | None


def _fail(code: str) -> NoReturn:
    raise R13AnalyticsSourceUnavailable("R13_ANALYTICS_" + code)


def _utc(value: object, code: str) -> datetime:
    if not isinstance(value, datetime):
        _fail(code)
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        _fail(code)
    return value.astimezone(UTC)


def _positive(value: object, code: str) -> Decimal:
    try:
        if isinstance(value, bool) or value in (None, ""):
            raise ValueError("missing")
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise ValueError("not positive")
        return number
    except (InvalidOperation, ValueError) as exc:
        raise R13AnalyticsSourceUnavailable(
            "R13_ANALYTICS_" + code
        ) from exc


def _optional_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def require_repeatable_read_only(connection: Any) -> None:
    if (
        connection.execute("SHOW transaction_isolation").fetchone()
        != ("repeatable read",)
        or connection.execute("SHOW transaction_read_only").fetchone()
        != ("on",)
    ):
        _fail("READ_ONLY_REPEATABLE_SNAPSHOT_REQUIRED")


def read_active_source_epoch(connection: Any) -> R13SourceEpoch:
    rows = connection.execute(
        """SELECT source_session_id,producer_release_commit,
                  subscribed_at,last_accepted_at
             FROM analytics.native_public_trade_source_sessions
            WHERE market_source_id=%s AND state='ACTIVE'
            ORDER BY subscribed_at DESC LIMIT 2""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID,),
    ).fetchall()
    if len(rows) != 1:
        _fail("ONE_ACTIVE_SOURCE_EPOCH_REQUIRED")
    session, producer, subscribed, accepted = rows[0]
    if (
        not isinstance(session, str)
        or _EPOCH.fullmatch(session) is None
        or not isinstance(producer, str)
        or _SHA.fullmatch(producer) is None
    ):
        _fail("SOURCE_EPOCH_IDENTITY_INVALID")
    started = _utc(subscribed, "SOURCE_SUBSCRIBED_AT_INVALID")
    last = None if accepted is None else _utc(
        accepted, "SOURCE_LAST_ACCEPTED_AT_INVALID"
    )
    return R13SourceEpoch(session, producer, started, last)


def read_opportunity_trade_batch(
    connection: Any,
    *,
    epoch: R13SourceEpoch,
    symbol: str,
    signal_at: datetime,
    after_frame: int,
    after_index: int,
    limit: int = 512,
) -> tuple[R13OrderedTrade, ...]:
    """Read exact original provider order for one research opportunity."""
    if (
        not isinstance(epoch, R13SourceEpoch)
        or _SYMBOL.fullmatch(symbol) is None
        or signal_at.tzinfo is None
        or signal_at.utcoffset() != timedelta(0)
        or type(after_frame) is not int
        or type(after_index) is not int
        or after_frame < 0
        or after_index < 0
        or type(limit) is not int
        or not 1 <= limit <= 1000
    ):
        _fail("OPPORTUNITY_READ_BOUNDARY_INVALID")
    instrument = bybit_instrument_id("linear", symbol)
    rows = connection.execute(
        """SELECT e.frame_number,e.frame_index,e.external_symbol,
                  e.exchange_sequence,e.exec_id,e.event_at,
                  n.price,n.quantity,n.taker_side
             FROM analytics.native_public_trade_source_events e
             JOIN analytics.native_public_trade_events n
               ON n.market_source_id=e.market_source_id
              AND n.instrument_id=e.instrument_id
              AND n.exchange_sequence=e.exchange_sequence
              AND n.exec_id=e.exec_id
            WHERE e.source_session_id=%s
              AND e.market_source_id=%s
              AND e.instrument_id=%s
              AND e.external_symbol=%s
              AND e.event_at >= %s
              AND (e.frame_number,e.frame_index)>(%s,%s)
            ORDER BY e.frame_number,e.frame_index
            LIMIT %s""",
        (
            epoch.source_session_id,
            BYBIT_PUBLIC_MARKET_SOURCE_ID,
            instrument,
            symbol,
            signal_at.astimezone(UTC),
            after_frame,
            after_index,
            limit,
        ),
    ).fetchall()
    result: list[R13OrderedTrade] = []
    previous = (after_frame, after_index)
    seen: set[tuple[int, str]] = set()
    for row in rows:
        frame, index, row_symbol, seq, exec_id, event_at, price, qty, side = row
        point = (frame, index)
        if (
            type(frame) is not int
            or type(index) is not int
            or point <= previous
            or row_symbol != symbol
            or type(seq) is not int
            or seq < 0
            or not isinstance(exec_id, str)
            or not exec_id
            or (seq, exec_id) in seen
            or side not in ("Buy", "Sell")
        ):
            _fail("OPPORTUNITY_ORDERED_EVENT_IDENTITY_INVALID")
        at = _utc(event_at, "OPPORTUNITY_EVENT_AT_INVALID")
        item = R13OrderedTrade(
            source_session_id=epoch.source_session_id,
            producer_release_commit=epoch.producer_release_commit,
            frame_number=frame,
            frame_index=index,
            symbol=symbol,
            exchange_sequence=seq,
            exec_id=exec_id,
            event_at=at,
            price=_positive(price, "OPPORTUNITY_PRICE_INVALID"),
            quantity=_positive(qty, "OPPORTUNITY_QUANTITY_INVALID"),
            taker_side=side,
        )
        result.append(item)
        previous = point
        seen.add((seq, exec_id))
    return tuple(result)


def read_publication_tickers(
    connection: Any,
    *,
    symbols: tuple[str, ...],
    observed_at: datetime,
) -> dict[str, R13PublicationTicker]:
    """Read exact ticker refs from one current MarketPublication.

    Missing instruments remain missing; this reader never falls back to Bybit.
    """
    require_repeatable_read_only(connection)
    now = _utc(observed_at, "CLOCK_INVALID")
    requested = tuple(dict.fromkeys(symbols))
    if (
        not requested
        or len(requested) > 64
        or any(_SYMBOL.fullmatch(x) is None for x in requested)
    ):
        _fail("TICKER_SCOPE_INVALID")
    epoch = read_active_source_epoch(connection)
    row = connection.execute(
        """SELECT publication_id,source_session_id,producer_release_commit,
                  as_of_second,publication_status,
                  compact_second_refs,compact_field_coverage
             FROM analytics.market_publications
            WHERE market_source_id=%s AND source_session_id=%s
            ORDER BY as_of_second DESC,publication_id DESC LIMIT 1""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID, epoch.source_session_id),
    ).fetchone()
    if row is None or not isinstance(row, (tuple, list)) or len(row) != 7:
        _fail("PUBLICATION_MISSING")
    pid, session, producer, as_of, status, refs, quality = row
    published = _utc(as_of, "PUBLICATION_TIME_INVALID")
    if (
        type(pid) is not int
        or pid <= 0
        or session != epoch.source_session_id
        or producer != epoch.producer_release_commit
        or status != "WATERMARKS_ONLY"
        or not timedelta(0) <= now - published <= _PUBLICATION_MAX_AGE
        or not isinstance(refs, dict)
        or not isinstance(quality, dict)
    ):
        _fail("PUBLICATION_IDENTITY_OR_AGE_INVALID")

    wanted: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}
    for symbol in requested:
        instrument = bybit_instrument_id("linear", symbol)
        by_dataset = refs.get(instrument)
        by_quality = quality.get(instrument)
        ref = by_dataset.get("TICKER") if isinstance(by_dataset, dict) else None
        state = (
            by_quality.get("TICKER")
            if isinstance(by_quality, dict) else None
        )
        fields = state.get("fields") if isinstance(state, dict) else None
        if (
            not isinstance(ref, dict)
            or not isinstance(state, dict)
            or state.get("status") != "OBSERVED"
            or not isinstance(fields, dict)
            or fields.get("lastPrice") != "FRESH"
            or ref.get("source_session_id") != epoch.source_session_id
            or ref.get("instrument_id") != instrument
            or ref.get("product_category") != "LINEAR"
            or ref.get("external_symbol") != symbol
        ):
            continue
        wanted[instrument] = (symbol, ref, state)

    result: dict[str, R13PublicationTicker] = {}
    for instrument, (symbol, ref, _state) in wanted.items():
        second = ref.get("observed_second")
        if not isinstance(second, str):
            _fail("TICKER_REF_SECOND_INVALID")
        try:
            observed_second = datetime.fromisoformat(second)
        except ValueError as exc:
            raise R13AnalyticsSourceUnavailable(
                "R13_ANALYTICS_TICKER_REF_SECOND_INVALID"
            ) from exc
        observed_second = _utc(
            observed_second, "TICKER_REF_SECOND_INVALID"
        )
        physical = connection.execute(
            """SELECT source_session_id,instrument_id,product_category,
                      external_symbol,observed_second,source_time_max,
                      compact_features,producer_release_commit
                 FROM analytics.public_market_source_seconds
                WHERE source_session_id=%s AND instrument_id=%s
                  AND observed_second=%s""",
            (epoch.source_session_id, instrument, observed_second),
        ).fetchone()
        if physical is None or len(physical) != 8:
            _fail("TICKER_PHYSICAL_ROW_MISSING")
        (
            row_session, row_instrument, product, row_symbol, row_second,
            source_time, compact, row_producer,
        ) = physical
        if (
            row_session != epoch.source_session_id
            or row_instrument != instrument
            or product != "LINEAR"
            or row_symbol != symbol
            or row_producer != epoch.producer_release_commit
            or _utc(row_second, "TICKER_ROW_SECOND_INVALID")
            != observed_second
            or not isinstance(compact, dict)
        ):
            _fail("TICKER_PHYSICAL_IDENTITY_DRIFT")
        ticker = compact.get("ticker")
        if not isinstance(ticker, dict):
            _fail("TICKER_PAYLOAD_MISSING")
        result[symbol] = R13PublicationTicker(
            symbol=symbol,
            publication_id=pid,
            source_session_id=epoch.source_session_id,
            producer_release_commit=epoch.producer_release_commit,
            publication_as_of=published,
            observed_second=observed_second,
            source_time_max=_utc(
                source_time, "TICKER_SOURCE_TIME_INVALID"
            ),
            last_price=_positive(
                ticker.get("lastPrice"), "TICKER_LAST_PRICE_INVALID"
            ),
            mark_price=_optional_decimal(ticker.get("markPrice")),
            bid_price=_optional_decimal(ticker.get("bid1Price")),
            ask_price=_optional_decimal(ticker.get("ask1Price")),
            turnover_24h=_optional_decimal(ticker.get("turnover24h")),
            open_interest_value=_optional_decimal(
                ticker.get("openInterestValue")
            ),
            funding_rate=_optional_decimal(ticker.get("fundingRate")),
        )
    return result
