"""R10: Execution-only read of the SAME committed native MarketPublication.

No public Bybit REST/WS, no synthetic ticker response or Order permission.
Call under one REPEATABLE READ READ ONLY PG transaction. Missing original
data, stale fields, wrong instrument/product/source or split publications
fail closed. Private signed account/execution checks remain separate owners.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data.exit_publication_reader import (
    ExitPublicationPin,
    ExitPublicationUnavailable,
    read_exit_l53_candles,
    read_exit_ticker_last_price,
)
from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID

_REQUIRED_TICKER_FIELDS = ("lastPrice", "bid1Price", "ask1Price")


class R10ExecutionPublicationUnavailable(RuntimeError):
    """No exact market snapshot for a new Execution-side decision."""


@dataclass(frozen=True, slots=True)
class R10ExecutionQuote:
    symbol: str
    last_price: Decimal
    bid_price: Decimal
    ask_price: Decimal
    publication_id: int
    source_session_id: str
    producer_release_commit: str
    as_of_second: datetime
    field_times: tuple[datetime, datetime, datetime]


@dataclass(frozen=True, slots=True)
class R10ExecutionCausalSnapshot:
    quote: R10ExecutionQuote
    closed_5m: tuple[Candle, ...]
    history_limit: int
    publication_id: int
    source_session_id: str


def _utc(value: object) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise R10ExecutionPublicationUnavailable(
                "R10_TICKER_FIELD_CLOCK_INVALID"
            ) from exc
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise R10ExecutionPublicationUnavailable(
            "R10_TICKER_FIELD_CLOCK_INVALID"
        )
    return value.astimezone(UTC)


def _positive(value: object) -> Decimal:
    try:
        if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
            raise ValueError("not numeric")
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise ValueError("not positive")
        return number
    except (ValueError, InvalidOperation) as exc:
        raise R10ExecutionPublicationUnavailable(
            "R10_TICKER_ORIGINAL_PRICE_INVALID"
        ) from exc


def read_r10_execution_quote(
    connection: Any,
    *,
    symbol: str,
    observed_at: datetime,
    max_field_age_seconds: int = 2,
) -> R10ExecutionQuote:
    """Original linear bid/ask/lastPrice under EXECUTION_P90 freshness 2s."""
    if type(max_field_age_seconds) is not int or not 1 <= max_field_age_seconds <= 5:
        raise R10ExecutionPublicationUnavailable(
            "R10_EXECUTION_TICKER_TTL_INVALID"
        )
    now = _utc(observed_at)
    try:
        last, pin = read_exit_ticker_last_price(
            connection, symbol=symbol, observed_at=now,
        )
    except ExitPublicationUnavailable as exc:
        raise R10ExecutionPublicationUnavailable(
            "R10_EXECUTION_TICKER_SOURCE_INVALID"
        ) from exc
    if (
        not isinstance(pin, ExitPublicationPin)
        or not isinstance(pin.compact_refs, dict)
        or not isinstance(pin.compact_quality, dict)
    ):
        raise R10ExecutionPublicationUnavailable("R10_TICKER_PIN_INVALID")
    ref = pin.compact_refs.get("TICKER")
    quality = pin.compact_quality.get("TICKER")
    if (
        not isinstance(ref, dict)
        or not isinstance(quality, dict)
        or not isinstance(quality.get("fields"), dict)
        or quality.get("required") is not True
        or quality.get("status") != "OBSERVED"
        or any(
            quality["fields"].get(name) != "FRESH"
            for name in _REQUIRED_TICKER_FIELDS
        )
    ):
        raise R10ExecutionPublicationUnavailable(
            "R10_EXECUTION_TICKER_FIELDS_NOT_FRESH"
        )
    second = _utc(ref.get("observed_second"))
    source_time = _utc(ref.get("source_time_max"))
    if (
        ref.get("source_session_id") != pin.source_session_id
        or ref.get("instrument_id") != pin.instrument_id
        or ref.get("external_symbol") != symbol
        or ref.get("product_category") != "LINEAR"
        or not timedelta(0) <= now - second
        <= timedelta(seconds=max_field_age_seconds)
        or not timedelta(0) <= now - source_time
        <= timedelta(seconds=max_field_age_seconds)
    ):
        raise R10ExecutionPublicationUnavailable(
            "R10_EXECUTION_TICKER_REF_OR_AGE_INVALID"
        )
    row = connection.execute(
        """SELECT market_source_id,producer_release_commit,product_category,
                  external_symbol,source_time_max,compact_features
             FROM analytics.public_market_source_seconds
            WHERE source_session_id=%s AND instrument_id=%s
              AND observed_second=%s""",
        (pin.source_session_id, pin.instrument_id, second),
    ).fetchone()
    if row is None or not isinstance(row, (tuple, list)) or len(row) != 6:
        raise R10ExecutionPublicationUnavailable(
            "R10_EXECUTION_PHYSICAL_TICKER_MISSING"
        )
    source, commit, product, original_symbol, original_at, features = row
    if (
        source != BYBIT_PUBLIC_MARKET_SOURCE_ID
        or commit != pin.producer_release_commit
        or product != "LINEAR"
        or original_symbol != symbol
        or _utc(original_at) != source_time
        or not isinstance(features, dict)
        or not isinstance(features.get("ticker"), dict)
        or not isinstance(features.get("ticker_field_times"), dict)
    ):
        raise R10ExecutionPublicationUnavailable(
            "R10_EXECUTION_TICKER_ORIGINAL_IDENTITY_MISMATCH"
        )
    original = features["ticker"]
    times = features["ticker_field_times"]
    field_times = tuple(_utc(times.get(name)) for name in _REQUIRED_TICKER_FIELDS)
    if any(
        not timedelta(0) <= now - stamped
        <= timedelta(seconds=max_field_age_seconds)
        or stamped > pin.as_of_second
        for stamped in field_times
    ):
        raise R10ExecutionPublicationUnavailable(
            "R10_EXECUTION_TICKER_FIELD_TTL_EXPIRED"
        )
    original_last, bid, ask = tuple(
        _positive(original.get(name)) for name in _REQUIRED_TICKER_FIELDS
    )
    if original_last != last or bid > ask:
        raise R10ExecutionPublicationUnavailable(
            "R10_EXECUTION_TICKER_CROSSED_OR_UNVERIFIED"
        )
    return R10ExecutionQuote(
        symbol=symbol,
        last_price=last,
        bid_price=bid,
        ask_price=ask,
        publication_id=pin.publication_id,
        source_session_id=pin.source_session_id,
        producer_release_commit=pin.producer_release_commit,
        as_of_second=pin.as_of_second,
        field_times=(
            field_times[0], field_times[1], field_times[2],
        ),
    )


def read_r10_exact_r1_causal_snapshot(
    connection: Any,
    *,
    symbol: str,
    observed_at: datetime,
    history_limit: int,
    max_ticker_age_seconds: int = 2,
) -> R10ExecutionCausalSnapshot:
    """Pin one R1 validity L5-3 5m ATR seed and fresh price to one publication."""
    if type(history_limit) is not int or not 205 <= history_limit <= 240:
        raise R10ExecutionPublicationUnavailable(
            "R10_R1_EXACT_HISTORY_LIMIT_INVALID"
        )
    quote = read_r10_execution_quote(
        connection,
        symbol=symbol,
        observed_at=observed_at,
        max_field_age_seconds=max_ticker_age_seconds,
    )
    try:
        candles, pin = read_exit_l53_candles(
            connection, symbol=symbol, observed_at=observed_at,
        )
    except ExitPublicationUnavailable as exc:
        raise R10ExecutionPublicationUnavailable(
            "R10_R1_EXACT_CLOSED_5M_HISTORY_UNVERIFIED"
        ) from exc
    if (
        pin.publication_id != quote.publication_id
        or pin.source_session_id != quote.source_session_id
        or pin.producer_release_commit != quote.producer_release_commit
        or len(candles) != 240
        or not isinstance(pin.closed_refs, dict)
        or not isinstance(pin.closed_refs.get("5"), dict)
        or _utc(pin.closed_refs["5"].get("closed_at"))
        != candles[-1].closed_at
        or any(not bar.is_closed or bar.timeframe != "5" for bar in candles)
    ):
        raise R10ExecutionPublicationUnavailable(
            "R10_R1_SNAPSHOT_SPLIT_OR_UNCLOSED"
        )
    return R10ExecutionCausalSnapshot(
        quote=quote,
        closed_5m=candles[-history_limit:],
        history_limit=history_limit,
        publication_id=quote.publication_id,
        source_session_id=quote.source_session_id,
    )
