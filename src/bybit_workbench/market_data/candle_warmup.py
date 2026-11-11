"""On-demand closed-candle warmup from an explicitly selected MarketSource.

A Strategy asks for its exact full immutable plan horizon. This adapter first
reads the one common store, then fetches missing history from the approved
provider and validates it before any Strategy sees a new candle. No fake bars
or native-trade cursor, no per-Strategy storage or Exchange access.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.domain.models import Candle

from .shared_closed_candles import (
    CandleSourceConflict,
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



# Immutable owner set from approved R01/R03 scope. This is NOT a general
# MarketPublication consumer replacement (that is roadmap R06).
R03_NATIVE_CLOSED_SYMBOLS = frozenset((
    "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
))
_R03_NATIVE_CLOSED_INTERVALS = frozenset(("1", "5", "15"))
_R03_SETTLE_GRACE = timedelta(seconds=15)


def load_verified_r03_native_closed_seed(
    connection: Any, *, symbol: str, required_counts: Mapping[str, int],
    observed_at: datetime,
) -> dict[str, tuple[Candle, ...]]:
    """Read exact registered, committed R03 OHLCV; no REST and no INSERT.

    One current source identity, closed-only UTC boundary (15-second
    provider grace), exact full historical plan window and gap-free rows
    are mandatory. Missing/stale native data is a failure, never a reason
    to silently become an independent second candle writer.
    """
    if (
        symbol not in R03_NATIVE_CLOSED_SYMBOLS
        or not required_counts
        or any(tf not in _R03_NATIVE_CLOSED_INTERVALS for tf in required_counts)
        or any(not 1 <= count <= 10000 for count in required_counts.values())
    ):
        raise ValueError("R03_NATIVE_CLOSED_SEED_SCOPE_INVALID")
    if observed_at.utcoffset() != timedelta(0):
        raise ValueError("R03_NATIVE_CLOSED_SEED_CLOCK_NOT_UTC")
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
    source_id, venue_id, instrument_id, exact_symbol, product = matches[0]
    if exact_symbol != symbol or product != "LINEAR":
        raise RuntimeError("R03_NATIVE_CLOSED_SEED_IDENTITY_UNVERIFIED")
    settled_at = observed_at - _R03_SETTLE_GRACE
    result: dict[str, tuple[Candle, ...]] = {}
    for tf, required in required_counts.items():
        step = int(tf) * 60
        end_seconds = int(settled_at.timestamp()) // step * step
        through = datetime.fromtimestamp(end_seconds, tz=UTC)
        key = MarketCandleKey(
            source_id, venue_id, instrument_id, exact_symbol, product, tf,
        )
        # history() itself proves exact closed UTC bars, full length,
        # adjacency and the requested end. No fallback or normalization.
        result[tf] = SharedClosedCandleStore(connection, key).history(
            through=through, required=required,
        )
    return result

def cache_approved_bybit_history(
    connection: Any, *, symbol: str, history: dict[str, tuple[Candle, ...]],
    observed_at: datetime, producer_release_commit: str,
    source_transport: str = "BYBIT_REST_KLINE",
    verify_only: bool = False,
    source_wait_seconds: float = 0.0,
) -> dict[str, tuple[Candle, ...]]:
    """One PostgreSQL source across consumers; only the Bybit REST adapter.

    The caller must have fetched the candles from its approved, authenticated
    `BYBIT_PUBLIC_REST` endpoint. This function does NOT infer source identity
    from Strategy ID, account balance, a coin name or a fabricated market source.
    It resolves the one active registered Bybit MarketSource/Instrument pair.
    """
    # R03 transfers precisely these native LINEAR 1/5/15 candle keys.
    # All other symbols and intervals retain their existing writer until the
    # separately accepted roadmap scope; the service-wide env flag alone
    # cannot silently revoke unrelated Observer source ownership.
    r03_reader_enabled = (
        os.environ.get("CRIPTA_R03_NATIVE_CANDLE_READER") == "ENABLED"
    )
    r03_symbols = frozenset((
        "APTUSDT", "ARBUSDT", "DOTUSDT", "INJUSDT", "LTCUSDT",
    ))
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
        # Protected OPEN-restart code can remain byte-for-byte SHA-pinned:
        # the common adapter owns the exact consumer writer/reader decision.
        r03_native_owned = (
            r03_reader_enabled
            and symbol in r03_symbols
            and tf in ("1", "5", "15")
        )
        read_only = verify_only or r03_native_owned
        read_wait = max(
            source_wait_seconds, 5.0 if r03_native_owned else 0.0,
        )
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
        if read_only:
            # R03 handoff: the Observer can still receive public REST/WS
            # candles for its existing Strategy transport, but must NEVER
            # be a second writer in the canonical MarketSource table.
            # Wait a small bounded interval for the separately committing
            # native source. Never synthesize, overwrite or accept a conflict.
            if not 0.0 <= read_wait <= 120.0:
                raise ValueError("CANDLE_SOURCE_READER_WAIT_INVALID")
            deadline = time.monotonic() + read_wait
            while True:
                try:
                    canonical = store.history(
                        through=full[-1].closed_at, required=len(full),
                    )
                    break
                except RuntimeError as exc:
                    if (
                        str(exc) not in (
                            "CANDLE_WARMUP_INCOMPLETE",
                            "CANDLE_HISTORY_GAP_OR_DUPLICATE",
                            "CANDLE_WARMUP_END_NOT_PROVEN",
                        )
                        or time.monotonic() >= deadline
                    ):
                        raise
                    time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
            if canonical != full:
                raise CandleSourceConflict(key, full[-1].closed_at)
            result[tf] = canonical
        else:
            store.ingest(
                full, producer_release_commit=producer_release_commit,
                transport=source_transport, observed_at=observed_at,
            )
            result[tf] = store.history(
                through=full[-1].closed_at, required=len(full),
            )
    return result
