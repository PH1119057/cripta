"""Scoped P90 native event dispatch: one PostgreSQL transaction / checkpoint.

This is an infrastructure building block. It does NOT activate a Strategy,
submit an Entry/Exit order, or issue financial permissions. A real Engine
must supply the verified callback and atomic decision writer. Missing source,
consumer plan identity or an uninitialized checkpoint is BLOCKED, not reset.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.market_data.entry_publication_reader import EntryPublicationPin

from .p90l90_legacy_strategy import MAIN_ID, SUB_ID
from .p90l90_real_contract_v3 import ALLOWED_SYMBOLS, VERSION
from .p90l90_scoped_native_source import (
    NativeSourceEvidence,
    NativeSourceProofUnavailable,
    OrderedNativeTrade,
    read_ordered_native_events,
)


class P90ScopedConsumerBlocked(RuntimeError):
    """Source/plan/checkpoint/transaction boundary is not independently proven."""


def _sha(value: object, size: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == size
        and all(c in "0123456789abcdef" for c in value)
    )


def _verify_pinned_publication_bounds(
    connection: Any, *,
    publication_pin: EntryPublicationPin,
    source: NativeSourceEvidence,
    observed_at: datetime,
    max_publication_age: timedelta,
) -> dict[str, tuple[int, int]]:
    """Recheck immutable market publication INSIDE the decision transaction.

    The earlier R06 pin was acquired in a separate read-only REPEATABLE READ
    snapshot. An Entry callback may write only after this transaction proves
    that exactly the same immutable publication belongs to its still-locked
    source epoch. WATERMARKS_ONLY is never treated as geometry/ATR READY.
    """
    if (
        not isinstance(publication_pin, EntryPublicationPin)
        or type(publication_pin.publication_id) is not int
        or publication_pin.publication_id < 1
        or publication_pin.source_session_id != source.session_id
        or publication_pin.producer_release_commit != source.producer_release_commit
        or not isinstance(publication_pin.as_of_second, datetime)
        or publication_pin.as_of_second.utcoffset() != timedelta(0)
        or type(max_publication_age) is not timedelta
        or not timedelta(0) < max_publication_age <= timedelta(seconds=30)
        or not timedelta(0)
            <= observed_at - publication_pin.as_of_second
            <= max_publication_age
    ):
        raise P90ScopedConsumerBlocked("P90_SCOPED_PUBLICATION_IDENTITY_OR_AGE_INVALID")

    row = connection.execute(
        """SELECT source_session_id,producer_release_commit,as_of_second,
                  publication_status,native_watermarks,closed_candle_watermarks
             FROM analytics.market_publications
            WHERE publication_id=%s AND market_source_id=%s""",
        (publication_pin.publication_id, source.market_source_id),
    ).fetchone()
    if (
        row is None
        or row[:4] != (
            source.session_id, source.producer_release_commit,
            publication_pin.as_of_second, "WATERMARKS_ONLY",
        )
        or not isinstance(row[4], dict)
        or not isinstance(row[5], dict)
    ):
        raise P90ScopedConsumerBlocked("P90_SCOPED_PUBLICATION_EPOCH_CHANGED")

    native, candles = row[4], row[5]
    limits: dict[str, tuple[int, int]] = {}
    for symbol in source.by_symbol:
        ref = publication_pin.native_watermarks.get(symbol)
        tf = publication_pin.closed_refs.get(symbol)
        if (
            not isinstance(ref, dict)
            or not isinstance(tf, dict)
            or native.get(symbol) != ref
            or candles.get(symbol) != tf
            or not all(k in tf for k in ("5", "15"))
        ):
            raise P90ScopedConsumerBlocked("P90_SCOPED_PUBLICATION_REF_DRIFT")
        frame, index = ref.get("frame_number"), ref.get("frame_index")
        if (
            type(frame) is not int or frame < 0
            or type(index) is not int or index < 0
        ):
            raise P90ScopedConsumerBlocked("P90_SCOPED_PUBLICATION_WATERMARK_INVALID")
        limits[symbol] = frame, index
    if not limits:
        raise P90ScopedConsumerBlocked("P90_SCOPED_PUBLICATION_SCOPE_EMPTY")
    return limits


def consume_scoped_native_batch(
    connection: Any,
    *,
    source: NativeSourceEvidence,
    strategy_id: str,
    strategy_version: str,
    entry_plan_fingerprint: str,
    exit_plan_fingerprint: str,
    consumer_release_commit: str,
    observed_at: datetime,
    max_evidence_age: timedelta,
    on_native_trade: Callable[[OrderedNativeTrade], None],
    limit: int = 256,
    publication_pin: EntryPublicationPin | None = None,
    max_publication_age: timedelta | None = None,
) -> tuple[OrderedNativeTrade, ...]:
    """Dispatch only events after an existing proven checkpoint, atomically.

    Caller MUST connect this to the existing canonical Engine/Exit owners,
    and keep the financial gate independently OFF until complete parity.
    A new session has NO implicit checkpoint and never inherits the previous
    session's ordinal or invents historical continuity.
    """
    if (
        strategy_id not in (MAIN_ID, SUB_ID)
        or strategy_version != VERSION
        or not _sha(entry_plan_fingerprint, 64)
        or not _sha(exit_plan_fingerprint, 64)
        or not _sha(consumer_release_commit, 40)
        or not isinstance(source, NativeSourceEvidence)
        or source.ready is not True
        or not source.session_id
        or not _sha(source.producer_release_commit, 40)
        or set(source.by_symbol) != set(ALLOWED_SYMBOLS[strategy_id])
        or not callable(on_native_trade)
        or not isinstance(observed_at, datetime)
        or observed_at.utcoffset() is None
        or not isinstance(max_evidence_age, timedelta)
        or not timedelta(0) < max_evidence_age <= timedelta(minutes=1)
        or type(limit) is not int or not 1 <= limit <= 1000
    ):
        raise P90ScopedConsumerBlocked("P90_SCOPED_EXACT_CONSUMER_INPUT_INVALID")
    if (publication_pin is None) != (max_publication_age is None):
        raise P90ScopedConsumerBlocked("P90_SCOPED_PUBLICATION_CONTRACT_INCOMPLETE")
    now = observed_at.astimezone(UTC)
    evidence_age = now - source.checked_at
    if (
        not timedelta(seconds=-2) <= evidence_age <= max_evidence_age
        or source.market_source_id != "ms-bybit-mainnet-public-v1"
    ):
        raise P90ScopedConsumerBlocked("P90_SCOPED_NATIVE_EVIDENCE_STALE")

    with connection.transaction():
        # Hold source epoch shared lock until decision effects+cursor commit.
        # A writer session rollover UPDATE must not silently race the cursor.
        row = connection.execute(
            """SELECT state,producer_release_commit,ended_at,last_accepted_at
                 FROM analytics.native_public_trade_source_sessions
                WHERE source_session_id=%s AND market_source_id=%s
                FOR SHARE""",
            (source.session_id, source.market_source_id),
        ).fetchone()
        if (
            row is None or row[0] != "ACTIVE"
            or row[1] != source.producer_release_commit
            or row[2] is not None
            or not isinstance(row[3], datetime)
            or row[3].utcoffset() is None
            or not timedelta(seconds=-2)
                <= now - row[3].astimezone(UTC) <= max_evidence_age
        ):
            raise P90ScopedConsumerBlocked("P90_SCOPED_SOURCE_SESSION_CHANGED")

        publication_bounds = (
            _verify_pinned_publication_bounds(
                connection, publication_pin=publication_pin,
                source=source, observed_at=now,
                max_publication_age=max_publication_age,
            )
            if publication_pin is not None and max_publication_age is not None
            else None
        )

        cursor = connection.execute(
            """SELECT frame_number,frame_index,entry_plan_fingerprint,
                      exit_plan_fingerprint,producer_release_commit,
                      consumer_release_commit
                 FROM strategy_entry.p90_scoped_native_consumer_checkpoints
                WHERE strategy_id=%s AND strategy_version=%s
                  AND source_session_id=%s
                FOR UPDATE""",
            (strategy_id, strategy_version, source.session_id),
        ).fetchone()
        if cursor is None:
            raise P90ScopedConsumerBlocked(
                "P90_SCOPED_SOURCE_CHECKPOINT_NOT_INITIALIZED"
            )
        frame, index, entry, exit_, producer, loaded = cursor
        if (
            type(frame) is not int or frame < 0
            or type(index) is not int or index < 0
            or entry != entry_plan_fingerprint
            or exit_ != exit_plan_fingerprint
            or producer != source.producer_release_commit
            or loaded != consumer_release_commit
        ):
            raise P90ScopedConsumerBlocked(
                "P90_SCOPED_SOURCE_CHECKPOINT_CONTRACT_DRIFT"
            )

        if (
            publication_bounds is not None
            and (frame, index) > max(publication_bounds.values())
        ):
            raise P90ScopedConsumerBlocked(
                "P90_SCOPED_PUBLICATION_BEHIND_DECISION_CHECKPOINT"
            )

        try:
            batch = read_ordered_native_events(
                connection, source=source,
                after_frame=frame, after_index=index, limit=limit,
            )
        except NativeSourceProofUnavailable as exc:
            raise P90ScopedConsumerBlocked(
                "P90_SCOPED_NATIVE_EVENT_PROOF_FAILED"
            ) from exc
        if publication_bounds is not None:
            # One original provider order across all symbols; never dispatch
            # events past any published per-symbol boundary. A later batch
            # can resume only after another immutable publication is pinned.
            bounded: list[OrderedNativeTrade] = []
            for event in batch:
                upper = publication_bounds.get(event.symbol)
                if upper is None:
                    raise P90ScopedConsumerBlocked(
                        "P90_SCOPED_PUBLICATION_SYMBOL_UNLISTED"
                    )
                if (event.frame_number, event.frame_index) > upper:
                    break
                bounded.append(event)
            batch = tuple(bounded)
        for event in batch:
            if event.symbol not in ALLOWED_SYMBOLS[strategy_id]:
                raise P90ScopedConsumerBlocked(
                    "P90_SCOPED_NATIVE_EVENT_SCOPE_DRIFT"
                )
            # Callback MUST persist real Engine effects with THIS connection,
            # not defer an Exchange command outside the transaction.
            on_native_trade(event)
        if batch:
            last = batch[-1]
            result = connection.execute(
                """UPDATE strategy_entry.p90_scoped_native_consumer_checkpoints
                      SET frame_number=%s,frame_index=%s,last_exec_id=%s,
                          checked_at=clock_timestamp()
                    WHERE strategy_id=%s AND strategy_version=%s
                      AND source_session_id=%s
                      AND frame_number=%s AND frame_index=%s
                      AND consumer_release_commit=%s
                      AND producer_release_commit=%s""",
                (
                    last.frame_number, last.frame_index, last.exec_id,
                    strategy_id, strategy_version, source.session_id,
                    frame, index, loaded, producer,
                ),
            )
            if result.rowcount != 1:
                raise P90ScopedConsumerBlocked(
                    "P90_SCOPED_CHECKPOINT_CONCURRENT_CHANGE"
                )
        return batch


def consume_pinned_entry_native_batch(
    connection: Any,
    *,
    publication_pin: EntryPublicationPin,
    max_publication_age: timedelta,
    source: NativeSourceEvidence,
    strategy_id: str,
    strategy_version: str,
    entry_plan_fingerprint: str,
    exit_plan_fingerprint: str,
    consumer_release_commit: str,
    observed_at: datetime,
    max_evidence_age: timedelta,
    on_publication_trade: Callable[[OrderedNativeTrade, int], None],
    limit: int = 256,
) -> tuple[OrderedNativeTrade, ...]:
    """Stage R06 only: same canonical P90 decision cursor and atomic writer.

    The Engine callback receives the exact immutable publication_id with each
    original event in the SAME PG transaction as checkpoint progress. This
    function is NOT wired to runtime Observer or an Exchange endpoint. The
    supplied callback must independently prove full geometry/ATR/admission;
    pinning WATERMARKS_ONLY is insufficient and grants no financial authority.
    """
    if not isinstance(publication_pin, EntryPublicationPin) or not callable(
        on_publication_trade
    ):
        raise P90ScopedConsumerBlocked("P90_SCOPED_ENTRY_PUBLICATION_INPUT_INVALID")

    def deliver(event: OrderedNativeTrade) -> None:
        on_publication_trade(event, publication_pin.publication_id)

    return consume_scoped_native_batch(
        connection, source=source, strategy_id=strategy_id,
        strategy_version=strategy_version,
        entry_plan_fingerprint=entry_plan_fingerprint,
        exit_plan_fingerprint=exit_plan_fingerprint,
        consumer_release_commit=consumer_release_commit,
        observed_at=observed_at, max_evidence_age=max_evidence_age,
        on_native_trade=deliver, limit=limit,
        publication_pin=publication_pin,
        max_publication_age=max_publication_age,
    )
