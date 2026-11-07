"""Shared immutable Bybit native publicTrade evidence boundary (#470).

This module is intentionally *not* a Strategy, a second MarketSource producer,
a replacement for the existing P90 same-sequence dedup sidecar or an Exit owner.
The approved producer calls append() *inside the existing source transaction*
BEFORE acknowledging a native event or committing the dependent causal cursor.

Neither append() nor this module commits, rolls back, retries, deletes data,
generates a missing sequence ID or grants financial Entry permission.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

_SHA = re.compile(r"^[0-9a-f]{40}$")


class NativeTradeEvidenceConflict(RuntimeError):
    """One exact provider event identity was observed with different facts."""


@dataclass(frozen=True, slots=True)
class NativeTradeEvidence:
    market_source_id: str
    instrument_id: str
    external_symbol: str
    exchange_sequence: int
    exec_id: str
    event_at: datetime
    price: Decimal
    quantity: Decimal
    taker_side: str
    source_transport: str
    producer_release_commit: str

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and 0 < len(value) <= limit
            for value, limit in (
                (self.market_source_id, 128),
                (self.instrument_id, 128),
                (self.external_symbol, 64),
                (self.exec_id, 128),
                (self.source_transport, 128),
            )
        ):
            raise ValueError("NATIVE_TRADE_EVIDENCE_IDENTITY_INVALID")
        if type(self.exchange_sequence) is not int or self.exchange_sequence < 0:
            raise ValueError("NATIVE_TRADE_EVIDENCE_SEQUENCE_INVALID")
        if (
            not isinstance(self.event_at, datetime)
            or self.event_at.tzinfo is None
            or self.event_at.utcoffset() is None
        ):
            raise ValueError("NATIVE_TRADE_EVIDENCE_TIME_INVALID")
        if (
            not isinstance(self.price, Decimal)
            or not self.price.is_finite()
            or self.price <= 0
            or not isinstance(self.quantity, Decimal)
            or not self.quantity.is_finite()
            or self.quantity <= 0
        ):
            raise ValueError("NATIVE_TRADE_EVIDENCE_AMOUNT_INVALID")
        if self.taker_side not in ("Buy", "Sell"):
            raise ValueError("NATIVE_TRADE_EVIDENCE_SIDE_INVALID")
        if not isinstance(self.producer_release_commit, str) or not _SHA.fullmatch(
            self.producer_release_commit
        ):
            raise ValueError("NATIVE_TRADE_EVIDENCE_PRODUCER_INVALID")


class NativePublicTradeJournal:
    """Minimal append-only physical owner port; caller owns atomic checkpoint.

    The same database connection and outer transaction MUST commit this row,
    subsequent dependent Entry/Exit/MFE effects and their processing cursor.
    An insert without a corresponding cursor update is not an acknowledged
    source event. A duplicate is accepted only when immutable facts agree.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def append(self, item: NativeTradeEvidence) -> bool:
        if not isinstance(item, NativeTradeEvidence):
            raise TypeError("NATIVE_TRADE_EVIDENCE_REQUIRED")
        # PostgreSQL's current transaction is caller-owned. An autocommit
        # connection with no open transaction is forbidden: it would allow
        # journal COMMIT independently from causal processing/Exit.
        info = getattr(self._connection, "info", None)
        status = getattr(info, "transaction_status", None)
        if str(getattr(status, "name", status)) != "INTRANS":
            raise RuntimeError("NATIVE_TRADE_EVIDENCE_ATOMIC_SOURCE_TX_REQUIRED")

        key = (
            item.market_source_id,
            item.instrument_id,
            item.exchange_sequence,
            item.exec_id,
        )
        immutable = (
            item.external_symbol,
            item.event_at.astimezone(UTC),
            item.price,
            item.quantity,
            item.taker_side,
        )
        inserted = self._connection.execute(
            """INSERT INTO analytics.native_public_trade_events
                    (market_source_id,instrument_id,exchange_sequence,exec_id,
                     external_symbol,event_at,price,quantity,taker_side,
                     source_transport,producer_release_commit)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                 ON CONFLICT DO NOTHING RETURNING exec_id""",
            (*key, *immutable, item.source_transport, item.producer_release_commit),
        ).fetchone()
        if inserted == (item.exec_id,):
            return True
        # Exact replay across WS/restart/approved archive is idempotent.
        # Transport and writer SHA are first-observation provenance and need
        # not match replay delivery, but native identity/semantics MUST match.
        previous = self._connection.execute(
            """SELECT external_symbol,event_at,price,quantity,taker_side
                 FROM analytics.native_public_trade_events
                 WHERE market_source_id=%s AND instrument_id=%s
                   AND exchange_sequence=%s AND exec_id=%s""",
            key,
        ).fetchone()
        if previous != immutable:
            raise NativeTradeEvidenceConflict("NATIVE_PUBLIC_TRADE_IDENTITY_CONFLICT")
        return False


