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
        if getattr(self._connection, "autocommit", False) and str(
            getattr(status, "name", status)
        ) != "INTRANS":
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
