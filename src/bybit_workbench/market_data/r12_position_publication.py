"""R12: Position Supervisor reads one immutable shared MarketPublication.

No Bybit REST/WS, no market writer, no account/private substitution and no
trading mutation.  The caller supplies one REPEATABLE READ READ ONLY
PostgreSQL transaction.  Forming 1m, six closed 1m bars, ticker and exact
top-25 orderbook depth must all belong to the same active source epoch and
publication.  Missing/stale/split evidence fails closed.

Account-local mark/protection samples are intentionally outside this adapter;
they remain owned by private/account state and MUST NOT be promoted to exact
native P90 MFE evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn, cast

from bybit_workbench.market_data.shared_closed_candles import (
    MarketCandleKey,
    SharedClosedCandleStore,
)
from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

_EPOCH = re.compile(r"^[0-9a-f]{32}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{2,32}$")
_PUBLICATION_MAX_AGE = timedelta(seconds=30)
_POSITION_FORMING_TTL = 8
_POSITION_CLOSED_TTL = 75
_POSITION_TICKER_TTL = 2
_POSITION_BOOK_TTL = 3
_TICKER_FIELDS = ("lastPrice", "bid1Price", "ask1Price", "markPrice")


class R12PositionPublicationUnavailable(RuntimeError):
    """Required Position public-market evidence is absent or unverified."""


@dataclass(frozen=True, slots=True)
class R12PositionMarketSnapshot:
    symbol: str
    publication_id: int
    source_session_id: str
    producer_release_commit: str
    as_of_second: datetime
    forming_opened_at: datetime
    forming_source_at: datetime
    forming_close: Decimal
    closed_closes_newest_first: tuple[Decimal, ...]
    ticker_source_at: datetime
    last_price: Decimal
    bid_price: Decimal
    ask_price: Decimal
    public_mark_price: Decimal
    book_source_at: datetime
    top25_bid_depth_usdt: Decimal
    top25_ask_depth_usdt: Decimal


def _fail(code: str) -> NoReturn:
    raise R12PositionPublicationUnavailable("R12_POSITION_" + code)


def _utc(value: object, code: str) -> datetime:
    parsed: datetime
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise R12PositionPublicationUnavailable(
                "R12_POSITION_" + code
            ) from exc
    elif isinstance(value, datetime):
        parsed = value
    else:
        _fail(code)
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
    ):
        _fail(code)
    return parsed.astimezone(UTC)


def _positive(value: object, code: str) -> Decimal:
    try:
        if isinstance(value, bool) or not isinstance(
            value, (str, int, Decimal),
        ):
            raise ValueError("not numeric")
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise ValueError("not positive")
        return number
    except (InvalidOperation, ValueError) as exc:
        raise R12PositionPublicationUnavailable(
            "R12_POSITION_" + code
        ) from exc


def _pin(
    connection: Any,
    *,
    symbol: str,
    observed_at: datetime,
) -> tuple[
    int, str, str, datetime, str,
    dict[str, Any], dict[str, Any],
    dict[str, Any], dict[str, Any],
    dict[str, Any], dict[str, Any],
]:
    now = _utc(observed_at, "CLOCK_NOT_UTC")
    if not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None:
        _fail("SYMBOL_INVALID")
    if (
        connection.execute("SHOW transaction_isolation").fetchone()
        != ("repeatable read",)
        or connection.execute("SHOW transaction_read_only").fetchone()
        != ("on",)
    ):
        _fail("READ_ONLY_REPEATABLE_SNAPSHOT_REQUIRED")
    sessions = connection.execute(
        """SELECT source_session_id,producer_release_commit
             FROM analytics.native_public_trade_source_sessions
            WHERE market_source_id=%s AND state='ACTIVE'
            ORDER BY subscribed_at DESC LIMIT 2""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID,),
    ).fetchall()
    if (
        len(sessions) != 1
        or not isinstance(sessions[0][0], str)
        or _EPOCH.fullmatch(sessions[0][0]) is None
        or not isinstance(sessions[0][1], str)
        or _SHA.fullmatch(sessions[0][1]) is None
    ):
        _fail("ONE_ACTIVE_SOURCE_EPOCH_REQUIRED")
    epoch, commit = sessions[0]
    row = connection.execute(
        """SELECT publication_id,source_session_id,producer_release_commit,
                  as_of_second,publication_status,
                  forming_minute_refs,forming_field_coverage,
                  closed_instrument_refs,closed_instrument_coverage,
                  compact_second_refs,compact_field_coverage
             FROM analytics.market_publications
            WHERE market_source_id=%s AND source_session_id=%s
            ORDER BY as_of_second DESC,publication_id DESC LIMIT 1""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID, epoch),
    ).fetchone()
    if row is None or not isinstance(row, (tuple, list)) or len(row) != 11:
        _fail("PUBLICATION_MISSING")
    (
        pid, pub_epoch, pub_commit, as_of, status,
        forming, forming_quality, closed, closed_quality,
        compact, compact_quality,
    ) = row
    published = _utc(as_of, "PUBLICATION_CLOCK_INVALID")
    if (
        type(pid) is not int
        or pid <= 0
        or pub_epoch != epoch
        or pub_commit != commit
        or status != "WATERMARKS_ONLY"
        or published.microsecond != 0
        or not timedelta(0) <= now - published <= _PUBLICATION_MAX_AGE
        or any(
            not isinstance(item, dict)
            for item in (
                forming, forming_quality, closed, closed_quality,
                compact, compact_quality,
            )
        )
    ):
        _fail("PUBLICATION_IDENTITY_OR_AGE_INVALID")
    instrument = bybit_instrument_id("linear", symbol)
    return (
        pid, epoch, commit, published, instrument,
        forming.get(instrument, {}), forming_quality.get(instrument, {}),
        closed.get(instrument, {}), closed_quality.get(instrument, {}),
        compact.get(instrument, {}), compact_quality.get(instrument, {}),
    )


def _forming(
    connection: Any,
    *,
    pin: tuple[Any, ...],
    symbol: str,
) -> tuple[datetime, datetime, Decimal]:
    (
        _pid, epoch, commit, as_of, instrument,
        refs, quality, _closed, _closed_quality, _compact, _compact_quality,
    ) = pin
    raw_ref = refs.get("FORMING_KLINE") if isinstance(refs, dict) else None
    raw_state = (
        quality.get("FORMING_KLINE") if isinstance(quality, dict) else None
    )
    if not isinstance(raw_ref, dict) or not isinstance(raw_state, dict):
        _fail("FORMING_1M_REF_UNVERIFIED")
    ref = cast(dict[str, Any], raw_ref)
    state = cast(dict[str, Any], raw_state)
    if (
        state.get("required") is not True
        or state.get("status") != "UNCONFIRMED_OPEN_MINUTE"
        or state.get("freshness_seconds") != _POSITION_FORMING_TTL
        or ref.get("source_session_id") != epoch
        or ref.get("instrument_id") != instrument
        or ref.get("product_category") != "LINEAR"
        or ref.get("external_symbol") != symbol
        or ref.get("timeframe") != "1"
        or type(ref.get("forming_observation_id")) is not int
    ):
        _fail("FORMING_1M_REF_UNVERIFIED")
    source_at = _utc(ref.get("source_at"), "FORMING_1M_SOURCE_TIME_INVALID")
    opened = _utc(ref.get("opened_at"), "FORMING_1M_OPEN_TIME_INVALID")
    closed = _utc(ref.get("closed_at"), "FORMING_1M_CLOSE_TIME_INVALID")
    if (
        opened != opened.replace(second=0, microsecond=0)
        or closed - opened != timedelta(minutes=1)
        or not opened <= as_of < closed
        or not timedelta(0) <= as_of - source_at
        <= timedelta(seconds=_POSITION_FORMING_TTL)
    ):
        _fail("FORMING_1M_BOUNDARY_OR_TTL_INVALID")
    row = connection.execute(
        """SELECT source_session_id,instrument_id,product_category,
                  external_symbol,timeframe,opened_at,closed_at,source_at,
                  provider_last_match_at,received_at,forming_ohlcv,
                  producer_release_commit
             FROM analytics.public_forming_minute_observations
            WHERE forming_observation_id=%s""",
        (ref["forming_observation_id"],),
    ).fetchone()
    if row is None or not isinstance(row, (tuple, list)) or len(row) != 12:
        _fail("FORMING_1M_PHYSICAL_ROW_MISSING")
    (
        row_epoch, row_instrument, product, row_symbol, timeframe,
        row_open, row_close, row_source, match, received, values, row_commit,
    ) = row
    if (
        row_epoch != epoch
        or row_instrument != instrument
        or product != "LINEAR"
        or row_symbol != symbol
        or timeframe != "1"
        or _utc(row_open, "FORMING_1M_PHYSICAL_TIME_INVALID") != opened
        or _utc(row_close, "FORMING_1M_PHYSICAL_TIME_INVALID") != closed
        or _utc(row_source, "FORMING_1M_PHYSICAL_TIME_INVALID") != source_at
        or _utc(match, "FORMING_1M_MATCH_TIME_INVALID") > source_at
        or _utc(received, "FORMING_1M_RECEIVED_TIME_INVALID") >= as_of
        or row_commit != commit
        or not isinstance(values, dict)
    ):
        _fail("FORMING_1M_PHYSICAL_IDENTITY_MISMATCH")
    return opened, source_at, _positive(
        values.get("close"), "FORMING_1M_CLOSE_INVALID",
    )


def _closed(
    connection: Any,
    *,
    pin: tuple[Any, ...],
    symbol: str,
    forming_opened_at: datetime,
) -> tuple[Decimal, ...]:
    (
        _pid, _epoch, _commit, as_of, instrument,
        _forming, _forming_quality, refs, quality, _compact, _compact_quality,
    ) = pin
    raw_ref = refs.get("1") if isinstance(refs, dict) else None
    raw_state = quality.get("1") if isinstance(quality, dict) else None
    if not isinstance(raw_ref, dict) or not isinstance(raw_state, dict):
        _fail("CLOSED_1M_REF_UNVERIFIED")
    ref = cast(dict[str, Any], raw_ref)
    state = cast(dict[str, Any], raw_state)
    history_required_bars = state.get("history_required_bars")
    if (
        ref.get("market_source_id") != BYBIT_PUBLIC_MARKET_SOURCE_ID
        or ref.get("instrument_id") != instrument
        or ref.get("product_category") != "LINEAR"
        or ref.get("external_symbol") != symbol
        or ref.get("timeframe") != "1"
        or state.get("required") is not True
        or state.get("status") != "LAST_CLOSED_OBSERVED_WINDOW_UNVERIFIED"
        or state.get("history_window") != "NOT_VERIFIED"
        or state.get("freshness_seconds") != _POSITION_CLOSED_TTL
        or type(history_required_bars) is not int
        or history_required_bars < 6
    ):
        _fail("CLOSED_1M_REF_UNVERIFIED")
    through = _utc(ref.get("closed_at"), "CLOSED_1M_TIME_INVALID")
    if (
        through != forming_opened_at
        or not timedelta(0) <= as_of - through
        <= timedelta(seconds=_POSITION_CLOSED_TTL)
    ):
        _fail("CLOSED_1M_BOUNDARY_OR_TTL_INVALID")
    key = MarketCandleKey(
        BYBIT_PUBLIC_MARKET_SOURCE_ID,
        "venue-bybit",
        instrument,
        symbol,
        "LINEAR",
        "1",
    )
    try:
        bars = SharedClosedCandleStore(connection, key).history(
            through=through, required=6,
        )
    except (RuntimeError, ValueError) as exc:
        raise R12PositionPublicationUnavailable(
            "R12_POSITION_CLOSED_1M_HISTORY_UNVERIFIED"
        ) from exc
    return tuple(bar.close for bar in reversed(bars))


def _compact_row(
    connection: Any,
    *,
    pin: tuple[Any, ...],
    symbol: str,
    dataset: str,
    ttl: int,
) -> tuple[dict[str, Any], datetime]:
    (
        _pid, epoch, commit, as_of, instrument,
        _forming, _forming_quality, _closed, _closed_quality,
        refs, quality,
    ) = pin
    raw_ref = refs.get(dataset) if isinstance(refs, dict) else None
    raw_state = quality.get(dataset) if isinstance(quality, dict) else None
    if not isinstance(raw_ref, dict) or not isinstance(raw_state, dict):
        _fail(dataset + "_REF_UNVERIFIED")
    ref = cast(dict[str, Any], raw_ref)
    state = cast(dict[str, Any], raw_state)
    if (
        ref.get("source_session_id") != epoch
        or ref.get("instrument_id") != instrument
        or ref.get("product_category") != "LINEAR"
        or ref.get("external_symbol") != symbol
        or state.get("required") is not True
        or state.get("status") != "OBSERVED"
        or state.get("freshness_seconds") != ttl
    ):
        _fail(dataset + "_REF_UNVERIFIED")
    second = _utc(ref.get("observed_second"), dataset + "_SECOND_INVALID")
    ref_source = _utc(
        ref.get("source_time_max"), dataset + "_SOURCE_TIME_INVALID",
    )
    if (
        second >= as_of
        or not timedelta(0) <= as_of - second <= timedelta(seconds=ttl)
        or ref_source > as_of
    ):
        _fail(dataset + "_BOUNDARY_OR_TTL_INVALID")
    row = connection.execute(
        """SELECT market_source_id,producer_release_commit,product_category,
                  external_symbol,source_time_max,compact_features
             FROM analytics.public_market_source_seconds
            WHERE source_session_id=%s AND instrument_id=%s
              AND observed_second=%s""",
        (epoch, instrument, second),
    ).fetchone()
    if row is None or not isinstance(row, (tuple, list)) or len(row) != 6:
        _fail(dataset + "_PHYSICAL_ROW_MISSING")
    source, row_commit, product, row_symbol, physical_at, features = row
    if (
        source != BYBIT_PUBLIC_MARKET_SOURCE_ID
        or row_commit != commit
        or product != "LINEAR"
        or row_symbol != symbol
        or _utc(physical_at, dataset + "_PHYSICAL_TIME_INVALID") != ref_source
        or not isinstance(features, dict)
    ):
        _fail(dataset + "_PHYSICAL_IDENTITY_MISMATCH")
    return features, ref_source


def _ticker(
    connection: Any,
    *,
    pin: tuple[Any, ...],
    symbol: str,
) -> tuple[datetime, Decimal, Decimal, Decimal, Decimal]:
    features, source_at = _compact_row(
        connection, pin=pin, symbol=symbol,
        dataset="TICKER", ttl=_POSITION_TICKER_TTL,
    )
    ticker = features.get("ticker")
    times = features.get("ticker_field_times")
    raw_quality = (
        pin[-1].get("TICKER") if isinstance(pin[-1], dict) else None
    )
    if (
        not isinstance(ticker, dict)
        or not isinstance(times, dict)
        or not isinstance(raw_quality, dict)
    ):
        _fail("TICKER_FIELDS_NOT_FRESH")
    quality = cast(dict[str, Any], raw_quality)
    raw_fields = quality.get("fields")
    if not isinstance(raw_fields, dict):
        _fail("TICKER_FIELDS_NOT_FRESH")
    fields = cast(dict[str, Any], raw_fields)
    if any(fields.get(name) != "FRESH" for name in _TICKER_FIELDS):
        _fail("TICKER_FIELDS_NOT_FRESH")
    as_of = pin[3]
    for name in _TICKER_FIELDS:
        at = _utc(times.get(name), "TICKER_FIELD_TIME_INVALID")
        if (
            at > as_of
            or as_of - at > timedelta(seconds=_POSITION_TICKER_TTL)
        ):
            _fail("TICKER_FIELD_TTL_EXPIRED")
    last, bid, ask, mark = tuple(
        _positive(ticker.get(name), "TICKER_PRICE_INVALID")
        for name in _TICKER_FIELDS
    )
    if bid > ask:
        _fail("TICKER_CROSSED")
    return source_at, last, bid, ask, mark


def _book(
    connection: Any,
    *,
    pin: tuple[Any, ...],
    symbol: str,
) -> tuple[datetime, Decimal, Decimal]:
    features, _source_at = _compact_row(
        connection, pin=pin, symbol=symbol,
        dataset="ORDERBOOK_50", ttl=_POSITION_BOOK_TTL,
    )
    book = features.get("book")
    if not isinstance(book, dict):
        _fail("ORDERBOOK_50_PAYLOAD_MISSING")
    source_at = _utc(
        book.get("source_time"), "ORDERBOOK_50_SOURCE_TIME_INVALID",
    )
    as_of = pin[3]
    if (
        source_at > as_of
        or as_of - source_at > timedelta(seconds=_POSITION_BOOK_TTL)
    ):
        _fail("ORDERBOOK_50_SOURCE_STALE")
    bid = _positive(book.get("top25_bid_usd"), "ORDERBOOK_50_TOP25_INVALID")
    ask = _positive(book.get("top25_ask_usd"), "ORDERBOOK_50_TOP25_INVALID")
    _positive(book.get("best_bid"), "ORDERBOOK_50_BEST_PRICE_INVALID")
    _positive(book.get("best_ask"), "ORDERBOOK_50_BEST_PRICE_INVALID")
    return source_at, bid, ask


def read_r12_position_market_snapshot(
    connection: Any,
    *,
    symbol: str,
    observed_at: datetime,
) -> R12PositionMarketSnapshot:
    """One exact publication-backed Position market snapshot."""
    pin = _pin(connection, symbol=symbol, observed_at=observed_at)
    opened, forming_at, forming_close = _forming(
        connection, pin=pin, symbol=symbol,
    )
    closed = _closed(
        connection, pin=pin, symbol=symbol,
        forming_opened_at=opened,
    )
    ticker_at, last, bid, ask, mark = _ticker(
        connection, pin=pin, symbol=symbol,
    )
    book_at, book_bid, book_ask = _book(
        connection, pin=pin, symbol=symbol,
    )
    return R12PositionMarketSnapshot(
        symbol=symbol,
        publication_id=pin[0],
        source_session_id=pin[1],
        producer_release_commit=pin[2],
        as_of_second=pin[3],
        forming_opened_at=opened,
        forming_source_at=forming_at,
        forming_close=forming_close,
        closed_closes_newest_first=closed,
        ticker_source_at=ticker_at,
        last_price=last,
        bid_price=bid,
        ask_price=ask,
        public_mark_price=mark,
        book_source_at=book_at,
        top25_bid_depth_usdt=book_bid,
        top25_ask_depth_usdt=book_ask,
    )
