"""Isolation layer for P90L90 PAPER causal facts vs ordinary Strategy facts.

The ordinary Strategy consumer receives independent transport delivery as-is.
Only the P90 consumer waits for completed 5m AND 15m causal bars. Delivery
to the P90 consumer is within the durable join store's database transaction.
This is a library primitive, NOT connected to live Observer or permission to
activate an unverified Strategy.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .contracts import MarketFactEnvelope
from .p90l90_causal_fact_codec import (
    encode_p90l90_fact,
    restore_p90l90_fact,
)
from .p90l90_causal_transport_join import P90TransportEvent
from .transport_continuity import TradeCursor

Consumer = Callable[[MarketFactEnvelope, TradeCursor | None], None]

_P90_SYMBOLS = frozenset({
    "LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT",
})


class DurableP90Join(Protocol):
    def deliver(
        self,
        event: P90TransportEvent,
        *,
        dispatch: Callable[[tuple[P90TransportEvent, ...]], None],
    ) -> tuple[P90TransportEvent, ...]: ...


class P90L90ObserverCausalRouter:
    """Do not hold back or reorder existing non-P90 Strategy consumers.

    The normal consumer MUST NOT also feed the P90 engine. The P90 consumer
    MUST use only the join store's same PostgreSQL transaction/connection and
    must not send Exchange orders or alter independent Strategy state.
    In-memory side effects are not transactional; any exception requires
    stopping the entire epoch and reconstructing watch state before replay.
    """

    def __init__(
        self,
        store: DurableP90Join,
        *,
        ordinary: Consumer,
        p90_paper: Consumer,
        p90_symbols: frozenset[str],
    ) -> None:
        if not p90_symbols or not p90_symbols.issubset(_P90_SYMBOLS):
            raise ValueError("P90L90 router symbols outside approved Strategy set")
        self._store = store
        self._ordinary = ordinary
        self._p90_paper = p90_paper
        self._symbols = p90_symbols

    def accept(
        self,
        fact: MarketFactEnvelope,
        *,
        cursor: TradeCursor | None = None,
    ) -> int:
        """Return P90 released-event count, not a Strategy execution receipt."""
        self._ordinary(fact, cursor)
        if fact.symbol not in self._symbols or fact.event_kind not in {
            "PUBLIC_TRADE", "CANDLE_CLOSED",
        }:
            return 0
        if fact.event_kind == "CANDLE_CLOSED" and str(
            fact.attributes.to_dict().get("timeframe") or ""
        ) not in {"5", "15"}:
            return 0
        envelope = encode_p90l90_fact(fact, cursor=cursor)

        def deliver_released(batch: tuple[P90TransportEvent, ...]) -> None:
            for item in batch:
                restored, native_cursor = restore_p90l90_fact(item)
                if restored.symbol not in self._symbols:
                    raise RuntimeError("P90L90 restored fact outside active Strategy set")
                self._p90_paper(restored, native_cursor)

        return len(self._store.deliver(envelope, dispatch=deliver_released))
