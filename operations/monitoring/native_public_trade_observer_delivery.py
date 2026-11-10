"""PostgreSQL-first native MarketSource -> technical Observer delivery (#470/1).

The existing native producer owns the WebSocket, original journal and source
cursor. This adapter only reads already committed events in provider frame
order and atomically acknowledges technical Observer delivery. No signed data,
Strategy, Exit, Execution, gate or financial rights are present.

A new source session after a GAP is a NEW source epoch. Its checkpoint starts
at (0,0), never inherits the older session's cursor or certifies the gap.
The P90 causal consumer maintains its separate Strategy/Exit checkpoint.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID
from bybit_workbench.universal_entry.p90l90_scoped_native_source import (
    NativeSourceEvidence,
    NativeSourceProofUnavailable,
    OrderedNativeTrade,
    read_ordered_native_events,
    read_scoped_native_source,
    verify_native_producer_code_compatibility,
)

CONSUMER_ID = "UNIVERSAL_ENTRY_OBSERVER_NATIVE_PG_V1"
SOURCE_TTL = timedelta(minutes=2)


class NativeObserverDeliveryBlocked(RuntimeError):
    """Original source or transport delivery is missing; never waive it."""




def validate_technical_native_receipt(event: OrderedNativeTrade) -> None:
    """Validate one immutable source row without any Strategy/Exit dispatch.

    The surrounding consume_native_observer_batch() transaction may ACK the
    dedicated technical transport cursor only AFTER this decoder accepts the
    original provider identity. It cannot imply Strategy/Exit delivery, MFE,
    position protection or permission to open a financial Entry.
    """
    from bybit_workbench.universal_entry.p90l90_scoped_native_codec import (
        original_native_fact,
    )

    try:
        original_native_fact(event)
    except (NativeSourceProofUnavailable, ValueError) as exc:
        raise NativeObserverDeliveryBlocked(
            "NATIVE_OBSERVER_TECHNICAL_ORIGINAL_ROW_UNVERIFIED"
        ) from exc

@dataclass(frozen=True, slots=True)
class NativeObserverDeliveryResult:
    source_session_id: str
    events: tuple[OrderedNativeTrade, ...]
    source_epoch_new: bool


@lru_cache(maxsize=16)
def _verified_producer(
    checkout: Path, producer: str, consumer: str,
) -> frozenset[str]:
    # Git blobs of four immutable producer ABI paths MUST match. An older
    # producer process can remain up through an unrelated runtime release.
    return verify_native_producer_code_compatibility(
        source_checkout=checkout,
        claimed_producer_commit=producer,
        loaded_consumer_commit=consumer,
    )


def active_native_observer_source(
    connection: Any,
    *,
    symbols: tuple[str, ...],
    source_checkout: Path,
    loaded_consumer_commit: str,
    observed_at: datetime,
    max_lag: timedelta = SOURCE_TTL,
) -> NativeSourceEvidence:
    """Only a fresh five-symbol original producer session may be consumed."""
    if (
        not isinstance(source_checkout, Path)
        or not isinstance(loaded_consumer_commit, str)
        or len(loaded_consumer_commit) != 40
        or any(c not in "0123456789abcdef" for c in loaded_consumer_commit)
        or not isinstance(observed_at, datetime)
        or observed_at.utcoffset() is None
    ):
        raise NativeObserverDeliveryBlocked(
            "NATIVE_OBSERVER_SOURCE_CONSUMER_IDENTITY_INVALID"
        )
    sessions = connection.execute(
        """SELECT producer_release_commit
             FROM analytics.native_public_trade_source_sessions
            WHERE market_source_id=%s AND state='ACTIVE'
            ORDER BY subscribed_at DESC LIMIT 2""",
        (BYBIT_PUBLIC_MARKET_SOURCE_ID,),
    ).fetchall()
    if len(sessions) != 1 or not isinstance(sessions[0][0], str):
        raise NativeObserverDeliveryBlocked(
            "NATIVE_OBSERVER_SOURCE_ACTIVE_SESSION_NOT_UNIQUE"
        )
    producer = sessions[0][0]
    try:
        permitted = _verified_producer(
            source_checkout, producer, loaded_consumer_commit,
        )
        source = read_scoped_native_source(
            connection,
            symbols=symbols,
            accepted_producer_commits=permitted,
            observed_at=observed_at,
            max_lag=max_lag,
        )
    except NativeSourceProofUnavailable as exc:
        raise NativeObserverDeliveryBlocked(
            "NATIVE_OBSERVER_SOURCE_PROOF_UNAVAILABLE"
        ) from exc
    if not source.ready:
        reasons = sorted({
            item.reason
            for item in source.by_symbol.values() if not item.ready
        })
        raise NativeObserverDeliveryBlocked(
            "NATIVE_OBSERVER_SOURCE_NOT_READY:" + ",".join(reasons)
        )
    return source


def consume_native_observer_batch(
    connection: Any,
    *,
    source: NativeSourceEvidence,
    observed_at: datetime,
    on_trade: Callable[[OrderedNativeTrade], None],
    limit: int = 64,
    max_lag: timedelta = SOURCE_TTL,
) -> NativeObserverDeliveryResult:
    """Read/callback/checkpoint in ONE PG transaction, no silent skipped row.

    No implicit private P90/Exit recovery or Entry activation is possible;
    callers may only use this in technical source-only delivery until an
    independently approved consumer cutover certifies full Engine parity.
    """
    if (
        not isinstance(source, NativeSourceEvidence)
        or source.ready is not True
        or source.market_source_id != BYBIT_PUBLIC_MARKET_SOURCE_ID
        or not source.session_id
        or not source.producer_release_commit
        or not callable(on_trade)
        or not isinstance(observed_at, datetime)
        or observed_at.utcoffset() is None
        or not isinstance(max_lag, timedelta)
        or not timedelta(0) < max_lag <= timedelta(minutes=5)
        or type(limit) is not int or not 1 <= limit <= 1000
    ):
        raise NativeObserverDeliveryBlocked(
            "NATIVE_OBSERVER_DELIVERY_CONTRACT_INVALID"
        )
    now = observed_at.astimezone(UTC)
    if not timedelta(seconds=-2) <= now-source.checked_at <= max_lag:
        raise NativeObserverDeliveryBlocked(
            "NATIVE_OBSERVER_DELIVERY_SOURCE_PROOF_STALE"
        )
    with connection.transaction():
        # Share-lock source epoch against writer's GAP/rollover transition.
        session = connection.execute(
            """SELECT state,producer_release_commit,ended_at,last_accepted_at
                 FROM analytics.native_public_trade_source_sessions
                WHERE source_session_id=%s AND market_source_id=%s
                FOR SHARE""",
            (source.session_id, source.market_source_id),
        ).fetchone()
        if (
            session is None or session[0] != "ACTIVE"
            or session[1] != source.producer_release_commit
            or session[2] is not None
            or not isinstance(session[3], datetime)
            or session[3].utcoffset() is None
            or not timedelta(seconds=-2)
            <= now-session[3].astimezone(UTC) <= max_lag
        ):
            raise NativeObserverDeliveryBlocked(
                "NATIVE_OBSERVER_DELIVERY_SOURCE_SESSION_MOVED"
            )
        prior = connection.execute(
            """SELECT frame_number,frame_index,producer_release_commit
                 FROM analytics.native_public_trade_observer_delivery
                WHERE consumer_id=%s AND source_session_id=%s
                FOR UPDATE""",
            (CONSUMER_ID, source.session_id),
        ).fetchone()
        new_epoch = prior is None
        if new_epoch:
            # Explicitly a new TRANSPORT source epoch, NOT original P90 Exit
            # replay. First cursor is zero; never skip unseen native rows.
            connection.execute(
                """INSERT INTO analytics.native_public_trade_observer_delivery
                   (consumer_id,source_session_id,producer_release_commit,
                    frame_number,frame_index)
                   VALUES (%s,%s,%s,0,0) ON CONFLICT DO NOTHING""",
                (CONSUMER_ID, source.session_id,
                 source.producer_release_commit),
            )
            prior = connection.execute(
                """SELECT frame_number,frame_index,producer_release_commit
                     FROM analytics.native_public_trade_observer_delivery
                    WHERE consumer_id=%s AND source_session_id=%s
                    FOR UPDATE""",
                (CONSUMER_ID, source.session_id),
            ).fetchone()
        if (
            prior is None
            or type(prior[0]) is not int or prior[0] < 0
            or type(prior[1]) is not int or prior[1] < 0
            or prior[2] != source.producer_release_commit
        ):
            raise NativeObserverDeliveryBlocked(
                "NATIVE_OBSERVER_DELIVERY_CHECKPOINT_CORRUPT"
            )
        frame, index, _ = prior
        try:
            events = read_ordered_native_events(
                connection,
                source=source,
                after_frame=frame,
                after_index=index,
                limit=limit,
            )
        except NativeSourceProofUnavailable as exc:
            raise NativeObserverDeliveryBlocked(
                "NATIVE_OBSERVER_DELIVERY_ORIGINAL_ROW_MISSING"
            ) from exc
        for event in events:
            # Callback and CHECKPOINT are the same transaction. Any failure
            # rolls back BOTH; re-delivery after restart remains safe.
            on_trade(event)
        if events:
            last = events[-1]
            written = connection.execute(
                """UPDATE analytics.native_public_trade_observer_delivery
                      SET frame_number=%s,frame_index=%s,last_exec_id=%s,
                          updated_at=clock_timestamp()
                    WHERE consumer_id=%s AND source_session_id=%s
                      AND producer_release_commit=%s
                      AND frame_number=%s AND frame_index=%s""",
                (last.frame_number, last.frame_index, last.exec_id,
                 CONSUMER_ID, source.session_id, source.producer_release_commit,
                 frame, index),
            )
            if written.rowcount != 1:
                raise NativeObserverDeliveryBlocked(
                    "NATIVE_OBSERVER_DELIVERY_CONCURRENT_CURSOR_CHANGE"
                )
        return NativeObserverDeliveryResult(
            source_session_id=source.session_id,
            events=events,
            source_epoch_new=new_epoch,
        )
