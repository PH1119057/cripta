"""R08: READ ONLY Universal Exit facts from the one native MarketPublication.

Each caller owns a separate REPEATABLE READ READ ONLY PostgreSQL transaction.
Publication pointers are NOT proof of the 240 contiguous CLOSED 5m candles
or of a fresh ticker field. No REST, synthetic price, Strategy or order write.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from bybit_workbench.domain.models import Candle
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
_CLOSED_SETTLE_GRACE = timedelta(seconds=15)
_L53_REQUIRED_BARS = 240
_TICKER_FIELD = "lastPrice"


class ExitPublicationUnavailable(RuntimeError):
    """A required physical source fact or current epoch could not be proved."""


@dataclass(frozen=True, slots=True)
class ExitPublicationPin:
    publication_id: int
    source_session_id: str
    producer_release_commit: str
    as_of_second: datetime
    instrument_id: str
    symbol: str
    closed_refs: dict[str, Any]
    closed_quality: dict[str, Any]
    compact_refs: dict[str, Any]
    compact_quality: dict[str, Any]

    def evidence_ref(self, kind: str) -> str:
        return (
            f"market-publication:{self.publication_id}:"
            f"{self.source_session_id}:{self.instrument_id}:{kind}"
        )


def _utc(value: object, reason: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ExitPublicationUnavailable(reason) from exc
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ExitPublicationUnavailable(reason)
    return value.astimezone(UTC)


def _positive_decimal(value: object, reason: str) -> Decimal:
    try:
        if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
            raise ValueError("not numeric")
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise ValueError("not positive finite")
        return number
    except (InvalidOperation, ValueError) as exc:
        raise ExitPublicationUnavailable(reason) from exc


def _pin(
    connection: Any, *, symbol: str, observed_at: datetime,
) -> ExitPublicationPin:
    now = _utc(observed_at, "R08_CLOCK_NOT_UTC")
    if not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None:
        raise ExitPublicationUnavailable("R08_INSTRUMENT_INVALID")
    if (
        connection.execute("SHOW transaction_isolation").fetchone()
        != ("repeatable read",)
        or connection.execute("SHOW transaction_read_only").fetchone() != ("on",)
    ):
        raise ExitPublicationUnavailable("R08_ATOMIC_READ_ONLY_SNAPSHOT_REQUIRED")
    instrument = bybit_instrument_id("linear", symbol)
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
        raise ExitPublicationUnavailable("R08_ONE_ACTIVE_SOURCE_EPOCH_REQUIRED")
    epoch, commit = sessions[0]
    row = connection.execute(
        """SELECT publication_id,source_session_id,producer_release_commit,
                  as_of_second,publication_status,closed_instrument_refs,
                  closed_instrument_coverage,compact_second_refs,
                  compact_field_coverage
             FROM analytics.market_publications
            WHERE market_source_id=%s AND source_session_id=%s
            ORDER BY as_of_second DESC,publication_id DESC LIMIT 1""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID, epoch),
    ).fetchone()
    if row is None:
        raise ExitPublicationUnavailable("R08_PUBLICATION_MISSING")
    (
        pid, pub_epoch, pub_commit, as_of, status,
        closed, c_quality, compact, t_quality,
    ) = row
    published = _utc(as_of, "R08_PUBLICATION_CLOCK_INVALID")
    if (
        type(pid) is not int or pid < 1
        or pub_epoch != epoch or pub_commit != commit
        or status != "WATERMARKS_ONLY"
        or published.microsecond
        or not timedelta(0) <= now - published <= _PUBLICATION_MAX_AGE
        or any(not isinstance(obj, dict) for obj in (
            closed, c_quality, compact, t_quality,
        ))
    ):
        raise ExitPublicationUnavailable("R08_PUBLICATION_IDENTITY_OR_AGE_INVALID")
    return ExitPublicationPin(
        publication_id=pid,
        source_session_id=epoch,
        producer_release_commit=commit,
        as_of_second=published,
        instrument_id=instrument,
        symbol=symbol,
        closed_refs=closed.get(instrument, {}),
        closed_quality=c_quality.get(instrument, {}),
        compact_refs=compact.get(instrument, {}),
        compact_quality=t_quality.get(instrument, {}),
    )