class NativeTradeEvidenceUnavailable(RuntimeError):
    """No exact saved-native range: never replace with candle approximation."""


@dataclass(frozen=True, slots=True)
class NativeTradeCandidateSlice:
    """Authentic persisted rows, NOT a certificate of uninterrupted coverage.

    The producer's independently attested WS-session or approved archive
    coverage is required before any caller may replay these rows as a
    complete causal interval for an OPEN position. In particular, this
    result does not authorize Entry, Exit, MFE or checkpoint advancement.
    """

    events: tuple[NativeTradeEvidence, ...]
    first_sequence: int
    last_sequence: int

    @property
    def coverage_proven(self) -> bool:
        # This object cannot be constructed with a spoofed 'verified' bit.
        # Only an independent original-WS / exact archive certificate can
        # prove all intervening native ticks, never this row sample alone.
        return False


def read_native_candidates(
    connection: Any,
    *,
    market_source_id: str,
    instrument_id: str,
    first_sequence: int,
    first_exec_ids: frozenset[str],
    last_sequence: int,
    last_exec_id: str,
    limit: int = 25_000,
) -> NativeTradeCandidateSlice:
    """Read exact saved IDs inside a bounded native sequence interval.

    This is a PostgreSQL-first *evidence reader*, not a replay attestation.
    Native sequence numbers need not be contiguous. Neither a sequence
    range nor presence of both endpoints proves that all intervening
    WebSocket trades were captured while the process was offline.
    """
    if (
        not isinstance(market_source_id, str) or not market_source_id
        or not isinstance(instrument_id, str) or not instrument_id
        or type(first_sequence) is not int or first_sequence < 0
        or type(last_sequence) is not int or last_sequence < first_sequence
        or not isinstance(first_exec_ids, frozenset) or not first_exec_ids
        or any(not isinstance(x, str) or not x for x in first_exec_ids)
        or not isinstance(last_exec_id, str) or not last_exec_id
        or type(limit) is not int or not 2 <= limit <= 25_000
    ):
        raise ValueError("NATIVE_PUBLIC_TRADE_READ_BOUNDARY_INVALID")

    rows = connection.execute(
        """SELECT market_source_id,instrument_id,external_symbol,
                  exchange_sequence,exec_id,event_at,price,quantity,
                  taker_side,source_transport,producer_release_commit
             FROM analytics.native_public_trade_events
            WHERE market_source_id=%s AND instrument_id=%s
              AND exchange_sequence BETWEEN %s AND %s
            ORDER BY exchange_sequence,event_at,exec_id
            LIMIT %s""",
        (market_source_id, instrument_id, first_sequence, last_sequence, limit + 1),
    ).fetchall()
    if len(rows) > limit:
        raise NativeTradeEvidenceUnavailable(
            "NATIVE_PUBLIC_TRADE_RANGE_EXCEEDS_BOUNDED_READ"
        )
    if not rows:
        raise NativeTradeEvidenceUnavailable(
            "NATIVE_PUBLIC_TRADE_PERSISTED_RANGE_EMPTY"
        )
    events = tuple(NativeTradeEvidence(*row) for row in rows)
    if not first_exec_ids.issubset(
        {e.exec_id for e in events if e.exchange_sequence == first_sequence}
    ):
        raise NativeTradeEvidenceUnavailable(
            "NATIVE_PUBLIC_TRADE_FIRST_SAME_SEQUENCE_IDS_MISSING"
        )
    if last_exec_id not in {
        e.exec_id for e in events if e.exchange_sequence == last_sequence
    }:
        raise NativeTradeEvidenceUnavailable(
            "NATIVE_PUBLIC_TRADE_LAST_CURSOR_MISSING"
        )
    if any(
        previous.event_at > current.event_at
        for previous, current in zip(events, events[1:])
    ):
        raise NativeTradeEvidenceConflict(
            "NATIVE_PUBLIC_TRADE_SAVED_ORDER_CONFLICT"
        )
    return NativeTradeCandidateSlice(
        events=events,
        first_sequence=first_sequence,
        last_sequence=last_sequence,
    )
