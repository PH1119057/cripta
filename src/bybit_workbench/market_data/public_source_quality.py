"""Append-only quality evidence for internal R04 MarketSource transport scopes.

Only the single existing native PG writer may persist these events. The
quality log does NOT grant any Strategy/Entry/Exit readiness: a new transport
connection never proves missing historical native continuity or a prior GAP.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    bybit_instrument_id,
)

Quality = Literal[
    "GAP", "NO_DATA", "STALE", "NOT_LISTED", "UNKNOWN", "RECOVERING",
]
_ID = re.compile(r"^[a-f0-9]{32}$")
_RELEASE = re.compile(r"^[a-f0-9]{40}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{2,32}$")
_TOPIC = re.compile(r"^[A-Za-z0-9_./:*-]{1,128}$")
_REASON = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")
_QUALITY = frozenset(
    ("GAP", "NO_DATA", "STALE", "NOT_LISTED", "UNKNOWN", "RECOVERING")
)


class PublicSourceQualityInvalid(RuntimeError):
    """Exact producer, transport scope, or durable quality claim invalid."""


@dataclass(frozen=True, slots=True)
class PublicSourceQualityEvent:
    event_id: str
    source_session_id: str
    transport_epoch_id: str
    product: str
    topic: str
    symbol: str
    quality: Quality
    reason: str
    observed_at: datetime

    @classmethod
    def create(
        cls, *,
        source_session_id: str,
        transport_epoch_id: str,
        product: str,
        topic: str,
        symbol: str = "",
        quality: Quality,
        reason: str,
        observed_at: datetime,
    ) -> "PublicSourceQualityEvent":
        event = cls(
            event_id=uuid4().hex,
            source_session_id=source_session_id,
            transport_epoch_id=transport_epoch_id,
            product=product, topic=topic,
            symbol=symbol, quality=quality,
            reason=reason, observed_at=observed_at,
        )
        event.validate()
        return event

    def validate(self) -> None:
        if (
            not _ID.fullmatch(self.event_id)
            or not _ID.fullmatch(self.source_session_id)
            or not _ID.fullmatch(self.transport_epoch_id)
            or self.product not in ("LINEAR", "SPOT")
            or not _TOPIC.fullmatch(self.topic)
            or not (
                self.symbol == "" or _SYMBOL.fullmatch(self.symbol)
            )
            or (self.topic == "*") != (self.symbol == "")
            or self.quality not in _QUALITY
            or not _REASON.fullmatch(self.reason)
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() != timedelta(0)
        ):
            raise PublicSourceQualityInvalid("PUBLIC_SOURCE_QUALITY_EVENT_INVALID")
        if self.topic != "*":
            # Exact product/topic/instrument identity; a scoped quality fault
            # cannot be falsely attributed to an unrelated symbol or dataset.
            ws = (
                f"publicTrade.{self.symbol}",
                f"orderbook.50.{self.symbol}",
                *(f"kline.{interval}.{self.symbol}"
                  for interval in ("1", "5", "15", "60")),
            )
            if self.product == "LINEAR":
                ws += (
                    f"tickers.{self.symbol}",
                    f"allLiquidation.{self.symbol}",
                    f"REST:market/account-ratio:{self.symbol}",
                )
            allowed = ws + (
                f"REST:market/instruments-info:{self.symbol}",
            )
            if self.topic not in allowed:
                raise PublicSourceQualityInvalid(
                    "PUBLIC_SOURCE_QUALITY_TOPIC_SCOPE_INVALID"
                )
        if self.quality == "NOT_LISTED" and self.topic != (
            f"REST:market/instruments-info:{self.symbol}"
        ):
            raise PublicSourceQualityInvalid(
                "PUBLIC_SOURCE_NOT_LISTED_PROOF_INVALID"
            )

    @property
    def instrument_id(self) -> str | None:
        if not self.symbol:
            return None
        return bybit_instrument_id(self.product.lower(), self.symbol)


def persist_public_source_quality_events(
    connection: Any, *, events: tuple[PublicSourceQualityEvent, ...],
    producer_release_commit: str,
) -> int:
    """One append-only transaction, exact ACTIVE native session and release.

    Duplicate event IDs are idempotent ONLY if all persisted fields match.
    Source/quality events cannot update, delete or repair original GAP rows.
    """
    if not events:
        return 0
    if not _RELEASE.fullmatch(producer_release_commit) or len(events) > 256:
        raise PublicSourceQualityInvalid("PUBLIC_SOURCE_QUALITY_BATCH_INVALID")
    for event in events:
        event.validate()
    session = events[0].source_session_id
    if any(e.source_session_id != session for e in events):
        raise PublicSourceQualityInvalid("PUBLIC_SOURCE_QUALITY_MIXED_SESSION")

    inserted = 0
    with connection.transaction():
        epoch = connection.execute(
            """SELECT state, market_source_id, producer_release_commit
                 FROM analytics.native_public_trade_source_sessions
                WHERE source_session_id=%s""",
            (session,),
        ).fetchone()
        if epoch != (
            "ACTIVE", BYBIT_PUBLIC_MARKET_SOURCE_ID, producer_release_commit,
        ):
            raise PublicSourceQualityInvalid(
                "PUBLIC_SOURCE_QUALITY_EPOCH_NOT_ACTIVE"
            )
        for event in events:
            row = (
                event.event_id, BYBIT_PUBLIC_MARKET_SOURCE_ID, "venue-bybit",
                session, event.transport_epoch_id, event.product,
                event.topic, event.symbol, event.instrument_id,
                event.quality, event.reason, event.observed_at,
                producer_release_commit,
            )
            saved = connection.execute(
                """INSERT INTO analytics.public_market_source_quality_events
                   (quality_event_id, market_source_id, venue_id,
                    source_session_id, transport_epoch_id, product_category,
                    dataset_topic, external_symbol, instrument_id,
                    quality_state, reason, observed_at, producer_release_commit)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (quality_event_id) DO NOTHING
                   RETURNING quality_event_id""",
                row,
            ).fetchone()
            if saved is not None:
                inserted += 1
            else:
                existing = connection.execute(
                    """SELECT quality_event_id, market_source_id, venue_id,
                              source_session_id, transport_epoch_id,
                              product_category, dataset_topic, external_symbol,
                              instrument_id, quality_state, reason,
                              observed_at, producer_release_commit
                         FROM analytics.public_market_source_quality_events
                        WHERE quality_event_id=%s""",
                    (event.event_id,),
                ).fetchone()
                if existing != row:
                    raise PublicSourceQualityInvalid(
                        "PUBLIC_SOURCE_QUALITY_EVENT_CONFLICT"
                    )
    return inserted
