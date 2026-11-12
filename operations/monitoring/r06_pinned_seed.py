"""Read exact confirmed OHLCV refs of an immutable R06 publication."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from bybit_workbench.market_data.entry_publication_reader import EntryPublicationPin
from bybit_workbench.market_data.shared_closed_candles import (
    MarketCandleKey,
    SharedClosedCandleStore,
)
from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID, bybit_instrument_id


class R06PinnedSeedBlocked(RuntimeError):
    """Never substitute wall-clock grace or a later candle for the pinned ref."""


def read_pinned_closed_seed(connection: Any, *, pin: EntryPublicationPin, symbol: str,
                            required_counts: dict[str, int]) -> dict[str, tuple[Any, ...]]:
    if (not isinstance(pin, EntryPublicationPin) or symbol not in pin.closed_refs
            or not required_counts or not set(required_counts) <= {"5", "15"}
            or not set(required_counts) <= set(pin.closed_refs[symbol])
            or any(type(n) is not int or not 1 <= n <= 10000 for n in required_counts.values())):
        raise R06PinnedSeedBlocked("R06_PINNED_SEED_SCOPE_INVALID")
    rows = connection.execute(
        "SELECT s.market_source_id,s.venue_id,i.instrument_id,i.external_symbol,i.product_category "
        "FROM identity.market_sources s JOIN identity.instruments i ON i.venue_id=s.venue_id "
        "WHERE s.venue_id='venue-bybit' AND s.source_kind='DIRECT_PUBLIC_COMPOSITE' "
        "AND s.status='ACTIVE' AND i.external_symbol=%s AND i.product_category='LINEAR'",
        (symbol,),
    ).fetchall()
    expected = (BYBIT_PUBLIC_MARKET_SOURCE_ID, "venue-bybit",
                bybit_instrument_id("linear", symbol), symbol, "LINEAR")
    if rows != [expected]:
        raise R06PinnedSeedBlocked("R06_PINNED_SEED_IDENTITY_UNVERIFIED")
    result = {}
    for tf, count in required_counts.items():
        through = datetime.fromisoformat(pin.closed_refs[symbol][tf])
        if (through.utcoffset() != timedelta(0) or through > pin.as_of_second
                or through.second or through.microsecond
                or int(through.timestamp()) % (int(tf)*60)):
            raise R06PinnedSeedBlocked("R06_PINNED_SEED_BOUNDARY_INVALID")
        key = MarketCandleKey(*expected, tf)
        # Publication already proves this exact provider-confirmed close.
        # R03's wall-clock-minus-15s preparation grace is not this boundary.
        # The common store still proves full length, UTC, adjacency and end.
        result[tf] = SharedClosedCandleStore(connection, key).history(
            through=through, required=count,
        )
    return result


def scope_geometry_ready(pin: Any, symbols: tuple[str, ...]) -> bool:
    """Pause only the scope whose next published native prefix needs a close."""
    for symbol in symbols:
        event = datetime.fromisoformat(pin.native_watermarks[symbol]["event_at"])
        if event.utcoffset() != timedelta(0) or event >= pin.as_of_second+timedelta(seconds=1):
            raise R06PinnedSeedBlocked("R06_PINNED_NATIVE_TIME_INVALID")
        for tf in ("5", "15"):
            step = int(tf)*60
            required = int(event.timestamp())//step*step
            close = datetime.fromisoformat(pin.closed_refs[symbol][tf])
            if close.timestamp() < required:
                return False
    return True
