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
