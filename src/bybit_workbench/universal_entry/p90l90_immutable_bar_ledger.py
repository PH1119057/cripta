"""Immutable, source-bound P90 closed 5m/15m candles for causal restart.

Append-only normalized candle envelopes are the exact as-of Entry history.
A conflicting REST/WS bar with identical (symbol,timeframe,closed_at) raises,
even after a process restart. The read side never forward-fills missing bars.

This library does not read the exchange, dispatch any order or activate PAPER.
The missing Observer inbound raw-vendor-source proof and epoch reconstruction
remain separate release gates.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from bybit_workbench.domain.models import Candle

from .fingerprint import canonical_json
from .p90l90_causal import P90L90EntryState, causal_entry_state
from .p90l90_causal_fact_codec import restore_p90l90_fact
from .p90l90_causal_transport_join import P90TransportEvent

_SHA = re.compile(r"^[0-9a-f]{40}$")
_INTERVALS = {"5": timedelta(minutes=5), "15": timedelta(minutes=15)}
_SYMBOLS = frozenset({"LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT"})
_TABLE = "strategy_entry.p90l90_causal_closed_bars"


def _as_candle(event: P90TransportEvent) -> Candle:
    if event.kind != "CANDLE_CLOSED" or event.timeframe not in _INTERVALS:
        raise ValueError("P90L90 bar ledger accepts only completed 5m/15m")
    fact, cursor = restore_p90l90_fact(event)
    if cursor is not None:
        raise ValueError("P90L90 closed bar has unexpected native cursor")
    raw = fact.attributes.to_dict()
    candle = Candle(
        symbol=event.symbol,
        timeframe=event.timeframe,
        opened_at=datetime.fromisoformat(str(raw["opened_at"])),
        closed_at=event.event_at,
        open=Decimal(str(raw["open"])),
        high=Decimal(str(raw["high"])),
        low=Decimal(str(raw["low"])),
        close=Decimal(str(raw["close"])),
        volume=Decimal(str(raw["volume"])),
        is_closed=True,
    )
    if candle.opened_at + _INTERVALS[event.timeframe] != candle.closed_at:
        raise ValueError("P90L90 closed bar duration not exact")
    if candle.low > min(candle.open, candle.close, candle.high) or (
        candle.high < max(candle.open, candle.close, candle.low)
    ) or candle.volume < 0:
        raise ValueError("P90L90 closed bar OHLCV not coherent")
    return candle


class P90L90ImmutableBarLedger:
    def __init__(self, connection: Any, *, source_commit: str) -> None:
        if not _SHA.fullmatch(source_commit):
            raise ValueError("P90L90 bar ledger requires exact source commit")
        self._db = connection
        self._sha = source_commit

    def append(self, event: P90TransportEvent) -> bool:
        """Append the original normalized bar; exact duplicates are idempotent.

        Return True for a new bar, False for an exact duplicate. Never change
        historical prices, timestamp or source identity after first receipt.
        Call inside the P90 join+Entry transaction for live PAPER usage.
        """
        if event.symbol not in _SYMBOLS:
            raise ValueError("P90L90 bar ledger symbol outside allowed scope")
        _as_candle(event)
        payload = event.record()
        with self._db.transaction():
            inserted = self._db.execute(
                """INSERT INTO strategy_entry.p90l90_causal_closed_bars
                       (symbol,timeframe,closed_at,event_json,source_commit)
                   VALUES(%s,%s,%s,%s::jsonb,%s)
                   ON CONFLICT (symbol,timeframe,closed_at) DO NOTHING
                   RETURNING closed_at""",
                (
                    event.symbol, event.timeframe, event.event_at,
                    canonical_json(payload), self._sha,
                ),
            ).fetchone()
            row = self._db.execute(
                """SELECT event_json,source_commit
                     FROM strategy_entry.p90l90_causal_closed_bars
                    WHERE symbol=%s AND timeframe=%s AND closed_at=%s""",
                (event.symbol, event.timeframe, event.event_at),
            ).fetchone()
            if row is None or row[1] != self._sha or row[0] != payload:
                raise RuntimeError(
                    "P90L90 conflicting immutable closed candle/source commit"
                )
            return inserted is not None

    def history(
        self,
        *,
        symbol: str,
        timeframe: str,
        through: datetime,
        limit: int,
    ) -> tuple[Candle, ...]:
        """Read consecutive immutable as-of candles with no interpolation."""
        if symbol not in _SYMBOLS or timeframe not in _INTERVALS:
            raise ValueError("P90L90 history outside five-symbol 5m/15m scope")
        if (
            through.tzinfo is None or through.utcoffset() != timedelta()
            or through.second or through.microsecond
            or through.minute % int(timeframe)
        ):
            raise ValueError("P90L90 history requires exact UTC bar boundary")
        if type(limit) is not int or not (1 <= limit <= 2800):
            raise ValueError("P90L90 history unbounded limit prohibited")
        if timeframe == "15" and limit > 1000:
            raise ValueError("P90L90 15m history limit exceeds seeded source")
        rows = self._db.execute(
            """SELECT event_json,source_commit
                 FROM strategy_entry.p90l90_causal_closed_bars
                WHERE symbol=%s AND timeframe=%s AND closed_at <= %s
                ORDER BY closed_at DESC LIMIT %s""",
            (symbol, timeframe, through, limit),
        ).fetchall()
        bars: list[Candle] = []
        for payload, source in reversed(rows):
            if source != self._sha:
                raise RuntimeError("P90L90 historical source release mismatch")
            event = P90TransportEvent.restore(payload)
            candle = _as_candle(event)
            if candle.symbol != symbol or candle.timeframe != timeframe:
                raise RuntimeError("P90L90 historical bar scope mismatch")
            if bars and candle.closed_at - bars[-1].closed_at != _INTERVALS[timeframe]:
                raise RuntimeError("P90L90 immutable candle archive contains a gap")
            bars.append(candle)
        if not bars or bars[-1].closed_at != through:
            raise RuntimeError("P90L90 immutable history incomplete at causal boundary")
        return tuple(bars)

    def recompute_geometry(
        self, *, symbol: str, effective_at: datetime
    ) -> P90L90EntryState | None:
        """Calculate the Entry state only from committed no-lookahead bars."""
        if effective_at.tzinfo is None or effective_at.utcoffset() != timedelta():
            raise ValueError("P90L90 geometry effective_at must be UTC")
        fifteen = effective_at.replace(
            minute=effective_at.minute // 15 * 15,
            second=0, microsecond=0,
        )
        five_bars = self.history(
            symbol=symbol, timeframe="5", through=effective_at, limit=2800,
        )
        fifteen_bars = self.history(
            symbol=symbol, timeframe="15", through=fifteen, limit=1000,
        )
        return causal_entry_state(
            five_bars, fifteen_bars, effective_at=effective_at,
        )
