"""Shared MarketSource candle cache. This is not a Strategy or an order writer.

All active consumers use the one (source,instrument,timeframe,closed_at) key.
Frozen historical P90L90 records remain legacy audit evidence, not a new writer.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from bybit_workbench.domain.models import Candle

_INTERVALS = {"1": 1, "3": 3, "5": 5, "15": 15, "30": 30,
              "60": 60, "120": 120, "240": 240}
_SHA = re.compile(r"^[a-f0-9]{40}$")
_TRANSPORT = frozenset({"BYBIT_REST_KLINE", "BYBIT_WS_KLINE"})


@dataclass(frozen=True, slots=True)
class MarketCandleKey:
    market_source_id: str
    venue_id: str
    instrument_id: str
    external_symbol: str
    product_category: str
    timeframe: str

    def __post_init__(self) -> None:
        if (not all((self.market_source_id, self.venue_id,
                     self.instrument_id, self.external_symbol))
            or self.product_category not in ("SPOT", "LINEAR")
            or self.timeframe not in _INTERVALS):
            raise ValueError("CANDLE_INVALID_SOURCE_INSTRUMENT_OR_INTERVAL")


def verify_closed_window(
    key: MarketCandleKey, bars: Iterable[Candle],
    *, minimum: int = 1, through: datetime | None = None,
) -> tuple[Candle, ...]:
    """Reject gaps, wrong intervals and unconfirmed bars. No forward fill."""
    result = tuple(bars)
    if minimum < 1 or len(result) < minimum:
        raise RuntimeError("CANDLE_WARMUP_INCOMPLETE")
    step = timedelta(minutes=_INTERVALS[key.timeframe])
    previous: datetime | None = None
    for candle in result:
        if (
            candle.symbol != key.external_symbol
            or candle.timeframe != key.timeframe
            or not candle.is_closed
            or candle.opened_at.utcoffset() != timedelta(0)
            or candle.closed_at.utcoffset() != timedelta(0)
            or candle.opened_at + step != candle.closed_at
            or candle.closed_at.second or candle.closed_at.microsecond
            or int(candle.closed_at.timestamp()) % int(step.total_seconds())
        ):
            raise RuntimeError("CANDLE_UNVERIFIED_CLOSED_SOURCE_BAR")
        if previous is not None and candle.closed_at - previous != step:
            raise RuntimeError("CANDLE_HISTORY_GAP_OR_DUPLICATE")
        previous = candle.closed_at
    if through is not None and (
        through.utcoffset() != timedelta(0)
        or result[-1].closed_at != through
    ):
        raise RuntimeError("CANDLE_WARMUP_END_NOT_PROVEN")
    return result


class SharedClosedCandleStore:
    """Append-only canonical cache; conflicting provider facts fail closed."""

    def __init__(self, connection: Any, key: MarketCandleKey) -> None:
        self._db = connection
        self.key = key

    def ingest(
        self, bars: Iterable[Candle], *, producer_release_commit: str,
        transport: str, observed_at: datetime,
    ) -> int:
        if not _SHA.fullmatch(producer_release_commit):
            raise ValueError("CANDLE_PRODUCER_RELEASE_INVALID")
        if transport not in _TRANSPORT:
            raise ValueError("CANDLE_UNAPPROVED_TRANSPORT")
        if observed_at.utcoffset() != timedelta(0):
            raise ValueError("CANDLE_OBSERVED_NOT_UTC")
        items = verify_closed_window(self.key, bars)
        # Batch source rows: per-bar readback makes multi-day P90 warmup
        # unnecessarily slow on every Observer restart.
        with self._db.transaction():
            source = self._db.execute(
                """SELECT s.status,i.venue_id FROM identity.market_sources s
                   JOIN identity.instruments i ON i.venue_id=s.venue_id
                  WHERE s.market_source_id=%s AND s.venue_id=%s
                    AND i.instrument_id=%s AND i.external_symbol=%s
                    AND i.product_category=%s""",
                (self.key.market_source_id, self.key.venue_id,
                 self.key.instrument_id, self.key.external_symbol,
                 self.key.product_category),
            ).fetchone()
            if source != ("ACTIVE", self.key.venue_id):
                raise RuntimeError("CANDLE_UNVERIFIED_MARKET_SOURCE")

            lookup = (
                self.key.market_source_id, self.key.instrument_id,
                self.key.timeframe, items[0].closed_at, items[-1].closed_at,
            )
            query = """SELECT closed_at,opened_at,open,high,low,close,volume
                         FROM analytics.closed_candles
                        WHERE market_source_id=%s AND instrument_id=%s
                          AND timeframe=%s AND closed_at BETWEEN %s AND %s"""
            before = {
                r[0]: r[1:] for r in self._db.execute(query, lookup).fetchall()
            }
            missing: list[tuple[Any, ...]] = []
            for c in items:
                values = (
                    c.opened_at, c.open, c.high, c.low, c.close, c.volume,
                )
                if c.closed_at in before:
                    if before[c.closed_at] != values:
                        raise RuntimeError("CANDLE_IMMUTABLE_SOURCE_CONFLICT")
                    continue
                missing.append((
                    self.key.market_source_id, self.key.venue_id,
                    self.key.instrument_id, self.key.external_symbol,
                    self.key.product_category, self.key.timeframe,
                    c.opened_at, c.closed_at, c.open, c.high, c.low,
                    c.close, c.volume, transport, producer_release_commit,
                    observed_at,
                ))
            inserted = 0
            if missing:
                with self._db.cursor() as cursor:
                    cursor.executemany(
                        """INSERT INTO analytics.closed_candles
                          (market_source_id,venue_id,instrument_id,external_symbol,
                           product_category,timeframe,opened_at,closed_at,
                           open,high,low,close,volume,source_transport,
                           producer_release_commit,observed_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (market_source_id,instrument_id,timeframe,closed_at)
                           DO NOTHING""",
                        missing,
                    )
                    inserted = cursor.rowcount
            # Repeat exact comparison AFTER any concurrent conflict resolution.
            # PostgreSQL ON CONFLICT never silently accepts differing OHLCV.
            confirmed = {
                r[0]: r[1:] for r in self._db.execute(query, lookup).fetchall()
            }
            for c in items:
                if confirmed.get(c.closed_at) != (
                    c.opened_at, c.open, c.high, c.low, c.close, c.volume,
                ):
                    raise RuntimeError("CANDLE_IMMUTABLE_SOURCE_CONFLICT")
        return inserted

    def history(
        self, *, through: datetime, required: int,
    ) -> tuple[Candle, ...]:
        if required < 1 or required > 10000:
            raise ValueError("CANDLE_INVALID_HISTORY_LENGTH")
        if through.utcoffset() != timedelta(0):
            raise ValueError("CANDLE_HISTORY_TIME_NOT_UTC")
        result = self._db.execute(
            """SELECT opened_at,closed_at,open,high,low,close,volume
                 FROM analytics.closed_candles
                WHERE market_source_id=%s AND instrument_id=%s
                  AND timeframe=%s AND closed_at<=%s
                ORDER BY closed_at DESC LIMIT %s""",
            (self.key.market_source_id, self.key.instrument_id,
             self.key.timeframe, through, required),
        ).fetchall()
        candles = (
            Candle(self.key.external_symbol, self.key.timeframe,
                   opened, closed, op, hi, lo, close, volume)
            for opened, closed, op, hi, lo, close, volume
            in reversed(result)
        )
        return verify_closed_window(
            self.key, candles, minimum=required, through=through,
        )
