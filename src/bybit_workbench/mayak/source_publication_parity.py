"""R11 MAYAK feature parity *candidate*, not an event-replay/consumer cutover.

The canonical R01-R05 public MarketSource has EXACT FIVE symbols; MAYAK has
SEVEN (BTC and ETH are reference anchors). These facts cannot be merged by
pretending compact one-second trade sums, book top/bands or liquidations are
the complete original per-event MAYAK inputs. This read-only adapter proves
three important typed features from ONE immutable PostgreSQL publication;
full seven-symbol source, original trade/BOOK-delta/liquidation replay,
account-ratio/instrument-info and live feature parity remain explicit gates.
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

R11_MAYAK_PANEL = (
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    "BTCUSDT", "ETHUSDT",
)
_R01_R05_CAPTURE = frozenset(R11_MAYAK_PANEL[:5])
_EPOCH = re.compile(r"^[0-9a-f]{32}$")
_RELEASE = re.compile(r"^[0-9a-f]{40}$")
_FIELD_TTL = timedelta(seconds=5)
_PUBLICATION_TTL = timedelta(seconds=30)
_TICKER = (
    "lastPrice", "markPrice", "indexPrice", "openInterest",
    "openInterestValue", "fundingRate",
)
_BOOK = (
    "best_bid", "best_ask", "bid_size", "ask_size",
    "bid_5bps_usd", "ask_5bps_usd",
    "bid_10bps_usd", "ask_10bps_usd",
    "bid_25bps_usd", "ask_25bps_usd",
    "bid_50bps_usd", "ask_50bps_usd",
)


class R11MayakPublicationUnavailable(RuntimeError):
    """A feature row is not proven, or a required event-level feed is absent."""


@dataclass(frozen=True, slots=True)
class R11MayakFeatureCandidate:
    symbol: str
    publication_id: int
    source_session_id: str
    producer_release_commit: str
    as_of_second: datetime
    spot_book_bands: dict[str, Decimal]
    linear_book_bands: dict[str, Decimal]
    linear_ticker: dict[str, Decimal]
    # These are deliberate stop flags, not cosmetic dashboard metadata.
    seven_symbol_panel_verified: bool = False
    original_spot_and_linear_trade_replay_verified: bool = False
    book_delta_replay_verified: bool = False
    liquidation_event_replay_verified: bool = False
    optional_rest_feature_parity_verified: bool = False

    @property
    def ready_to_replace_mayak_websockets(self) -> bool:
        return all((
            self.seven_symbol_panel_verified,
            self.original_spot_and_linear_trade_replay_verified,
            self.book_delta_replay_verified,
            self.liquidation_event_replay_verified,
            self.optional_rest_feature_parity_verified,
        ))


def _need(condition: bool, reason: str) -> None:
    if not condition:
        raise R11MayakPublicationUnavailable("R11_" + reason)


def _utc(value: object, reason: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise R11MayakPublicationUnavailable("R11_" + reason) from exc
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise R11MayakPublicationUnavailable("R11_" + reason)
    return value.astimezone(UTC)


def _numeric(value: object, *, signed: bool = False) -> Decimal:
    try:
        if not isinstance(value, (str, Decimal, int)) or isinstance(value, bool):
            raise ValueError("not numeric")
        amount = Decimal(str(value))
        if not amount.is_finite() or (amount < 0 and not signed):
            raise ValueError("nonfinite or invalid sign")
        return amount
    except (ValueError, InvalidOperation) as exc:
        raise R11MayakPublicationUnavailable("R11_ORIGINAL_VALUE_INVALID") from exc


def _physical(
    connection: Any,
    *,
    instrument: str,
    symbol: str,
    product: str,
    dataset: str,
    epoch: str,
    commit: str,
    published: datetime,
    refs: dict[str, object],
    quality: dict[str, object],
) -> dict[str, Any]:
    candidates = refs.get(instrument)
    conditions = quality.get(instrument)
    _need(
        isinstance(candidates, dict) and isinstance(conditions, dict),
        "FEATURE_PUBLICATION_IDENTITY_MISSING",
    )
    ref, q = candidates.get(dataset), conditions.get(dataset)
    _need(
        isinstance(ref, dict) and isinstance(q, dict)
        and ref.get("source_session_id") == epoch
        and ref.get("instrument_id") == instrument
        and ref.get("product_category") == product
        and ref.get("external_symbol") == symbol
        and q.get("status") == "OBSERVED",
        "FEATURE_REF_OR_QUALITY_INVALID",
    )
    sec = _utc(ref.get("observed_second"), "FEATURE_SECOND_CLOCK_INVALID")
    source_time = _utc(ref.get("source_time_max"), "FEATURE_EVENT_CLOCK_INVALID")
    _need(
        sec < published and source_time <= published
        and timedelta(0) <= published - sec <= _FIELD_TTL,
        "FEATURE_SECOND_STALE_OR_FUTURE",
    )
    row = connection.execute(
        """SELECT market_source_id,venue_id,producer_release_commit,
                  product_category,external_symbol,source_time_max,
                  compact_features
             FROM analytics.public_market_source_seconds
            WHERE source_session_id=%s AND instrument_id=%s
              AND observed_second=%s""",
        (epoch, instrument, sec),
    ).fetchone()
    _need(isinstance(row, (list, tuple)) and len(row) == 7,
          "PHYSICAL_ROW_MISSING")
    source, venue, released, kind, original_symbol, original_at, features = row
    _need(
        source == BYBIT_PUBLIC_MARKET_SOURCE_ID
        and venue == "venue-bybit" and released == commit
        and kind == product and original_symbol == symbol
        and _utc(original_at, "PHYSICAL_EVENT_CLOCK_INVALID") == source_time
        and isinstance(features, dict),
        "PHYSICAL_IDENTITY_MISMATCH",
    )
    key = "ticker" if dataset == "TICKER" else "book"
    selected = features.get(key)
    _need(isinstance(selected, dict), "PHYSICAL_FEATURE_MISSING")
    if dataset == "TICKER":
        times = features.get("ticker_field_times")
        statuses = q.get("fields")
        _need(
            isinstance(times, dict) and isinstance(statuses, dict)
            and all(statuses.get(name) == "FRESH" for name in _TICKER),
            "TICKER_FIELDS_NOT_FRESH",
        )
        for name in _TICKER:
            tick_time = _utc(
                times.get(name), "TICKER_FIELD_CLOCK_INVALID",
            )
            _need(
                timedelta(0) <= published - tick_time <= _FIELD_TTL,
                "TICKER_FIELD_EXPIRED",
            )
    else:
        book_time = _utc(
            selected.get("source_time"), "BOOK_EVENT_TIME_INVALID",
        )
        _need(
            timedelta(0) <= published - book_time <= _FIELD_TTL
            and type(selected.get("source_sequence")) is int
            and selected["source_sequence"] >= 0,
            "BOOK_EVENT_IDENTITY_OR_AGE_INVALID",
        )
    return selected


def read_r11_mayak_feature_candidate(
    connection: Any,
    *,
    symbol: str,
    observed_at: datetime,
) -> R11MayakFeatureCandidate:
    """READ ONLY current five-symbol projection; full seven-symbol cutover BLOCKED.

    A caller must hold one REPEATABLE READ READ ONLY PostgreSQL transaction.
    Even a valid candidate NEVER feeds LiveMayakEngine.on_trade/on_book, never
    asserts source continuity, and never authorizes removal of public WS.
    """
    now = _utc(observed_at, "NOW_NOT_UTC")
    _need(symbol in _R01_R05_CAPTURE, "SYMBOL_OUTSIDE_FIVE_CAPTURE")
    _need(
        connection.execute("SHOW transaction_isolation").fetchone()
        == ("repeatable read",)
        and connection.execute("SHOW transaction_read_only").fetchone()
        == ("on",),
        "READ_ONLY_ATOMIC_SNAPSHOT_REQUIRED",
    )
    sessions = connection.execute(
        """SELECT source_session_id,producer_release_commit
             FROM analytics.native_public_trade_source_sessions
            WHERE market_source_id=%s AND state='ACTIVE'
            ORDER BY subscribed_at DESC LIMIT 2""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID,),
    ).fetchall()
    _need(
        len(sessions) == 1 and isinstance(sessions[0][0], str)
        and isinstance(sessions[0][1], str)
        and _EPOCH.fullmatch(sessions[0][0]) is not None
        and _RELEASE.fullmatch(sessions[0][1]) is not None,
        "ONE_ACTIVE_SOURCE_REQUIRED",
    )
    epoch, commit = sessions[0]
    row = connection.execute(
        """SELECT publication_id,source_session_id,producer_release_commit,
                  as_of_second,publication_status,compact_second_refs,
                  compact_field_coverage
             FROM analytics.market_publications
            WHERE market_source_id=%s AND source_session_id=%s
            ORDER BY as_of_second DESC,publication_id DESC LIMIT 1""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID, epoch),
    ).fetchone()
    _need(isinstance(row, (list, tuple)) and len(row) == 7,
          "PUBLICATION_MISSING")
    pid, pub_epoch, pub_commit, as_of, status, refs, quality = row
    published = _utc(as_of, "PUBLICATION_CLOCK_INVALID")
    _need(
        type(pid) is int and pid > 0
        and pub_epoch == epoch and pub_commit == commit
        and status == "WATERMARKS_ONLY"
        and published.microsecond == 0
        and timedelta(0) <= now - published <= _PUBLICATION_TTL
        and isinstance(refs, dict) and isinstance(quality, dict),
        "PUBLICATION_WRONG_EPOCH_OR_STALE",
    )
    raw: dict[tuple[str, str], dict[str, Any]] = {}
    for product, dataset in (
        ("SPOT", "ORDERBOOK_50"),
        ("LINEAR", "ORDERBOOK_50"),
        ("LINEAR", "TICKER"),
    ):
        raw[product, dataset] = _physical(
            connection,
            instrument=bybit_instrument_id(product.lower(), symbol),
            symbol=symbol, product=product, dataset=dataset,
            epoch=epoch, commit=commit, published=published,
            refs=refs, quality=quality,
        )
    books: dict[str, dict[str, Decimal]] = {}
    for product in ("SPOT", "LINEAR"):
        book = raw[product, "ORDERBOOK_50"]
        state = {name: _numeric(book.get(name)) for name in _BOOK}
        _need(
            state["best_bid"] > 0 and state["best_ask"] > state["best_bid"]
            and state["bid_size"] > 0 and state["ask_size"] > 0,
            "BOOK_UNVERIFIED_OR_CROSSED",
        )
        books[product] = state
    ticker = raw["LINEAR", "TICKER"]
    fields = {
        name: _numeric(ticker.get(name), signed=name == "fundingRate")
        for name in _TICKER
    }
    _need(
        all(fields[name] > 0 for name in (
            "lastPrice", "markPrice", "indexPrice", "openInterest",
            "openInterestValue",
        )),
        "TICKER_REQUIRED_FIELD_INVALID",
    )
    return R11MayakFeatureCandidate(
        symbol=symbol, publication_id=pid, source_session_id=epoch,
        producer_release_commit=commit, as_of_second=published,
        spot_book_bands=books["SPOT"],
        linear_book_bands=books["LINEAR"],
        linear_ticker=fields,
    )
