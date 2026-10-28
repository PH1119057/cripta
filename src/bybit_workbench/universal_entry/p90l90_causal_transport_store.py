"""PostgreSQL SERIALIZABLE boundary for P90L90 causal market-fact joining.

No Exchange or StrategyActivation operations. Uses the dedicated five-symbol
PAPER-only migration. The caller's dispatch callback must be deterministic,
perform ONLY same-connection database operations, and must stop/rebuild the
whole in-memory epoch after any exception. A restored nonempty queue cannot
be silently replaced with newer REST candle history.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .fingerprint import canonical_json
from .p90l90_causal_transport_join import (
    P90L90CausalTransportJoin,
    P90TransportEvent,
)

_CHECKPOINT_TABLE = "strategy_entry.p90l90_causal_transport_checkpoints"
_SHA = re.compile(r"^[0-9a-f]{40}$")


class P90L90CausalJoinStore:
    def __init__(self, connection: Any, *, source_commit: str) -> None:
        if not _SHA.fullmatch(source_commit):
            raise ValueError("P90L90 causal join requires exact 40-char source commit")
        self._connection = connection
        self._source_commit = source_commit

    def initialize(
        self, *, symbol: str, last_closed_5m: datetime, last_closed_15m: datetime
    ) -> P90L90CausalTransportJoin:
        """Seed a previously unseen symbol, never silently reset old state."""
        state = P90L90CausalTransportJoin(
            symbol=symbol,
            closed_5m_at=last_closed_5m,
            closed_15m_at=last_closed_15m,
        )
        with self._connection.transaction():
            self._connection.execute(
                """INSERT INTO strategy_entry.p90l90_causal_transport_checkpoints
                     (symbol,checkpoint_json,source_commit,generation)
                   VALUES(%s,%s::jsonb,%s,0)
                   ON CONFLICT (symbol) DO NOTHING""",
                (symbol, canonical_json(state.snapshot()), self._source_commit),
            )
            row = self._connection.execute(
                """SELECT checkpoint_json,source_commit
                     FROM strategy_entry.p90l90_causal_transport_checkpoints
                    WHERE symbol=%s FOR UPDATE""",
                (symbol,),
            ).fetchone()
            if row is None or row[1] != self._source_commit:
                raise RuntimeError("P90L90 causal checkpoint source release mismatch")
            previous = P90L90CausalTransportJoin.restore(row[0])
            if previous.pending:
                raise RuntimeError(
                    "P90L90 restored pending native trades require ordered historical "
                    "state reconstruction before observer restart"
                )
            if (
                previous.closed_5m_at != state.closed_5m_at
                or previous.closed_15m_at != state.closed_15m_at
            ):
                raise RuntimeError(
                    "P90L90 causal checkpoint/REST seed boundary mismatch"
                )
            return previous

    def deliver(
        self,
        event: P90TransportEvent,
        *,
        dispatch: Callable[[tuple[P90TransportEvent, ...]], None],
    ) -> tuple[P90TransportEvent, ...]:
        """Atomic P90 fact journal update and dependent PAPER DB mutations.

        Fail/rollback if source, snapshot, exchange cursor or callback is
        unprovable; never confirm receipt of an event before COMMIT.
        """
        with self._connection.transaction():
            row = self._connection.execute(
                """SELECT checkpoint_json,source_commit,generation
                     FROM strategy_entry.p90l90_causal_transport_checkpoints
                    WHERE symbol=%s FOR UPDATE""",
                (event.symbol,),
            ).fetchone()
            if row is None or row[1] != self._source_commit:
                raise RuntimeError(
                    "P90L90 cannot consume native trade without exact durable "
                    "checkpoint and source commit"
                )
            state = P90L90CausalTransportJoin.restore(row[0])
            released = state.accept(event)
            # This callback MUST use only the same transaction/connection.
            dispatch(released)
            updated = self._connection.execute(
                """UPDATE strategy_entry.p90l90_causal_transport_checkpoints
                      SET checkpoint_json=%s::jsonb,generation=generation+1,
                          updated_at=clock_timestamp()
                    WHERE symbol=%s AND generation=%s
                      AND source_commit=%s
                    RETURNING generation""",
                (
                    canonical_json(state.snapshot()), event.symbol, row[2],
                    self._source_commit,
                ),
            ).fetchone()
            if updated is None:
                raise RuntimeError("P90L90 causal checkpoint concurrent mutation")
            return released
