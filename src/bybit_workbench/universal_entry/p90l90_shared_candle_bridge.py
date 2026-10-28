"""P90 PAPER read bridge: old frozen evidence, new single shared MarketSource store.

Old 17,995 seed bars are immutable forensic evidence. No additional P90
closed-bar rows are ever written by this adapter. New closed candles must
already be committed in the one canonical market-data cache by Observer.
A timestamp crossing the saved original seed must use provider-verified
common OHLC, not a fabricated/renamed original source commit.

This does NOT authorize skipping a native publicTrade cursor or advancing
a saved checkpoint across a missing period. A separate epoch continuity
gate must occur before activation.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data.shared_closed_candles import (
    MarketCandleKey,
    SharedClosedCandleStore,
)

from .p90l90_causal import P90L90EntryState, causal_entry_state
from .p90l90_causal_fact_codec import restore_p90l90_fact
from .p90l90_causal_transport_join import P90L90CausalTransportJoin, P90TransportEvent
from .p90l90_immutable_bar_ledger import P90L90ImmutableBarLedger

_SYMBOLS = frozenset({"LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT"})


class P90SharedCandleLedger:
    """Shared-cache consumer with as-of fallback to original immutable seed.

    This adapter cannot take a private per-Strategy candle write. All new
    facts require exact equality with pre-persisted market-data source.
    """

    def __init__(
        self, connection: Any, *, source_commit: str,
        producer_release_commit: str | None = None,
        accepted_source_commits: frozenset[str] | None = None,
    ) -> None:
        self._db = connection
        self._legacy = P90L90ImmutableBarLedger(
            connection, source_commit=source_commit,
            producer_release_commit=producer_release_commit,
            accepted_source_commits=accepted_source_commits,
        )
        self._seed: dict[tuple[str, str], datetime] = {}
        # MarketSource may have *future* preloaded closed bars. Only bars
        # actually emitted by the causal transport are visible to geometry.
        # On rollback the entire poisoned consumer epoch is discarded.
        self._emitted: dict[tuple[str, str], datetime] = {}
        self._key: dict[tuple[str, str], MarketCandleKey] = {}
        for symbol in sorted(_SYMBOLS):
            row = connection.execute(
                """SELECT checkpoint_json,source_commit
                     FROM strategy_entry.p90l90_causal_transport_checkpoints
                    WHERE symbol=%s""",
                (symbol,),
            ).fetchone()
            if row is None or row[1] != source_commit:
                raise RuntimeError("P90_SHARED_ORIGINAL_CHECKPOINT_MISSING")
            join = P90L90CausalTransportJoin.restore(row[0])
            if join.pending:
                raise RuntimeError("P90_SHARED_PENDING_NATIVE_QUEUE_NOT_EMPTY")
            for tf, boundary in (
                ("5", join.closed_5m_at), ("15", join.closed_15m_at),
            ):
                frozen_boundary = self._legacy.latest_closed_at(
                    symbol=symbol, timeframe=tf,
                )
                if boundary < frozen_boundary:
                    raise RuntimeError("P90_SHARED_CHECKPOINT_BEFORE_FROZEN_SEED")
                # The immutable original archive never advances after cutover.
                # The durable checkpoint may advance in a later verified epoch.
                self._seed[symbol, tf] = frozen_boundary
                self._emitted[symbol, tf] = boundary
                sources = connection.execute(
                    """SELECT s.market_source_id,s.venue_id,i.instrument_id,
                              i.external_symbol,i.product_category
                         FROM identity.market_sources s
                         JOIN identity.instruments i
                           ON i.venue_id=s.venue_id
                        WHERE s.venue_id='venue-bybit'
                          AND s.source_kind='DIRECT_PUBLIC_COMPOSITE'
                          AND s.status='ACTIVE' AND i.external_symbol=%s
                          AND i.product_category='LINEAR'""",
                    (symbol,),
                ).fetchall()
                if len(sources) != 1:
                    raise RuntimeError("P90_SHARED_SOURCE_IDENTITY_AMBIGUOUS")
                source, venue, instrument, external, product = sources[0]
                self._key[symbol, tf] = MarketCandleKey(
                    source, venue, instrument, external, product, tf,
                )

    def _canonical(
        self, symbol: str, tf: str, *,
        through: datetime, count: int,
    ) -> tuple[Candle, ...]:
        key = self._key.get((symbol, tf))
        if key is None:
            raise ValueError("P90_SHARED_UNRECOGNIZED_SYMBOL_TIMEFRAME")
        return SharedClosedCandleStore(self._db, key).history(
            through=through, required=count,
        )

    def append(self, event: P90TransportEvent) -> bool:
        """Read/compare only: market-data producer already wrote the OHLC."""
        fact, cursor = restore_p90l90_fact(event)
        if event.kind != "CANDLE_CLOSED" or cursor is not None:
            raise ValueError("P90_SHARED_EXPECTED_CLOSED_BAR")
        tf = event.timeframe
        if tf not in {"5", "15"}:
            raise ValueError("P90_SHARED_UNRECOGNIZED_TIMEFRAME")
        if event.event_at <= self._seed.get((event.symbol, tf), datetime.min.replace(tzinfo=UTC)):
            raise RuntimeError("P90_SHARED_CANNOT_REWRITE_ORIGINAL_SEED")
        attrs = fact.attributes.to_dict()
        from decimal import Decimal

        incoming = Candle(
            symbol=event.symbol, timeframe=tf,
            opened_at=datetime.fromisoformat(str(attrs["opened_at"])),
            closed_at=event.event_at,
            open=Decimal(str(attrs["open"])),
            high=Decimal(str(attrs["high"])),
            low=Decimal(str(attrs["low"])),
            close=Decimal(str(attrs["close"])),
            volume=Decimal(str(attrs["volume"])),
        )
        actual = self._canonical(
            event.symbol, tf, through=event.event_at, count=1,
        )[0]
        if actual != incoming:
            raise RuntimeError("P90_SHARED_CANDLE_NOT_EQUAL_TO_MARKET_SOURCE")
        key = event.symbol, tf
        previous = self._emitted.get(key)
        step = timedelta(minutes=int(tf))
        if previous is None or event.event_at != previous + step:
            raise RuntimeError("P90_SHARED_CAUSAL_EMISSION_GAP")
        self._emitted[key] = event.event_at
        return False

    def closed_at_or_before(
        self, *, symbol: str, timeframe: str, through: datetime,
    ) -> datetime:
        cutoff = self._seed.get((symbol, timeframe))
        if cutoff is None:
            raise ValueError("P90_SHARED_UNRECOGNIZED_SYMBOL_TIMEFRAME")
        if through < cutoff:
            return self._legacy.closed_at_or_before(
                symbol=symbol, timeframe=timeframe, through=through,
            )
        visible = self._emitted.get((symbol, timeframe), cutoff)
        if through >= visible:
            return visible
        if through == cutoff:
            return cutoff
        # This as-of query cannot observe provider-preloaded future bars.
        return self._canonical(symbol, timeframe, through=through, count=1)[0].closed_at

    def history(
        self, *, symbol: str, timeframe: str,
        through: datetime, limit: int,
    ) -> tuple[Candle, ...]:
        cutoff = self._seed.get((symbol, timeframe))
        if cutoff is None:
            raise ValueError("P90_SHARED_UNRECOGNIZED_SYMBOL_TIMEFRAME")
        if through <= cutoff:
            return self._legacy.history(
                symbol=symbol, timeframe=timeframe,
                through=through, limit=limit,
            )
        # A live path cannot reinterpret old frozen records. The independent
        # provider cache must cover full requested history, even across gap.
        if limit > 2800 or limit < 1:
            raise ValueError("P90_SHARED_UNBOUNDED_HISTORY")
        return self._canonical(
            symbol, timeframe, through=through, count=limit,
        )

    def recompute_geometry(
        self, *, symbol: str, effective_at: datetime,
    ) -> P90L90EntryState | None:
        cutoff = self._seed.get((symbol, "5"))
        if cutoff is None:
            raise ValueError("P90_SHARED_UNRECOGNIZED_SYMBOL")
        if effective_at <= cutoff:
            return self._legacy.recompute_geometry(
                symbol=symbol, effective_at=effective_at,
            )
        fifteen = effective_at.replace(
            minute=(effective_at.minute // 15) * 15,
            second=0, microsecond=0,
        )
        # Preserve original as-of window lengths/ATR semantics. Never silently
        # shorten ATR or PREV7D data merely to pass startup after a large gap.
        fives = self.history(
            symbol=symbol, timeframe="5", through=effective_at, limit=2600,
        )
        fifteens = self.history(
            symbol=symbol, timeframe="15", through=fifteen, limit=999,
        )
        return causal_entry_state(
            fives, fifteens, effective_at=effective_at,
        )


    def closed_gap_plan(
        self, *, symbol: str, through_5m: datetime,
        through_15m: datetime,
    ) -> tuple[tuple[str, Candle], ...]:
        """Read-only exact gap proof. No missing native trade is synthesized.

        The common candle store must ALREADY have source-verified consecutive
        closed bars from the frozen checkpoint through both target boundaries.
        A fresh epoch/cursor is a separate safety gate after this OHLC proof.
        """
        base5 = self._seed.get((symbol, "5"))
        base15 = self._seed.get((symbol, "15"))
        if base5 is None or base15 is None:
            raise ValueError("P90_SHARED_UNRECOGNIZED_SYMBOL")
        changes: list[tuple[str, Candle]] = []
        for tf, start, end in (
            ("5", base5, through_5m), ("15", base15, through_15m),
        ):
            interval = timedelta(minutes=int(tf))
            if (
                end.utcoffset() != timedelta(0)
                or end < start
                or (end - start) % interval
            ):
                raise RuntimeError("P90_SHARED_GAP_BOUNDARIES_NOT_ALIGNED")
            missing = (end - start) // interval
            if missing > 10000:
                raise RuntimeError("P90_SHARED_GAP_REQUIRES_BOUNDED_PAGINATION")
            if missing == 0:
                continue
            bars = self._canonical(
                symbol, tf, through=end, count=int(missing),
            )
            if (
                len(bars) != missing
                or bars[0].closed_at != start + interval
                or bars[-1].closed_at != end
            ):
                raise RuntimeError("P90_SHARED_GAP_COVERAGE_NOT_PROVEN")
            changes.extend((tf, bar) for bar in bars)
        return tuple(
            sorted(changes, key=lambda item: (
                item[1].closed_at,
                0 if item[0] == "5" else 1,
            ))
        )
