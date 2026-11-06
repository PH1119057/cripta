"""Exact rolling PUBLIC_TRADE sequence ID journal for P90 REAL and PAPER.

The original P90 atomic checkpoint row lock is the serialization owner.
This sidecar belongs to that SAME source transaction: previous sequence IDs
are retired and current IDs are inserted only when dependent Entry/Exit
effects and the JSON checkpoint commit atomically. It is not a market history,
a second producer, a Strategy policy or a new Exchange control.

Do not call bind() in read-only preflight: upgrading V1 bootstrap necessarily
INSERTs its current (at most 512) proven original IDs. Read-only preflight
uses verify() instead. No fallback is allowed for missing V2 evidence.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from .p90l90_causal_transport_join import P90L90CausalTransportJoin

_MODE = Literal["REAL", "PAPER"]
_SHA = re.compile(r"^[0-9a-f]{40}$")
_EPOCH = re.compile(r"^p90-real-epoch-[0-9a-f]{40}$")
_SYMBOLS = frozenset(("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT"))
_TABLE = "strategy_entry.p90l90_native_sequence_exec_ids"
_REST_MAX = 1000


class P90NativeSequenceIdJournal:
    """Exact PostgreSQL B-tree ID membership with bounded Python working set.

    All methods share the caller's connection and existing checkpoint xact.
    A writer MUST lock the corresponding original source checkpoint row first.
    """

    def __init__(
        self,
        connection: Any, *,
        mode: _MODE,
        source_epoch_id: str,
        source_commit: str,
        symbol: str,
    ) -> None:
        if (
            mode not in ("REAL", "PAPER")
            or (mode == "REAL" and not _EPOCH.fullmatch(source_epoch_id))
            or (mode == "PAPER" and source_epoch_id != "PAPER")
            or not _SHA.fullmatch(source_commit)
            or symbol not in _SYMBOLS
        ):
            raise ValueError("P90_NATIVE_SEQ_ID_JOURNAL_EXACT_SCOPE_INVALID")
        self._db = connection
        self._mode = mode
        self._scope = (mode, source_epoch_id, source_commit, symbol)
        self._symbol = symbol

    def _group_counts(self) -> list[tuple[int, int]]:
        rows = self._db.execute(
            """SELECT exchange_sequence, count(*)
                 FROM strategy_entry.p90l90_native_sequence_exec_ids
                WHERE mode=%s AND source_epoch_id=%s
                  AND source_commit=%s AND symbol=%s
                GROUP BY exchange_sequence
                ORDER BY exchange_sequence""",
            self._scope,
        ).fetchall()
        return [(int(seq), int(count)) for seq, count in rows]

    def verify(self, join: P90L90CausalTransportJoin) -> None:
        """Prove that the checkpoint and rolling sidecar match, read-only."""
        if join.symbol != self._symbol:
            raise RuntimeError("P90_NATIVE_SEQ_ID_SYMBOL_MISMATCH")
        groups = self._group_counts()
        seq = join.last_emitted_trade_sequence
        count = join.native_id_journal_count
        if count is None:
            # Legacy V1 has the full original ID set in its own checkpoint.
            # A partially published journal cannot silently upgrade it.
            if groups:
                raise RuntimeError("P90_NATIVE_SEQ_ID_LEGACY_JOURNAL_UNEXPECTED")
            return
        if (
            (seq is None and (groups or count != 0 or join.last_emitted_exec_ids))
            or (seq is not None and (groups != [(seq, count)] or count <= 0))
        ):
            raise RuntimeError("P90_NATIVE_SEQ_ID_JOURNAL_COUNT_MISMATCH")
        if join.last_emitted_exec_ids:
            present = self._db.execute(
                """SELECT exec_id
                     FROM strategy_entry.p90l90_native_sequence_exec_ids
                    WHERE mode=%s AND source_epoch_id=%s
                      AND source_commit=%s AND symbol=%s
                      AND exchange_sequence=%s AND exec_id = ANY(%s)""",
                (*self._scope, seq, sorted(join.last_emitted_exec_ids)),
            ).fetchall()
            if {str(row[0]) for row in present} != join.last_emitted_exec_ids:
                raise RuntimeError("P90_NATIVE_SEQ_ID_JOURNAL_WITNESS_MISSING")

    def bind(self, join: P90L90CausalTransportJoin) -> None:
        """Writable source checkpoint transaction only, AFTER row locking."""
        self.verify(join)
        if join.native_id_journal_count is None:
            # One-time V1 -> V2 bridge. Checkpoint and original IDs are both
            # protected by this caller's row lock and are committed together.
            seq = join.last_emitted_trade_sequence
            if seq is not None:
                for exec_id in sorted(join.last_emitted_exec_ids):
                    self._insert(seq, exec_id)
        join.bind_exact_journal(self)
        self.verify(join)

    def contains(self, seq: int, exec_id: str) -> bool:
        row = self._db.execute(
            """SELECT 1
                 FROM strategy_entry.p90l90_native_sequence_exec_ids
                WHERE mode=%s AND source_epoch_id=%s AND source_commit=%s
                  AND symbol=%s AND exchange_sequence=%s AND exec_id=%s""",
            (*self._scope, seq, exec_id),
        ).fetchone()
        return row == (1,)

    def _insert(self, seq: int, exec_id: str) -> None:
        if type(seq) is not int or seq < 0 or not exec_id:
            raise RuntimeError("P90_NATIVE_SEQ_ID_INVALID")
        row = self._db.execute(
            """INSERT INTO strategy_entry.p90l90_native_sequence_exec_ids
                   (mode,source_epoch_id,source_commit,symbol,
                    exchange_sequence,exec_id)
                 VALUES(%s,%s,%s,%s,%s,%s)
                 ON CONFLICT DO NOTHING RETURNING exec_id""",
            (*self._scope, seq, exec_id),
        ).fetchone()
        if row != (exec_id,):
            raise RuntimeError("P90_NATIVE_SEQ_ID_DUPLICATE_INSERT_AFTER_PROOF")

    def append(self, seq: int, exec_id: str, previous: int | None) -> None:
        """Only inside the same original checkpoint + dependent-effects xact."""
        if previous != seq:
            self._db.execute(
                """DELETE FROM strategy_entry.p90l90_native_sequence_exec_ids
                    WHERE mode=%s AND source_epoch_id=%s AND source_commit=%s
                      AND symbol=%s AND exchange_sequence<>%s""",
                (*self._scope, seq),
            )
        self._insert(seq, exec_id)

    def full_restart_ids(self, join: P90L90CausalTransportJoin) -> tuple[str, ...]:
        """Bounded REAL native REST proof; never truncate more than 1000 IDs."""
        self.verify(join)
        if join.native_id_journal_count is None:
            return tuple(sorted(join.last_emitted_exec_ids))
        seq = join.last_emitted_trade_sequence
        if seq is None:
            return ()
        if join.native_id_journal_count > _REST_MAX:
            raise RuntimeError("P90_NATIVE_RESTART_IDS_EXCEED_REST_WINDOW")
        rows = self._db.execute(
            """SELECT exec_id
                 FROM strategy_entry.p90l90_native_sequence_exec_ids
                WHERE mode=%s AND source_epoch_id=%s AND source_commit=%s
                  AND symbol=%s AND exchange_sequence=%s
                ORDER BY exec_id LIMIT 1001""",
            (*self._scope, seq),
        ).fetchall()
        if len(rows) != join.native_id_journal_count:
            raise RuntimeError("P90_NATIVE_RESTART_EXACT_IDS_UNVERIFIED")
        return tuple(str(row[0]) for row in rows)