def read_exit_l53_candles(
    connection: Any, *, symbol: str, observed_at: datetime,
) -> tuple[tuple[Candle, ...], ExitPublicationPin]:
    """Require exact R08 240-bar/ATR200 seed ending at the pinned close."""
    pin = _pin(connection, symbol=symbol, observed_at=observed_at)
    ref = pin.closed_refs.get("5") if isinstance(pin.closed_refs, dict) else None
    quality = (
        pin.closed_quality.get("5")
        if isinstance(pin.closed_quality, dict) else None
    )
    if not isinstance(ref, dict) or not isinstance(quality, dict):
        raise ExitPublicationUnavailable("R08_CLOSED_5M_MANIFEST_MISSING")
    freshness = quality.get("freshness_seconds")
    if (
        ref.get("market_source_id") != BYBIT_PUBLIC_MARKET_SOURCE_ID
        or ref.get("instrument_id") != pin.instrument_id
        or ref.get("external_symbol") != symbol
        or ref.get("product_category") != "LINEAR"
        or ref.get("timeframe") != "5"
        or quality.get("required") is not True
        or quality.get("status") != "LAST_CLOSED_OBSERVED_WINDOW_UNVERIFIED"
        or quality.get("history_window") != "NOT_VERIFIED"
        or type(quality.get("history_required_bars")) is not int
        or quality["history_required_bars"] < _L53_REQUIRED_BARS
        or type(freshness) is not int or not 1 <= freshness <= 3600
    ):
        raise ExitPublicationUnavailable("R08_CLOSED_5M_REF_UNVERIFIED")
    close_at = _utc(ref.get("closed_at"), "R08_CLOSED_5M_TIME_INVALID")
    expected = datetime.fromtimestamp(
        int((pin.as_of_second - _CLOSED_SETTLE_GRACE).timestamp()) // 300 * 300,
        tz=UTC,
    )
    if (
        close_at != expected
        or not timedelta(0) <= pin.as_of_second - close_at
        <= timedelta(seconds=freshness)
    ):
        raise ExitPublicationUnavailable("R08_CLOSED_5M_BOUNDARY_OR_TTL_INVALID")
    key = MarketCandleKey(
        BYBIT_PUBLIC_MARKET_SOURCE_ID, "venue-bybit",
        pin.instrument_id, symbol, "LINEAR", "5",
    )
    # SharedClosedCandleStore.history() performs SELECT ONLY and rejects
    # missing/duplicate/unclosed bars, mismatched instrument and wrong end.
    # A manifest's WATERMARKS_ONLY pointer never upgrades history to READY.
    try:
        candles = SharedClosedCandleStore(connection, key).history(
            through=close_at, required=_L53_REQUIRED_BARS,
        )
    except (RuntimeError, ValueError) as exc:
        raise ExitPublicationUnavailable(
            "R08_CLOSED_240_BAR_HISTORY_UNVERIFIED"
        ) from exc
    return candles, pin


def read_exit_ticker_last_price(
    connection: Any, *, symbol: str, observed_at: datetime,
) -> tuple[Decimal, ExitPublicationPin]:
    """Require original compact linear ticker lastPrice at the pinned epoch."""
    pin = _pin(connection, symbol=symbol, observed_at=observed_at)
    ref = pin.compact_refs.get("TICKER") if isinstance(pin.compact_refs, dict) else None
    quality = (
        pin.compact_quality.get("TICKER")
        if isinstance(pin.compact_quality, dict) else None
    )
    if not isinstance(ref, dict) or not isinstance(quality, dict):
        raise ExitPublicationUnavailable("R08_TICKER_MANIFEST_MISSING")
    freshness = quality.get("freshness_seconds")
    if (
        ref.get("source_session_id") != pin.source_session_id
        or ref.get("instrument_id") != pin.instrument_id
        or ref.get("external_symbol") != symbol
        or ref.get("product_category") != "LINEAR"
        or quality.get("required") is not True
        or quality.get("status") != "OBSERVED"
        or not isinstance(quality.get("fields"), dict)
        or quality["fields"].get(_TICKER_FIELD) != "FRESH"
        or type(freshness) is not int or not 1 <= freshness <= 3600
    ):
        raise ExitPublicationUnavailable("R08_TICKER_REF_UNVERIFIED")
    second = _utc(ref.get("observed_second"), "R08_TICKER_SECOND_INVALID")
    if (
        second >= pin.as_of_second
        or not timedelta(0) <= pin.as_of_second - second
        <= timedelta(seconds=freshness)
        or _utc(ref.get("source_time_max"), "R08_TICKER_SOURCE_TIME_INVALID")
        > pin.as_of_second
    ):
        raise ExitPublicationUnavailable("R08_TICKER_TIME_OR_TTL_INVALID")
    row = connection.execute(
        """SELECT market_source_id,producer_release_commit,product_category,
                  external_symbol,source_time_max,compact_features
             FROM analytics.public_market_source_seconds
            WHERE source_session_id=%s AND instrument_id=%s
              AND observed_second=%s""",
        (pin.source_session_id, pin.instrument_id, second),
    ).fetchone()
    if row is None:
        raise ExitPublicationUnavailable("R08_TICKER_PHYSICAL_ROW_MISSING")
    source, commit, product, original_symbol, source_time, features = row
    if (
        source != BYBIT_PUBLIC_MARKET_SOURCE_ID
        or commit != pin.producer_release_commit
        or product != "LINEAR" or original_symbol != symbol
        or _utc(source_time, "R08_TICKER_ORIGINAL_TIME_INVALID")
        != _utc(ref["source_time_max"], "R08_TICKER_REF_TIME_INVALID")
        or not isinstance(features, dict)
        or not isinstance(features.get("ticker"), dict)
        or not isinstance(features.get("ticker_field_times"), dict)
    ):
        raise ExitPublicationUnavailable("R08_TICKER_PHYSICAL_IDENTITY_MISMATCH")
    field_at = _utc(
        features["ticker_field_times"].get(_TICKER_FIELD),
        "R08_TICKER_FIELD_TIME_MISSING",
    )
    if (
        field_at > pin.as_of_second
        or pin.as_of_second - field_at > timedelta(seconds=freshness)
    ):
        raise ExitPublicationUnavailable("R08_TICKER_FIELD_STALE")
    price = _positive_decimal(
        features["ticker"].get(_TICKER_FIELD),
        "R08_TICKER_LAST_PRICE_INVALID",
    )
    return price, pin
