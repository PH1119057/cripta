"""One-transaction P90L90 PAPER Entry + native transport checkpoint.

The same five-symbol PostgreSQL row commits BOTH the causal native/kline
join state and the Entry previous-tick/first-crossing state. This component
never selects an account, activates a Strategy, sends an order or touches R1.

A PAPER callback may mutate only the supplied local Entry state and SQL on
the SAME connection/transaction. On any exception, discard the caller's
in-memory epoch; the saved row and any PAPER positions roll back together.
The caller must verify previous 5m/15m geometry independently from immutable
historical source at the saved effective time on EVERY restart. This file is
not a substitute for that missing historical ledger or Observer wiring.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .fingerprint import canonical_json
from .p90l90_causal import P90L90CausalEntry, P90L90EntryState
from .p90l90_causal_fact_codec import restore_p90l90_fact
from .p90l90_causal_transport_join import (
    P90L90CausalTransportJoin,
    P90TransportEvent,
)
from .p90l90_entry_restart_checkpoint import (
    entry_restart_snapshot,
    restore_entry_restart_snapshot,
)

_SHA = re.compile(r"^[0-9a-f]{40}$")
_SYMBOLS = frozenset({"LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT"})


class P90L90AtomicPaperCheckpointStore:
    """Fail closed without migrated, checked, durable Entry state."""

    def __init__(
        self, connection: Any, *, source_commit: str,
        writer_release_commit: str | None = None,
    ) -> None:
        if not _SHA.fullmatch(source_commit):
            raise ValueError("P90L90 atomic state requires exact source commit")
        if (
            writer_release_commit is not None
            and not _SHA.fullmatch(writer_release_commit)
        ):
            raise ValueError("P90L90 checkpoint writer release invalid")
        self._db = connection
        self._sha = source_commit  # Immutable seed lineage, never relabelled.
        self._writer_release = writer_release_commit

    @staticmethod
    def _symbol(symbol: str) -> None:
        if symbol not in _SYMBOLS:
            raise ValueError("P90L90 atomic state symbol outside two Strategies")

    def seed(
        self,
        *,
        symbol: str,
        last_closed_5m: datetime,
        last_closed_15m: datetime,
        entry: P90L90CausalEntry,
        proven_geometry: P90L90EntryState | None,
    ) -> None:
        """Seed only absent source state; never backfill a legacy NULL entry.

        The caller must independently prove these history boundaries, source
        commit, watch geometry and absence of unacknowledged earlier events.
        No self-approved production bootstrapping is implied.
        """
        self._symbol(symbol)
        if entry.state != proven_geometry:
            raise RuntimeError("P90L90 initial historical Entry geometry not proven")
        if (
            entry.previous_at is not None or entry.previous_price is not None
            or entry.seen
        ):
            raise RuntimeError("P90L90 initial native cursor cannot be invented")
        join = P90L90CausalTransportJoin(
            symbol=symbol,
            closed_5m_at=last_closed_5m,
            closed_15m_at=last_closed_15m,
        )
        if entry.state is not None and entry.state.effective_at > join.closed_5m_at:
            raise RuntimeError("P90L90 initial Entry geometry ahead of closed bar")
        entry_json = entry_restart_snapshot(entry)
        with self._db.transaction():
            self._db.execute(
                """INSERT INTO strategy_entry.p90l90_causal_transport_checkpoints
                       (symbol,checkpoint_json,entry_checkpoint_json,source_commit,generation)
                   VALUES(%s,%s::jsonb,%s::jsonb,%s,0)
                   ON CONFLICT (symbol) DO NOTHING""",
                (
                    symbol, canonical_json(join.snapshot()),
                    canonical_json(entry_json), self._sha,
                ),
            )
            row = self._db.execute(
                """SELECT checkpoint_json,entry_checkpoint_json,source_commit,generation
                     FROM strategy_entry.p90l90_causal_transport_checkpoints
                    WHERE symbol=%s FOR UPDATE""", (symbol,),
            ).fetchone()
            if row is None or row[2] != self._sha:
                raise RuntimeError("P90L90 atomic seed source identity mismatch")
            actual = P90L90CausalTransportJoin.restore(row[0])
            if actual.snapshot() != join.snapshot() or row[1] != entry_json:
                raise RuntimeError(
                    "P90L90 atomic seed would reset pending or previously committed state"
                )

    def inspect(
        self,
        *,
        symbol: str,
        recomputed_geometry: P90L90EntryState | None,
    ) -> tuple[P90L90CausalTransportJoin, P90L90CausalEntry]:
        """Read-only restart checkpoint, including any unresolved native queue."""
        self._symbol(symbol)
        row = self._db.execute(
            """SELECT checkpoint_json,entry_checkpoint_json,source_commit
                 FROM strategy_entry.p90l90_causal_transport_checkpoints
                WHERE symbol=%s""", (symbol,),
        ).fetchone()
        if row is None or row[2] != self._sha or row[1] is None:
            raise RuntimeError("P90L90 atomic restart missing matching durable state")
        join = P90L90CausalTransportJoin.restore(row[0])
        entry = restore_entry_restart_snapshot(
            row[1], seeded_geometry=recomputed_geometry,
        )
        if entry.state is not None and entry.state.effective_at > join.closed_5m_at:
            raise RuntimeError("P90L90 restored Entry geometry ahead of causal bars")
        return join, entry

    def inspect_verified(
        self,
        *,
        symbol: str,
        recompute_geometry: Callable[[str, object], P90L90EntryState | None],
    ) -> tuple[P90L90CausalTransportJoin, P90L90CausalEntry]:
        """Restart atomically against the saved source and as-of candle ledger."""
        self._symbol(symbol)
        with self._db.transaction():
            # Activation preflight is a SERIALIZABLE READ ONLY transaction:
            # PostgreSQL forbids SELECT FOR UPDATE there. Its MVCC snapshot
            # already supplies consistent source/Entry restart evidence.
            # Keep the row lock for writable live Observer recovery.
            mode = self._db.execute("SHOW transaction_read_only").fetchone()
            if mode not in (("on",), ("off",)):
                raise RuntimeError("P90L90_ATOMIC_RESTART_TX_MODE_UNPROVEN")
            lock = " FOR UPDATE" if mode[0] == "off" else ""
            row = self._db.execute(
                """SELECT checkpoint_json,entry_checkpoint_json,source_commit
                     FROM strategy_entry.p90l90_causal_transport_checkpoints
                    WHERE symbol=%s""" + lock,
                (symbol,),
            ).fetchone()
            if row is None or row[2] != self._sha or row[1] is None:
                raise RuntimeError("P90L90 verified restart durable state missing")
            join = P90L90CausalTransportJoin.restore(row[0])
            geometry = recompute_geometry(symbol, row[1])
            entry = restore_entry_restart_snapshot(
                row[1], seeded_geometry=geometry,
            )
            if entry.state is not None and (
                entry.state.effective_at > join.closed_5m_at
            ):
                raise RuntimeError("P90L90 verified restart has future Entry geometry")
            return join, entry

    def deliver(
        self,
        event: P90TransportEvent,
        *,
        recompute_geometry: Callable[[str, object], P90L90EntryState | None],
        apply_paper: Callable[
            [P90L90CausalEntry, tuple[P90TransportEvent, ...]], None
        ],
    ) -> tuple[P90TransportEvent, ...]:
        """Commit queue, Entry crossing and dependent PAPER DB writes together.

        Each dispatch reloads Entry from the committed row. The provided
        recompute_geometry callback MUST use an independently proven original
        5m/15m candle history as-of the saved checkpoint. The apply_paper
        callback MUST process all released facts on the same connection, and
        must not retain or mutate an outside in-memory watch.
        """
        self._symbol(event.symbol)
        with self._db.transaction():
            row = self._db.execute(
                """SELECT checkpoint_json,entry_checkpoint_json,source_commit,generation
                     FROM strategy_entry.p90l90_causal_transport_checkpoints
                    WHERE symbol=%s FOR UPDATE""", (event.symbol,),
            ).fetchone()
            if row is None or row[2] != self._sha or row[1] is None:
                raise RuntimeError("P90L90 causal PAPER state absent/untrusted")
            join = P90L90CausalTransportJoin.restore(row[0])
            geometry = recompute_geometry(event.symbol, row[1])
            entry = restore_entry_restart_snapshot(
                row[1], seeded_geometry=geometry,
            )
            if entry.state is not None and (
                entry.state.effective_at > join.closed_5m_at
            ):
                raise RuntimeError("P90L90 restored Entry ahead of committed candle")
            released = join.accept(event)
            # Dependent DB effects, including PAPER position mutations, MUST
            # share this transaction, not a second connection.
            apply_paper(entry, released)
            native = [
                item for item in released if item.kind == "PUBLIC_TRADE"
            ]
            if native:
                native_fact, _ = restore_p90l90_fact(native[-1])
                observed_price = native_fact.attributes.to_dict().get("price")
                if (
                    entry.previous_at != native[-1].event_at
                    or entry.previous_price is None
                    or str(entry.previous_price) != str(observed_price)
                ):
                    raise RuntimeError(
                        "P90L90 PAPER handler did not checkpoint last native trade"
                    )
            if entry.state is not None and (
                entry.state.effective_at > join.closed_5m_at
            ):
                raise RuntimeError("P90L90 PAPER advanced geometry beyond candle")
            updates = (
                "last_writer_release_commit=%s,"
                if self._writer_release is not None else ""
            )
            parameters: tuple[Any, ...] = (
                canonical_json(join.snapshot()),
                canonical_json(entry_restart_snapshot(entry)),
            )
            if self._writer_release is not None:
                parameters += (self._writer_release,)
            parameters += (event.symbol, self._sha, row[3])
            updated = self._db.execute(
                """UPDATE strategy_entry.p90l90_causal_transport_checkpoints
                      SET checkpoint_json=%s::jsonb,entry_checkpoint_json=%s::jsonb,"""
                + updates
                + """generation=generation+1,updated_at=clock_timestamp()
                    WHERE symbol=%s AND source_commit=%s AND generation=%s
                    RETURNING generation""",
                parameters,
            ).fetchone()
            if updated is None:
                raise RuntimeError("P90L90 atomic checkpoint concurrent mutation")
            return released
