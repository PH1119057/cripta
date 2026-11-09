"""Source-owned immutable PostgreSQL native row -> original P90 fact.

This is an envelope adapter, NOT a Strategy signal, admission rule, or
Exchange command. In particular it never substitutes consumer processing
time for the recorded provider time or assigns a synthetic native cursor.
"""
from __future__ import annotations

from datetime import UTC, timedelta

from bybit_workbench.universal_entry import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

from .p90l90_scoped_native_source import (
    NativeSourceProofUnavailable,
    OrderedNativeTrade,
)


def original_native_fact(
    event: OrderedNativeTrade,
) -> tuple[MarketFactEnvelope, TradeCursor]:
    """Recover the same envelope/TradeCursor shape as the legacy WS owner."""
    if (
        not isinstance(event, OrderedNativeTrade)
        or event.event_at.tzinfo is None
        or event.db_accepted_at.tzinfo is None
        or event.event_at.utcoffset() is None
        or event.db_accepted_at.utcoffset() is None
        or event.db_accepted_at.astimezone(UTC)
           < event.event_at.astimezone(UTC) - timedelta(seconds=2)
        or not event.exec_id
        or type(event.exchange_sequence) is not int
        or event.exchange_sequence < 0
    ):
        raise NativeSourceProofUnavailable(
            "P90_SCOPED_ORIGINAL_TRADE_ENVELOPE_INVALID"
        )
    fact = MarketFactEnvelope(
        fact_id="trade-" + event.exec_id,
        event_kind="PUBLIC_TRADE",
        symbol=event.symbol,
        observed_at=event.event_at.astimezone(UTC),
        event_at=event.event_at.astimezone(UTC),
        # Original producer's PG acceptance, NOT a newly invented WS receive.
        received_at=event.db_accepted_at.astimezone(UTC),
        source_refs=(
            f"bybit:publicTrade:{event.symbol}:{event.exec_id}",
        ),
        attributes=FrozenPolicy.from_mapping({
            "price": str(event.price),
            "size": str(event.quantity),
            "taker_side": event.taker_side,
        }),
    )
    cursor = TradeCursor(
        event.symbol, event.exec_id,
        event.exchange_sequence, event.event_at.astimezone(UTC),
    )
    return fact, cursor
