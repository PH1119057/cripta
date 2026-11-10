"""Source-owned *forming* one-minute Bybit evidence, never closed OHLCV.

Only the existing Native MarketSource's PostgreSQL actor can persist a
provider-confirmed FALSE kline frame. The latest forming observation may
be referenced by a shared publication but is NEVER a closed candle, trade
execution, MFE or permission to open/close a real position.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from bybit_workbench.market_data.public_source_event_codec import (
    PublicSourceFact,
)
from bybit_workbench.market_data.source_required_feed_manifest import (
    RequiredPublicFeed,
)
from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID

_COMMIT = re.compile(r"^[a-f0-9]{40}$")
_SESSION = re.compile(r"^[a-f0-9]{32}$")
_VALUES = ("open", "high", "low", "close", "volume", "turnover")


class FormingMinuteUnverified(RuntimeError):
    """Forming public kline was not proven under the exact active source epoch."""


def _utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise FormingMinuteUnverified("PUBLIC_FORMING_TIME_INVALID")
    try:
        at = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FormingMinuteUnverified("PUBLIC_FORMING_TIME_INVALID") from exc
    if at.tzinfo is None or at.utcoffset() != timedelta(0):
        raise FormingMinuteUnverified("PUBLIC_FORMING_TIME_NOT_UTC")
    return at


def _validated_forming(
    fact: PublicSourceFact, *, received_at: datetime,
) -> tuple[datetime, datetime, datetime, dict[str, str]]:
    p = fact.payload
    if (
        fact.market_source_id != BYBIT_PUBLIC_MARKET_SOURCE_ID
        or fact.kind != "FORMING_KLINE"
        or fact.product != "LINEAR"
        or p.get("timeframe") != "1"
        or p.get("confirmed") is not False
        or received_at.tzinfo is None
        or received_at.utcoffset() != timedelta(0)
    ):
        raise FormingMinuteUnverified("PUBLIC_FORMING_SOURCE_CATEGORY_INVALID")
    start = _utc(p.get("opened_at"))
    end = _utc(p.get("closed_at"))
    match = _utc(p.get("provider_last_match_at"))
    when = fact.source_time
    if (
        when.tzinfo is None or when.utcoffset() != timedelta(0)
        or start != start.replace(second=0, microsecond=0)
        or end - start != timedelta(minutes=1)
        or not start <= when < end
        or not start <= match < end
        or match > when + timedelta(seconds=5)
        or when > received_at + timedelta(seconds=5)
    ):
        raise FormingMinuteUnverified("PUBLIC_FORMING_CAUSAL_BOUNDARY_INVALID")
    values: dict[str, str] = {}
    for key in _VALUES:
        raw = p.get(key)
        if not isinstance(raw, str):
            raise FormingMinuteUnverified("PUBLIC_FORMING_OHLCV_INVALID")
        try:
            num = Decimal(raw)
        except InvalidOperation as exc:
            raise FormingMinuteUnverified("PUBLIC_FORMING_OHLCV_INVALID") from exc
        if not num.is_finite() or num < 0 or (
            num == 0 and key not in ("volume", "turnover")
        ):
            raise FormingMinuteUnverified("PUBLIC_FORMING_OHLCV_INVALID")
        values[key] = str(num)
    low, high, opn, cls = (
        Decimal(values[key]) for key in ("low", "high", "open", "close")
    )
    if low > high or not low <= opn <= high or not low <= cls <= high:
        raise FormingMinuteUnverified("PUBLIC_FORMING_PRICE_BOUNDS_INVALID")
    return start, end, match, values


def persist_forming_minute_fact(
    connection: Any, *, source_session_id: str,
    producer_release_commit: str,
    fact: PublicSourceFact, received_at: datetime,
) -> int:
    """One verified original provider frame; no update/delete or fake close."""
    if (
        _COMMIT.fullmatch(producer_release_commit) is None
        or _SESSION.fullmatch(source_session_id) is None
    ):
        raise FormingMinuteUnverified("PUBLIC_FORMING_SOURCE_EPOCH_INVALID")
    opened, closed, last_match, values = _validated_forming(
        fact, received_at=received_at,
    )
    from psycopg.types.json import Jsonb  # type: ignore[import-not-found]

    with connection.transaction():
        epoch = connection.execute(
            """SELECT state,market_source_id,producer_release_commit
                 FROM analytics.native_public_trade_source_sessions
                WHERE source_session_id=%s""",
            (source_session_id,),
        ).fetchone()
        if epoch != (
            "ACTIVE", BYBIT_PUBLIC_MARKET_SOURCE_ID, producer_release_commit,
        ):
            raise FormingMinuteUnverified("PUBLIC_FORMING_EPOCH_NOT_ACTIVE")
        row = connection.execute(
            """INSERT INTO analytics.public_forming_minute_observations
               (market_source_id,venue_id,source_session_id,instrument_id,
                product_category,external_symbol,timeframe,opened_at,
                closed_at,source_at,provider_last_match_at,received_at,
                forming_ohlcv,producer_release_commit)
               VALUES (%s,%s,%s,%s,%s,%s,'1',%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (source_session_id,instrument_id,opened_at,source_at)
               DO NOTHING RETURNING forming_observation_id""",
            (
                fact.market_source_id, "venue-bybit", source_session_id,
                fact.instrument_id, fact.product, fact.symbol,
                opened, closed, fact.source_time, last_match, received_at,
                Jsonb(values), producer_release_commit,
            ),
        ).fetchone()
        if row is not None:
            return int(row[0])
        previous = connection.execute(
            """SELECT forming_observation_id,forming_ohlcv,closed_at,
                      provider_last_match_at,product_category,external_symbol,
                      producer_release_commit
                 FROM analytics.public_forming_minute_observations
                WHERE source_session_id=%s AND instrument_id=%s
                  AND opened_at=%s AND source_at=%s""",
            (
                source_session_id, fact.instrument_id,
                opened, fact.source_time,
            ),
        ).fetchone()
        if previous is None or previous[1:] != (
            values, closed, last_match, fact.product, fact.symbol,
            producer_release_commit,
        ):
            raise FormingMinuteUnverified("PUBLIC_FORMING_DUPLICATE_CONFLICT")
        return int(previous[0])


def collect_forming_minute_references(
    connection: Any, *, source_session_id: str,
    as_of_second: datetime,
    requirements: Iterable[RequiredPublicFeed],
) -> tuple[
    dict[str, dict[str, dict[str, object]]],
    dict[str, dict[str, dict[str, object]]],
]:
    """Reference latest matching open minute in the same epoch, not a closure."""
    if (
        as_of_second.tzinfo is None
        or as_of_second.utcoffset() != timedelta(0)
        or as_of_second.microsecond != 0
        or _SESSION.fullmatch(source_session_id) is None
    ):
        raise FormingMinuteUnverified("PUBLIC_FORMING_PUBLICATION_CLOCK_INVALID")
    grouped: dict[str, RequiredPublicFeed] = {}
    for needed in requirements:
        if (
            needed.dataset != "FORMING_KLINE"
            or needed.product != "LINEAR"
            or needed.timeframe != "1"
        ):
            raise FormingMinuteUnverified("PUBLIC_FORMING_MANIFEST_NOT_SUPPORTED")
        old = grouped.get(needed.instrument_id)
        if old is None or needed.freshness_seconds < old.freshness_seconds:
            grouped[needed.instrument_id] = needed
    if not grouped:
        return {}, {}
    if len(grouped) > 40:
        raise FormingMinuteUnverified("PUBLIC_FORMING_SCOPE_EXCEEDED")
    instruments = sorted(grouped)
    horizon = max(v.freshness_seconds for v in grouped.values())
    rows = connection.execute(
        """SELECT DISTINCT ON (instrument_id)
                  forming_observation_id,instrument_id,product_category,
                  external_symbol,timeframe,opened_at,closed_at,source_at,
                  received_at,forming_ohlcv
             FROM analytics.public_forming_minute_observations
            WHERE source_session_id=%s AND instrument_id=ANY(%s)
              AND source_at < %s AND received_at < %s
              AND source_at >= %s
              AND opened_at <= %s AND closed_at > %s
            ORDER BY instrument_id,source_at DESC""",
        (
            source_session_id, instruments, as_of_second, as_of_second,
            as_of_second - timedelta(seconds=horizon),
            as_of_second, as_of_second,
        ),
    ).fetchall()
    found = {str(row[1]): row for row in rows}
    refs: dict[str, dict[str, dict[str, object]]] = {}
    quality: dict[str, dict[str, dict[str, object]]] = {}
    for instrument, needed in grouped.items():
        row = found.get(instrument)
        if row is None:
            quality.setdefault(instrument, {})["FORMING_KLINE"] = {
                "status": "MISSING_OR_STALE",
                "required": needed.required,
                "freshness_seconds": needed.freshness_seconds,
            }
            continue
        uid, inst, product, sym, tf, opened, closed, source_at, receipt, values = row
        if (
            inst != instrument or product != needed.product
            or sym != needed.symbol or tf != "1"
            or not isinstance(values, dict)
            or opened > as_of_second or closed <= as_of_second
            or source_at < opened or source_at >= closed
            or source_at > receipt + timedelta(seconds=5)
        ):
            raise FormingMinuteUnverified("PUBLIC_FORMING_REFERENCE_CONFLICT")
        age = (as_of_second - source_at).total_seconds()
        if age > needed.freshness_seconds:
            quality.setdefault(instrument, {})["FORMING_KLINE"] = {
                "status": "STALE", "age_seconds": age,
                "required": needed.required,
                "freshness_seconds": needed.freshness_seconds,
            }
            continue
        quality.setdefault(instrument, {})["FORMING_KLINE"] = {
            "status": "UNCONFIRMED_OPEN_MINUTE",
            "age_seconds": age,
            "required": needed.required,
            "freshness_seconds": needed.freshness_seconds,
        }
        refs.setdefault(instrument, {})["FORMING_KLINE"] = {
            "forming_observation_id": int(uid),
            "source_session_id": source_session_id,
            "instrument_id": instrument,
            "product_category": product,
            "external_symbol": sym,
            "timeframe": "1",
            "opened_at": opened.isoformat(),
            "closed_at": closed.isoformat(),
            "source_at": source_at.isoformat(),
        }
    return refs, quality
