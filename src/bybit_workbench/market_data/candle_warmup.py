"""On-demand closed-candle warmup from an explicitly selected MarketSource.

A Strategy asks for its exact full immutable plan horizon. This adapter first
reads the one common store, then fetches missing history from the approved
provider and validates it before any Strategy sees a new candle. No fake bars
or native-trade cursor, no per-Strategy storage or Exchange access.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from bybit_workbench.domain.models import Candle

from .shared_closed_candles import (
    MarketCandleKey,
    SharedClosedCandleStore,
    verify_closed_window,
)

FetchClosedHistory = Callable[[MarketCandleKey, int, datetime], Sequence[Candle]]


def ensure_closed_history(
    connection: Any, key: MarketCandleKey, *,
    through: datetime, required: int, source_fetch: FetchClosedHistory,
    source_transport: str, producer_release_commit: str,
    observed_at: datetime,
) -> tuple[Candle, ...]:
    """Cache hit -> zero upstream calls. Gap -> full independently verified slice.

    Provider-specific pagination/source authentication is the fetch callback's
    responsibility. This function verifies the common source key, closed-only
    order, required count and exact end before inserting any gap-filling bars.
    """
    store = SharedClosedCandleStore(connection, key)
    try:
        return store.history(through=through, required=required)
    except RuntimeError as exc:
        if str(exc) not in {
            "CANDLE_WARMUP_INCOMPLETE",
            "CANDLE_HISTORY_GAP_OR_DUPLICATE",
            "CANDLE_WARMUP_END_NOT_PROVEN",
        }:
            raise
    fetched = verify_closed_window(
        key, source_fetch(key, required, through),
        minimum=required, through=through,
    )
    store.ingest(
        fetched, producer_release_commit=producer_release_commit,
        transport=source_transport, observed_at=observed_at,
    )
    return store.history(through=through, required=required)


def cache_approved_bybit_history(
    connection: Any, *, symbol: str, history: dict[str, tuple[Candle, ...]],
    observed_at: datetime, producer_release_commit: str,
) -> dict[str, tuple[Candle, ...]]:
    """One PostgreSQL source across consumers; only the Bybit REST adapter.

    The caller must have fetched the candles from its approved, authenticated
    `BYBIT_PUBLIC_REST` endpoint. This function does NOT infer source identity
    from Strategy ID, account balance, a coin name or a fabricated market source.
    It resolves the one active registered Bybit MarketSource/Instrument pair.
    """
    matches = connection.execute(
        """SELECT s.market_source_id,s.venue_id,i.instrument_id,
                  i.external_symbol,i.product_category
             FROM identity.market_sources s
             JOIN identity.instruments i ON i.venue_id=s.venue_id
            WHERE s.venue_id='venue-bybit'
              AND s.source_kind='DIRECT_PUBLIC_COMPOSITE'
              AND s.status='ACTIVE'
              AND i.external_symbol=%s AND i.product_category='LINEAR'""",
        (symbol,),
    ).fetchall()
    if len(matches) != 1:
        raise RuntimeError("CANDLE_APPROVED_BYBIT_MARKET_SOURCE_AMBIGUOUS")
    result: dict[str, tuple[Candle, ...]] = {}
    for tf, rows in history.items():
        (source_id, venue_id, instrument_id, exact_symbol, product) = matches[0]
        key = MarketCandleKey(
            market_source_id=source_id, venue_id=venue_id,
            instrument_id=instrument_id, external_symbol=exact_symbol,
            product_category=product, timeframe=tf,
        )
        # A provider gap is not silently forward-filled even if another
        # strategy already has a cached subset of this source.
        full = verify_closed_window(key, rows)
        store = SharedClosedCandleStore(connection, key)
        store.ingest(
            full, producer_release_commit=producer_release_commit,
            transport="BYBIT_REST_KLINE", observed_at=observed_at,
        )
        result[tf] = store.history(through=full[-1].closed_at,
                                   required=len(full))
    return result
