"""Atomic, account-scoped P90 REAL native MFE ledger on existing Exit tables.

Only the verified native source owner may call this INSIDE the same DB
transaction as P90's native cursor checkpoint. Snapshot persistence is not
private Bybit attestation and cannot by itself authorize a REAL Exit mutation.
There is no backfill from OHLC, reconstructed paper position or first
unverified tick. The caller provides independently verified source coverage.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.contracts import FrozenPolicy
from bybit_workbench.universal_entry.fingerprint import canonical_json, fingerprint
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

from .p90l90_real_mfe import P90RealMfeCheckpoint, P90RealMfeUnverified


class _Cursor(Protocol):
    def fetchone(self) -> Mapping[str, object] | None: ...


class _Connection(Protocol):
    def execute(
        self, statement: str, parameters: Sequence[object] = (),
    ) -> _Cursor: ...


class P90RealMfeLedger:
    """One position/account lock and append-only MFE snapshots per native tick."""

    _KIND = "P90L90_REAL_NATIVE_MFE_CHECKPOINT"

    def __init__(self, connection: _Connection) -> None:
        self._db = connection

    def _require_source_transaction(self) -> None:
        # An autocommit write to the durable MFE ledger would commit a new
        # best-price cursor even if its originating publicTrade checkpoint
        # later rolls back. Do not accept a mere connection or valid cursor
        # as evidence that the outer market-source transaction exists.
        info = getattr(self._db, "info", None)
        transaction = getattr(info, "transaction_status", None)
        if getattr(transaction, "name", None) != "INTRANS":
            raise P90RealMfeUnverified(
                "P90_REAL_MFE_SOURCE_TRANSACTION_NOT_OPEN"
            )

    def on_trade(
        self,
        *,
        position: StrategyPosition,
        source_commit: str,
        fact_id: str,
        cursor: TradeCursor,
        price: Decimal,
        received_at: datetime,
        source_refs: tuple[str, ...],
        first_fill_forward_coverage_proven: bool,
        native_continuity_proven: bool,
    ) -> P90RealMfeCheckpoint:
        if (
            not fact_id
            or not source_refs
            or any(not isinstance(ref, str) or not ref.strip() for ref in source_refs)
            or received_at.tzinfo is None
            or cursor.traded_at.tzinfo is None
            or received_at.astimezone(UTC) < cursor.traded_at.astimezone(UTC)
            or cursor.symbol != position.symbol
        ):
            raise P90RealMfeUnverified("P90_REAL_MFE_SOURCE_FACT_INVALID")
        self._require_source_transaction()
        # Call must already run inside the native source checkpoint xact.
        # Serialize first-insert and read-modify-write for THIS position,
        # never acquire an unrelated account or cross-position MFE lock.
        self._db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (position.strategy_position_id,),
        )
        previous = self._db.execute(
            """SELECT observation_id,attributes,source_refs
                 FROM strategy_exit.exit_observations
                WHERE strategy_position_id=%s AND exit_plan_fingerprint=%s
                  AND event_kind='P90L90_REAL_NATIVE_MFE_CHECKPOINT'
                ORDER BY (attributes->'checkpoint'->>'cursor_seq')::bigint DESC,
                         event_at DESC, observation_id DESC
                LIMIT 1 FOR UPDATE""",
            (position.strategy_position_id, position.exit_plan_fingerprint),
        ).fetchone()
        if previous is None:
            checkpoint = P90RealMfeCheckpoint.begin(
                position=position,
                source_commit=source_commit,
                first_cursor=cursor,
                first_price=price,
                verified_fill_to_first_trade_coverage=first_fill_forward_coverage_proven,
            )
        else:
            attrs = previous.get("attributes")
            if not isinstance(attrs, Mapping):
                raise P90RealMfeUnverified("P90_REAL_MFE_DURABLE_ROW_CORRUPT")
            prior_raw = attrs.get("checkpoint")
            prior = P90RealMfeCheckpoint.restore(prior_raw)
            if prior.source_commit != source_commit:
                raise P90RealMfeUnverified("P90_REAL_MFE_SOURCE_RELEASE_DRIFT")
            checkpoint = prior.advance(
                position=position,
                cursor=cursor,
                price=price,
                proven_contiguous_native_cursor=native_continuity_proven,
            )
            if checkpoint is prior:
                if (
                    attrs.get("native_fact_id") != fact_id
                    or previous.get("source_refs") != list(source_refs)
                ):
                    raise P90RealMfeUnverified("P90_REAL_MFE_REPLAY_PROVENANCE_DRIFT")
                return prior
        observation_id = "p90-real-mfe-" + fingerprint({
            "position_id": position.strategy_position_id,
            "source_commit": source_commit,
            "native_exec_id": cursor.exec_id,
            "native_seq": cursor.seq,
            "native_at": cursor.traded_at.astimezone(UTC).isoformat(),
        })[:32]
        attributes = FrozenPolicy.from_mapping({
            "checkpoint": checkpoint.snapshot(),
            "native_fact_id": fact_id,
        })
        self._db.execute(
            """INSERT INTO strategy_exit.exit_observations(
                   observation_id,strategy_position_id,exit_plan_fingerprint,
                   symbol,event_kind,event_at,observed_at,received_at,
                   attributes,source_refs)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
               ON CONFLICT(observation_id) DO NOTHING""",
            (
                observation_id, position.strategy_position_id,
                position.exit_plan_fingerprint, position.symbol, self._KIND,
                cursor.traded_at, cursor.traded_at, received_at,
                attributes.payload_json, canonical_json(source_refs),
            ),
        )
        saved = self._db.execute(
            """SELECT observation_id,attributes,source_refs,symbol,event_at
                 FROM strategy_exit.exit_observations
                WHERE observation_id=%s""",
            (observation_id,),
        ).fetchone()
        if (
            saved is None
            or saved.get("observation_id") != observation_id
            or saved.get("attributes") != attributes.to_dict()
            or saved.get("source_refs") != list(source_refs)
            or saved.get("symbol") != position.symbol
            or saved.get("event_at") != cursor.traded_at.astimezone(UTC)
        ):
            raise P90RealMfeUnverified("P90_REAL_MFE_DURABLE_IDEMPOTENCY_MISMATCH")
        return checkpoint


    def load_latest(
        self,
        *,
        position: StrategyPosition,
        source_commit: str,
    ) -> tuple[P90RealMfeCheckpoint, str, tuple[str, ...]]:
        """Read exact durable last native state for a later closed 5m event.

        A caller must hold its outer source/Exit transaction. The per-position
        lock serializes new native MFE ticks with bar-close Exit decisions.
        """
        self._require_source_transaction()
        self._db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (position.strategy_position_id,),
        )
        row = self._db.execute(
            """SELECT observation_id,attributes,source_refs
                 FROM strategy_exit.exit_observations
                WHERE strategy_position_id=%s AND exit_plan_fingerprint=%s
                  AND event_kind='P90L90_REAL_NATIVE_MFE_CHECKPOINT'
                ORDER BY (attributes->'checkpoint'->>'cursor_seq')::bigint DESC,
                         event_at DESC, observation_id DESC
                LIMIT 1 FOR UPDATE""",
            (position.strategy_position_id, position.exit_plan_fingerprint),
        ).fetchone()
        if row is None:
            raise P90RealMfeUnverified("P90_REAL_MFE_DURABLE_NATIVE_STATE_MISSING")
        attrs = row.get("attributes")
        refs = row.get("source_refs")
        if (
            not isinstance(attrs, Mapping)
            or not isinstance(refs, list)
            or not refs
            or any(not isinstance(ref, str) or not ref for ref in refs)
        ):
            raise P90RealMfeUnverified("P90_REAL_MFE_DURABLE_SOURCE_REFS_INVALID")
        checkpoint = P90RealMfeCheckpoint.restore(attrs.get("checkpoint"))
        if checkpoint.source_commit != source_commit:
            raise P90RealMfeUnverified("P90_REAL_MFE_SOURCE_RELEASE_DRIFT")
        cursor = TradeCursor(
            symbol=checkpoint.symbol,
            seq=checkpoint.cursor_seq,
            exec_id=checkpoint.cursor_exec_id,
            traded_at=checkpoint.cursor_at,
        )
        checkpoint.advance(
            position=position,
            cursor=cursor,
            price=checkpoint.last_native_price,
            proven_contiguous_native_cursor=True,
        )
        fact_id = attrs.get("native_fact_id")
        expected_id = "p90-real-mfe-" + fingerprint({
            "position_id": position.strategy_position_id,
            "source_commit": source_commit,
            "native_exec_id": cursor.exec_id,
            "native_seq": cursor.seq,
            "native_at": cursor.traded_at.astimezone(UTC).isoformat(),
        })[:32]
        if (
            not isinstance(fact_id, str)
            or not fact_id
            or row.get("observation_id") != expected_id
        ):
            raise P90RealMfeUnverified("P90_REAL_MFE_DURABLE_IDENTITY_TAMPERED")
        return checkpoint, fact_id, tuple(refs)
