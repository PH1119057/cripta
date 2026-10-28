"""P90L90 standalone no-order causal PAPER event processor.

Combines immutable as-of closed candles, native order join, durable Entry
crossing state and caller-provided same-connection PAPER callbacks. No exchange
actions or StrategyActivation are implemented. The production Observer cannot
use this until the original WS/REST ingress and R1 strategy fan-out are wired
and verified separately.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from .contracts import MarketFactEnvelope
from .p90l90_atomic_paper_checkpoint import P90L90AtomicPaperCheckpointStore
from .p90l90_causal import P90L90CausalEntry, P90L90EntryState
from .p90l90_causal_fact_codec import restore_p90l90_fact
from .p90l90_causal_transport_join import P90L90CausalTransportJoin, P90TransportEvent
from .p90l90_crossing_fact import p90l90_crossing_fact
from .p90l90_immutable_bar_ledger import P90L90ImmutableBarLedger
from .transport_continuity import TradeCursor

NativeExitHandler = Callable[[MarketFactEnvelope, TradeCursor], None]
CandleHandler = Callable[[MarketFactEnvelope], None]
ProvenCandidateHandler = Callable[[MarketFactEnvelope, TradeCursor], None]


class P90L90DurablePaperProcessor:
    """PAPER-only causal interpreter. No global engine state or Bybit orders.

    Same connection and transaction are required for ALL callbacks. After an
    exception this instance is poisoned and must be replaced from DB, because
    in-memory callback side effects cannot be SQL-rolled-back.
    """

    def __init__(
        self,
        connection: Any,
        *,
        source_commit: str,
        producer_release_commit: str | None = None,
        accepted_source_commits: frozenset[str] | None = None,
        on_native_exit: NativeExitHandler,
        on_closed_candle: CandleHandler,
        candidate_entry_plan_fingerprint: str | None = None,
        on_proven_candidate: ProvenCandidateHandler | None = None,
    ) -> None:
        if (candidate_entry_plan_fingerprint is None) != (on_proven_candidate is None):
            raise ValueError("P90L90 candidate requires both exact plan and handler")
        self._atomic = P90L90AtomicPaperCheckpointStore(
            connection, source_commit=source_commit,
            writer_release_commit=producer_release_commit,
        )
        self._bars = P90L90ImmutableBarLedger(
            connection, source_commit=source_commit,
            producer_release_commit=producer_release_commit,
            accepted_source_commits=accepted_source_commits,
        )
        self._on_exit = on_native_exit
        self._on_candle = on_closed_candle
        self._candidate_plan = candidate_entry_plan_fingerprint
        self._on_proven_candidate = on_proven_candidate
        self._checked_geometry: dict[
            tuple[str, datetime], P90L90EntryState | None
        ] = {}
        self._poisoned = False

    def _geometry_for_checkpoint(
        self, symbol: str, entry_snapshot: object
    ) -> P90L90EntryState | None:
        if not isinstance(entry_snapshot, dict):
            raise ValueError("P90L90 Entry checkpoint is not an object")
        raw = entry_snapshot.get("geometry")
        if raw is None:
            return None
        if not isinstance(raw, dict) or not isinstance(raw.get("effective_at"), str):
            raise ValueError("P90L90 Entry checkpoint effective time missing")
        at = datetime.fromisoformat(raw["effective_at"])
        if at.tzinfo is None or at.utcoffset() != timedelta():
            raise ValueError("P90L90 Entry checkpoint effective time not UTC")
        key = symbol, at
        if key not in self._checked_geometry:
            self._checked_geometry[key] = self._bars.recompute_geometry(
                symbol=symbol, effective_at=at,
            )
        return self._checked_geometry[key]

    def seed_from_immutable_history(
        self, *, symbol: str, closed_5m_at: datetime, closed_15m_at: datetime
    ) -> None:
        """Only after operator-proven backfill of the original 7d+ATR candles.

        If the durable source is incomplete do not invent a warmup Geometry,
        trade, or reset any previous queue/Entry state.
        """
        if self._poisoned:
            raise RuntimeError("P90L90 poisoned epoch requires process restart")
        last5 = self._bars.closed_at_or_before(
            symbol=symbol, timeframe="5", through=closed_5m_at,
        )
        last15 = self._bars.closed_at_or_before(
            symbol=symbol, timeframe="15", through=closed_15m_at,
        )
        if last5 != closed_5m_at or last15 != closed_15m_at:
            raise RuntimeError("P90L90 incomplete historical candle seed")
        computed = self._bars.recompute_geometry(
            symbol=symbol, effective_at=closed_5m_at,
        )
        if computed is None:
            raise RuntimeError("P90L90 historical geometry warmup incomplete")
        self._atomic.seed(
            symbol=symbol,
            last_closed_5m=closed_5m_at,
            last_closed_15m=closed_15m_at,
            entry=P90L90CausalEntry(state=computed),
            proven_geometry=computed,
        )

    def recover(
        self, *, symbol: str
    ) -> tuple[P90L90CausalTransportJoin, P90L90CausalEntry]:
        """Verify as-of candles and previous native crossing after restart."""
        if self._poisoned:
            raise RuntimeError("P90L90 poisoned epoch requires process restart")
        return self._atomic.inspect_verified(
            symbol=symbol, recompute_geometry=self._geometry_for_checkpoint,
        )

    def accept(self, event: P90TransportEvent) -> tuple[P90TransportEvent, ...]:
        """One atomic market fact -> native/Entry/PAPER committed or none."""
        if self._poisoned:
            raise RuntimeError("P90L90 poisoned epoch requires process restart")

        def apply(entry: P90L90CausalEntry, batch: tuple[P90TransportEvent, ...]) -> None:
            for released in batch:
                fact, cursor = restore_p90l90_fact(released)
                if released.kind == "CANDLE_CLOSED":
                    self._bars.append(released)
                    # Recompute only from bars closed by THIS causal boundary,
                    # never from later records already present in the archive.
                    last5 = self._bars.closed_at_or_before(
                        symbol=released.symbol, timeframe="5",
                        through=released.event_at,
                    )
                    last15 = self._bars.closed_at_or_before(
                        symbol=released.symbol, timeframe="15",
                        through=released.event_at,
                    )
                    expected15 = last5.replace(
                        minute=(last5.minute // 15) * 15,
                    )
                    if last15 == expected15:
                        computed = self._bars.recompute_geometry(
                            symbol=released.symbol, effective_at=last5,
                        )
                        if computed is not None and (
                            entry.state is None
                            or computed.effective_at > entry.state.effective_at
                        ):
                            entry.update_geometry(computed)
                    self._on_candle(fact)
                    continue
                if cursor is None:
                    raise RuntimeError("P90L90 native paper tick lost exact cursor")
                self._on_exit(fact, cursor)
                price = Decimal(str(fact.attributes.to_dict()["price"]))
                crossings = entry.on_trade(price=price, event_at=released.event_at)
                if crossings:
                    if (
                        entry.state is None or self._candidate_plan is None
                        or self._on_proven_candidate is None
                    ):
                        raise RuntimeError(
                            "P90L90_ATOMIC_PAPER_CANDIDATE_OWNER_NOT_CONNECTED"
                        )
                    for crossing in crossings:
                        candidate = p90l90_crossing_fact(
                            entry_plan_fingerprint=self._candidate_plan,
                            source_fact=fact,
                            crossing=crossing,
                            geometry=entry.state,
                        )
                        self._on_proven_candidate(candidate, cursor)

        try:
            return self._atomic.deliver(
                event,
                recompute_geometry=self._geometry_for_checkpoint,
                apply_paper=apply,
            )
        except Exception:
            self._poisoned = True
            self._checked_geometry.clear()
            raise
