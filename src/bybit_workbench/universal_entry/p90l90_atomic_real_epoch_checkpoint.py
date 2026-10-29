"""REAL P90L90 per-epoch native+Entry checkpoint; old PAPER tables untouched.

A new epoch is permitted ONLY under ARCH §48.4 when there are NO open
positions/pending execution requests, after complete canonical closed-candle
warmup and actual Bybit publicTrade WS/mirror source attestation. The caller
must create a unique append-preserving p90l90_real_source_epochs record first.
This class never authorizes creating that epoch, never reads old PAPER
checkpoints and never manufactures native cursor history.

All delivery side effects must commit under one original source transaction.
A retired or absent source epoch is never accepted. No trading arm is set.
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


class P90L90AtomicRealEpochCheckpointStore:
    """Fail closed without migrated, checked, durable Entry state."""

    def __init__(
        self, connection: Any, *, source_commit: str,
        writer_release_commit: str | None = None,
        epoch_id: str,
    ) -> None:
        if not _SHA.fullmatch(source_commit):
            raise ValueError("P90L90 atomic state requires exact source commit")
        if (
            writer_release_commit is not None
            and not _SHA.fullmatch(writer_release_commit)
        ):
            raise ValueError("P90L90 checkpoint writer release invalid")
        if not re.fullmatch(r"p90-real-epoch-[0-9a-f]{40}", epoch_id):
            raise ValueError("P90_REAL_EXACT_SOURCE_EPOCH_ID_REQUIRED")
        self._epoch_id = epoch_id
        self._db = connection
        self._sha = source_commit  # Exact new REAL source producer; old PAPER untouched.
        self._writer_release = writer_release_commit

    def _require_active_epoch(self) -> None:
        row = self._db.execute(
            """SELECT source_commit,status
                 FROM strategy_entry.p90l90_real_source_epochs
                WHERE epoch_id=%s""", (self._epoch_id,),
        ).fetchone()
        if row != (self._sha, "ACTIVE"):
            raise RuntimeError("P90_REAL_SOURCE_EPOCH_NOT_CURRENT")

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
            self._require_active_epoch()
            self._db.execute(
                """INSERT INTO strategy_entry.p90l90_real_causal_checkpoints
                       (epoch_id,symbol,checkpoint_json,entry_checkpoint_json,
                        source_commit,generation)
                   VALUES(%s,%s,%s::jsonb,%s::jsonb,%s,0)
                   ON CONFLICT (epoch_id,symbol) DO NOTHING""",
                (
                    self._epoch_id, symbol, canonical_json(join.snapshot()),
                    canonical_json(entry_json), self._sha,
                ),
            )
            row = self._db.execute(
                """SELECT checkpoint_json,entry_checkpoint_json,source_commit,generation
                     FROM strategy_entry.p90l90_real_causal_checkpoints
                    WHERE epoch_id=%s AND symbol=%s FOR UPDATE""",
                (self._epoch_id, symbol),
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
        self._require_active_epoch()
        row = self._db.execute(
            """SELECT checkpoint_json,entry_checkpoint_json,source_commit
                 FROM strategy_entry.p90l90_real_causal_checkpoints
                WHERE epoch_id=%s AND symbol=%s""",
            (self._epoch_id, symbol),
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
            self._require_active_epoch()
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
                     FROM strategy_entry.p90l90_real_causal_checkpoints
                    WHERE epoch_id=%s AND symbol=%s""" + lock,
                (self._epoch_id, symbol),
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
        """Commit queue, Entry crossing and dependent REAL DB writes together.

        Each dispatch reloads Entry from the committed row. The provided
        recompute_geometry callback MUST use an independently proven original
        5m/15m candle history as-of the saved checkpoint. The apply_real
        callback MUST process all released facts on the same connection, and
        must not retain or mutate an outside in-memory watch.
        """
        self._symbol(event.symbol)
        with self._db.transaction():
            self._require_active_epoch()
            row = self._db.execute(
                """SELECT checkpoint_json,entry_checkpoint_json,source_commit,generation
                     FROM strategy_entry.p90l90_real_causal_checkpoints
                    WHERE epoch_id=%s AND symbol=%s FOR UPDATE""",
                (self._epoch_id, event.symbol),
            ).fetchone()
            if row is None or row[2] != self._sha or row[1] is None:
                raise RuntimeError("P90L90 causal REAL state absent/untrusted")
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
            # Dependent DB effects, including REAL position mutations, MUST
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
                        "P90L90 REAL handler did not checkpoint last native trade"
                    )
            if entry.state is not None and (
                entry.state.effective_at > join.closed_5m_at
            ):
                raise RuntimeError("P90L90 REAL advanced geometry beyond candle")
            updates = (
                "writer_release_commit=%s,"
                if self._writer_release is not None else ""
            )
            parameters: tuple[Any, ...] = (
                canonical_json(join.snapshot()),
                canonical_json(entry_restart_snapshot(entry)),
            )
            if self._writer_release is not None:
                parameters += (self._writer_release,)
            parameters += (self._epoch_id, event.symbol, self._sha, row[3])
            updated = self._db.execute(
                """UPDATE strategy_entry.p90l90_real_causal_checkpoints
                      SET checkpoint_json=%s::jsonb,entry_checkpoint_json=%s::jsonb,"""
                + updates
                + """generation=generation+1,updated_at=clock_timestamp()
                    WHERE epoch_id=%s AND symbol=%s
                      AND source_commit=%s AND generation=%s
                    RETURNING generation""",
                parameters,
            ).fetchone()
            if updated is None:
                raise RuntimeError("P90L90 atomic checkpoint concurrent mutation")
            return released
