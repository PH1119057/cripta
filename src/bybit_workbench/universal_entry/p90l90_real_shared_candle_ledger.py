"""REAL-only causal reader of the ONE canonical shared MarketSource candles.

An epoch starts from independently verified closed 5m/15m windows. It never
mutates historical P90 PAPER candles or checkpoint tables, never invents a
publicTrade and never treats provider-preloaded future closed bars as emitted.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from bybit_workbench.domain.models import Candle
from bybit_workbench.market_data.shared_closed_candles import (
    MarketCandleKey,
    SharedClosedCandleStore,
)

from .p90l90_causal import P90L90EntryState, causal_entry_state
from .p90l90_causal_fact_codec import restore_p90l90_fact
from .p90l90_causal_transport_join import P90L90CausalTransportJoin, P90TransportEvent

_SYMBOLS = frozenset(("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT"))


class P90RealSharedCandleLedger:
    """Checkpoint-scoped REAL reader; shared OHLC is owned by MarketSource."""

    def __init__(
        self, connection: Any, *,
        epoch_id: str, source_commit: str,
    ) -> None:
        self._db = connection
        self._epoch_id = epoch_id
        self._source_commit = source_commit
        self._emitted: dict[tuple[str, str], datetime] = {}
        self._key: dict[tuple[str, str], MarketCandleKey] = {}
        rows = connection.execute(
            """SELECT symbol,checkpoint_json,source_commit
                 FROM strategy_entry.p90l90_real_causal_checkpoints
                WHERE epoch_id=%s ORDER BY symbol""",
            (epoch_id,),
        ).fetchall()
        if len(rows) != 5 or {r[0] for r in rows} != _SYMBOLS:
            raise RuntimeError("P90_REAL_CANDLE_EXACT_FIVE_EPOCH_CHECKPOINTS")
        source_rows = connection.execute(
            """SELECT s.market_source_id,s.venue_id,i.instrument_id,
                      i.external_symbol,i.product_category
                 FROM identity.market_sources s
                 JOIN identity.instruments i ON i.venue_id=s.venue_id
                WHERE s.market_source_id='ms-bybit-mainnet-public-v1'
                  AND s.source_kind='DIRECT_PUBLIC_COMPOSITE'
                  AND s.status='ACTIVE'
                  AND i.product_category='LINEAR'
                  AND i.external_symbol = ANY(%s)""",
            (sorted(_SYMBOLS),),
        ).fetchall()
        if len(source_rows) != 5 or {r[3] for r in source_rows} != _SYMBOLS:
            raise RuntimeError("P90_REAL_CANDLE_MARKET_SOURCE_NOT_UNIQUE")
        source_by_symbol = {r[3]: r for r in source_rows}
        for symbol, snapshot, commit in rows:
            if commit != source_commit:
                raise RuntimeError("P90_REAL_CANDLE_SOURCE_COMMIT_DRIFT")
            join = P90L90CausalTransportJoin.restore(snapshot)
            if join.symbol != symbol:
                raise RuntimeError("P90_REAL_CANDLE_JOIN_SYMBOL_DRIFT")
            if join.pending:
                raise RuntimeError("P90_REAL_CANDLE_RESTART_PENDING_NATIVE_GAP")
            source, venue, instrument, external, product = source_by_symbol[symbol]
            for tf, closed_at in (
                ("5", join.closed_5m_at), ("15", join.closed_15m_at),
            ):
                self._key[symbol, tf] = MarketCandleKey(
                    source, venue, instrument, external, product, tf,
                )
                self._emitted[symbol, tf] = closed_at
                self._canonical(symbol, tf, through=closed_at, count=1)

    def _canonical(
        self, symbol: str, tf: str, *,
        through: datetime, count: int,
    ) -> tuple[Candle, ...]:
        key = self._key.get((symbol, tf))
        if key is None:
            raise RuntimeError("P90_REAL_CANDLE_UNRECOGNIZED_SOURCE")
        return SharedClosedCandleStore(self._db, key).history(
            through=through, required=count,
        )

    def append(self, event: P90TransportEvent) -> bool:
        """Read-and-compare only: original MarketSource already owns the bar."""
        fact, cursor = restore_p90l90_fact(event)
        if event.kind != "CANDLE_CLOSED" or cursor is not None:
            raise ValueError("P90_REAL_CANDLE_EXPECTED_CLOSED_FACT")
        tf = event.timeframe
        if tf not in ("5", "15"):
            raise ValueError("P90_REAL_CANDLE_INVALID_TIMEFRAME")
        attrs = fact.attributes.to_dict()
        actual = self._canonical(event.symbol, tf, through=event.event_at, count=1)[0]
        expected = Candle(
            symbol=event.symbol,
            timeframe=tf,
            opened_at=datetime.fromisoformat(str(attrs["opened_at"])),
            closed_at=event.event_at,
            open=Decimal(str(attrs["open"])),
            high=Decimal(str(attrs["high"])),
            low=Decimal(str(attrs["low"])),
            close=Decimal(str(attrs["close"])),
            volume=Decimal(str(attrs["volume"])),
        )
        if actual != expected:
            raise RuntimeError("P90_REAL_CANDLE_SHARED_SOURCE_CONFLICT")
        previous = self._emitted.get((event.symbol, tf))
        if previous is None or event.event_at != previous + timedelta(minutes=int(tf)):
            raise RuntimeError("P90_REAL_CANDLE_MISSING_OR_REORDERED_BOUNDARY")
        self._emitted[event.symbol, tf] = event.event_at
        return False

    def closed_at_or_before(
        self, *, symbol: str, timeframe: str, through: datetime,
    ) -> datetime:
        current = self._emitted.get((symbol, timeframe))
        if current is None:
            raise RuntimeError("P90_REAL_CANDLE_SYMBOL_TIMEFRAME_UNKNOWN")
        if through >= current:
            return current
        return self._canonical(symbol, timeframe, through=through, count=1)[0].closed_at

    def history(
        self, *, symbol: str, timeframe: str,
        through: datetime, limit: int,
    ) -> tuple[Candle, ...]:
        current = self._emitted.get((symbol, timeframe))
        if current is None or through > current:
            raise RuntimeError("P90_REAL_CANDLE_UNEMITTED_FUTURE_BAR")
        if not 1 <= limit <= 2800:
            raise ValueError("P90_REAL_CANDLE_HISTORY_UNBOUNDED")
        return self._canonical(symbol, timeframe, through=through, count=limit)

    def recompute_geometry(
        self, *, symbol: str, effective_at: datetime,
    ) -> P90L90EntryState | None:
        if effective_at > self._emitted.get(
            (symbol, "5"), datetime.min.replace(tzinfo=UTC)
        ):
            raise RuntimeError("P90_REAL_CANDLE_GEOMETRY_AHEAD_OF_EPOCH")
        fifteen = effective_at.replace(
            minute=(effective_at.minute // 15) * 15,
            second=0, microsecond=0,
        )
        fives = self.history(
            symbol=symbol, timeframe="5", through=effective_at, limit=2600,
        )
        fifteens = self.history(
            symbol=symbol, timeframe="15", through=fifteen, limit=999,
        )
        return causal_entry_state(
            fives, fifteens, effective_at=effective_at,
        )

    def restart_watermarks(self, symbol: str) -> dict[str, datetime]:
        if symbol not in _SYMBOLS:
            raise RuntimeError("P90_REAL_CANDLE_SYMBOL_UNKNOWN")
        return {tf: self._emitted[symbol, tf] for tf in ("5", "15")}

    def source_id(self, symbol: str) -> str:
        return self._key[symbol, "5"].market_source_id
