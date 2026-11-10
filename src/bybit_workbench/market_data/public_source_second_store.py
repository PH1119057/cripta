"""Small, event-driven per-second market-source facts (not raw market-data lake).

One authorized MarketSource producer owns capture and calls this writer.
Raw native P90 trades are retained by their separate exact journal. This
read-model is lower fidelity and MUST NOT be used as P90 MFE/native evidence.
No consumer, Strategy, account or Exchange authorization can be inferred.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from bybit_workbench.market_data.public_source_event_codec import (
    OrderbookContinuity,
    PublicSourceFact,
    PublicSourceFrameInvalid,
)
from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID

_KINDS = frozenset(("TRADE", "TICKER", "BOOK", "LIQUIDATION"))


class PublicSourceSecondUnavailable(RuntimeError):
    """One-second feed identity, coverage or transaction not established."""


class PublicSecondAccumulator:
    """One live producer epoch with explicit reconnect invalidation.

    In-memory aggregation is intentionally not a substitute for the exact
    durable native journal: a process crash loses only uncommitted/recomputable
    seconds and creates a new marked source epoch. No false continuity.
    """

    def __init__(self, *, session: str, max_pending_seconds: int = 3) -> None:
        if len(session) != 32 or not all(c in "0123456789abcdef" for c in session):
            raise PublicSourceSecondUnavailable("PUBLIC_FEED_EPOCH_INVALID")
        if max_pending_seconds not in (2, 3, 4):
            raise PublicSourceSecondUnavailable("PUBLIC_FEED_BUFFER_BOUNDS_INVALID")
        self.session = session
        self._books: dict[str, OrderbookContinuity] = {}
        self._ticker: dict[str, dict[str, str]] = {}
        self._ticker_seen: dict[str, dict[str, str]] = {}
        self._pending: dict[tuple[str, datetime], dict[str, Any]] = {}
        self._processed_trade_ids: dict[datetime, set[tuple[str, int, str]]] = defaultdict(set)
        self._max_pending = max_pending_seconds
        self._flushed_before: datetime | None = None

    @staticmethod
    def _second(at: datetime) -> datetime:
        if at.tzinfo is None or at.utcoffset() != timedelta(0):
            raise PublicSourceSecondUnavailable("PUBLIC_FEED_RECEIVER_NOT_UTC")
        return at.replace(microsecond=0)

    def invalidate_market(self, instrument_id: str) -> None:
        """Transport reconnect invalidates book and ticker; never stale READY."""
        self._books.pop(instrument_id, None)
        self._ticker.pop(instrument_id, None)
        self._ticker_seen.pop(instrument_id, None)

    def accept(self, event: PublicSourceFact, *, received_at: datetime) -> None:
        second = self._second(received_at)
        if (
            event.market_source_id != BYBIT_PUBLIC_MARKET_SOURCE_ID
            or event.kind not in _KINDS
            or event.source_time > received_at + timedelta(seconds=5)
        ):
            raise PublicSourceSecondUnavailable("PUBLIC_FEED_IDENTITY_INVALID")
        if self._flushed_before is not None and second <= self._flushed_before:
            raise PublicSourceSecondUnavailable("PUBLIC_FEED_LATE_RECEIVE_SECOND")
        if self._pending and second < min(t for _, t in self._pending):
            raise PublicSourceSecondUnavailable("PUBLIC_FEED_RECEIVE_REGRESSION")
        if len({t for _, t in self._pending} | {second}) > self._max_pending:
            raise PublicSourceSecondUnavailable("PUBLIC_FEED_BACKPRESSURE_UNVERIFIED")

        key = (event.instrument_id, second)
        if key not in self._pending:
            self._pending[key] = {
                "market_source_id": event.market_source_id,
                "venue_id": "venue-bybit",
                "instrument_id": event.instrument_id,
                "product": event.product,
                "symbol": event.symbol,
                "second": second,
                "source_session_id": self.session,
                "trade_buy_usd": Decimal(0),
                "trade_sell_usd": Decimal(0),
                "trade_count": 0,
                "liquidation_buy_usd": Decimal(0),
                "liquidation_sell_usd": Decimal(0),
                "liquidation_count": 0,
                "ticker": None,
                "ticker_field_times": None,
                "book": None,
                "original_time_min": event.source_time,
                "original_time_max": event.source_time,
            }
        row = self._pending[key]
        if row["product"] != event.product or row["symbol"] != event.symbol:
            raise PublicSourceSecondUnavailable("PUBLIC_FEED_INSTRUMENT_CONFLICT")
        row["original_time_min"] = min(row["original_time_min"], event.source_time)
        row["original_time_max"] = max(row["original_time_max"], event.source_time)

        if event.kind == "TRADE":
            if event.exchange_sequence is None or not event.exec_id:
                raise PublicSourceFrameInvalid("PUBLIC_TRADE_NATIVE_ID_REQUIRED")
            duplicate = (event.instrument_id, event.exchange_sequence, event.exec_id)
            if duplicate in self._processed_trade_ids[second]:
                return
            self._processed_trade_ids[second].add(duplicate)
            price = Decimal(str(event.payload["price"]))
            qty = Decimal(str(event.payload["qty"]))
            usd = price * qty
            if event.payload["side"] == "Buy":
                row["trade_buy_usd"] += usd
            else:
                row["trade_sell_usd"] += usd
            row["trade_count"] += 1
        elif event.kind == "LIQUIDATION":
            price = Decimal(str(event.payload["price"]))
            qty = Decimal(str(event.payload["qty"]))
            field = (
                "liquidation_buy_usd" if event.payload["side"] == "Buy"
                else "liquidation_sell_usd"
            )
            row[field] += price * qty
            row["liquidation_count"] += 1
        elif event.kind == "TICKER":
            ticker = self._ticker.setdefault(event.instrument_id, {})
            seen = self._ticker_seen.setdefault(event.instrument_id, {})
            for name, value in event.payload.items():
                ticker[name] = str(value)
                seen[name] = event.source_time.isoformat()
            row["ticker"] = dict(ticker)
            row["ticker_field_times"] = dict(seen)
        else:
            book = self._books.setdefault(event.instrument_id, OrderbookContinuity())
            row["book"] = dict(book.apply(event))

    def ready_seconds(self, *, before: datetime) -> tuple[dict[str, Any], ...]:
        """Peek frozen second rows, do not drop on failed DB transaction."""
        boundary = self._second(before)
        return tuple(
            dict(v) for (instrument, t), v in sorted(
                self._pending.items(), key=lambda item: (item[0][1], item[0][0]),
            ) if t < boundary
        )

    def mark_committed(self, rows: tuple[dict[str, Any], ...]) -> None:
        if not rows:
            return
        last: datetime | None = None
        for row in rows:
            key = (str(row["instrument_id"]), row["second"])
            if key not in self._pending:
                raise PublicSourceSecondUnavailable("PUBLIC_FEED_COMMIT_MISMATCH")
            del self._pending[key]
            at = row["second"]
            last = at if last is None or at > last else last
        if last is not None:
            self._flushed_before = last
            for sec in list(self._processed_trade_ids):
                if sec <= last:
                    del self._processed_trade_ids[sec]


def persist_compact_source_seconds(
    connection: Any, *, rows: tuple[dict[str, Any], ...],
    producer_release_commit: str,
) -> int:
    """Atomic durable compact seconds, one canonical writer, immutable row IDs.

    Only finalized seconds are inserted; if a conflict exists it must be
    independently equivalent or the transaction fails. Storage excludes full
    orderbook tape, full native tape, independent Strategy snapshots.
    """
    import re

    if not re.fullmatch(r"[a-f0-9]{40}", producer_release_commit):
        raise PublicSourceSecondUnavailable("PUBLIC_FEED_RELEASE_INVALID")
    if not rows:
        return 0

    from psycopg.types.json import Jsonb  # type: ignore[import-not-found]

    # A compact market row belongs to exactly one verified ACTIVE producer
    # epoch. Never use a source session from another release/venue or mix
    # transport epochs in a single durability transaction.
    sessions = {item.get("source_session_id") for item in rows}
    if len(sessions) != 1 or any(
        item.get("market_source_id") != BYBIT_PUBLIC_MARKET_SOURCE_ID
        or item.get("venue_id") != "venue-bybit"
        for item in rows
    ):
        raise PublicSourceSecondUnavailable("PUBLIC_FEED_EPOCH_SOURCE_MISMATCH")
    session = next(iter(sessions))
    inserted = 0
    with connection.transaction():
        source_epoch = connection.execute(
            """SELECT state,market_source_id,producer_release_commit
                 FROM analytics.native_public_trade_source_sessions
                WHERE source_session_id=%s""",
            (session,),
        ).fetchone()
        if source_epoch != (
            "ACTIVE", BYBIT_PUBLIC_MARKET_SOURCE_ID, producer_release_commit,
        ):
            raise PublicSourceSecondUnavailable(
                "PUBLIC_FEED_SOURCE_EPOCH_NOT_ACTIVE"
            )
        for item in rows:
            for name in ("market_source_id", "venue_id", "instrument_id", "product",
                         "symbol", "second", "source_session_id",
                         "original_time_min", "original_time_max"):
                if name not in item:
                    raise PublicSourceSecondUnavailable(
                        "PUBLIC_FEED_ROW_REQUIRED_FIELD_MISSING"
                    )
            if item["market_source_id"] != BYBIT_PUBLIC_MARKET_SOURCE_ID:
                raise PublicSourceSecondUnavailable("PUBLIC_FEED_SOURCE_MISMATCH")
            data = {
                name: str(item[name])
                for name in (
                    "trade_buy_usd", "trade_sell_usd", "trade_count",
                    "liquidation_buy_usd", "liquidation_sell_usd",
                    "liquidation_count",
                )
            }
            if item["ticker"] is not None:
                data["ticker"] = item["ticker"]
                data["ticker_field_times"] = item["ticker_field_times"]
            if item["book"] is not None:
                data["book"] = item["book"]
            values = (
                item["market_source_id"], item["venue_id"], item["source_session_id"],
                item["instrument_id"], item["product"], item["symbol"],
                item["second"], item["original_time_min"],
                item["original_time_max"], Jsonb(data), producer_release_commit,
            )
            new_row = connection.execute(
                """INSERT INTO analytics.public_market_source_seconds
                   (market_source_id,venue_id,source_session_id,instrument_id,
                    product_category,external_symbol,observed_second,
                    source_time_min,source_time_max,compact_features,
                    producer_release_commit)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (source_session_id,instrument_id,observed_second)
                   DO NOTHING RETURNING observed_second""",
                values,
            ).fetchone()
            if new_row is not None:
                inserted += 1
            else:
                before = connection.execute(
                    """SELECT compact_features,source_time_min,source_time_max
                         FROM analytics.public_market_source_seconds
                        WHERE source_session_id=%s AND instrument_id=%s
                          AND observed_second=%s""",
                    (item["source_session_id"], item["instrument_id"],
                     item["second"]),
                ).fetchone()
                if before != (data, item["original_time_min"],
                              item["original_time_max"]):
                    raise PublicSourceSecondUnavailable(
                        "PUBLIC_FEED_COMPACT_SECOND_CONFLICT"
                    )
    return inserted
