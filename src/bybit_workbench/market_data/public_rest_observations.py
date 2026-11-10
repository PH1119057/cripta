"""Append-only public REST evidence and provenance links for MarketPublication.

The one Native MarketSource process is the only DB writer. Public REST
market observations are not signed account/order/position evidence.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from bybit_workbench.market_data.public_market_rest_source import (
    PublicMarketRestFact,
)
from bybit_workbench.market_data.source_required_feed_manifest import (
    RequiredPublicFeed,
)
from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID

_COMMIT = re.compile(r"^[a-f0-9]{40}$")
_REST_DATASETS = frozenset(("ACCOUNT_RATIO", "INSTRUMENT_INFO"))


class PublicRestEvidenceUnverified(RuntimeError):
    """Invalid source epoch, stale observation or conflicting original fact."""


def persist_public_rest_fact(
    connection: Any, *, source_session_id: str,
    producer_release_commit: str, fact: PublicMarketRestFact,
) -> int:
    """Commit original validated REST observation under the active source epoch.

    For a repeated ratio's source period, identical payload is idempotent.
    Different data at the same original source period is a conflict, not a
    silent correction (which would require separately approved corrections).
    """
    if (
        _COMMIT.fullmatch(producer_release_commit) is None
        or len(source_session_id) != 32
        or not all(c in "0123456789abcdef" for c in source_session_id)
        or fact.market_source_id != BYBIT_PUBLIC_MARKET_SOURCE_ID
        or not fact.values
    ):
        raise PublicRestEvidenceUnverified("PUBLIC_REST_SOURCE_IDENTITY_INVALID")
    from psycopg.types.json import Jsonb  # type: ignore[import-not-found]

    with connection.transaction():
        source_epoch = connection.execute(
            """SELECT state,market_source_id,producer_release_commit
                 FROM analytics.native_public_trade_source_sessions
                WHERE source_session_id=%s""",
            (source_session_id,),
        ).fetchone()
        if source_epoch != (
            "ACTIVE", BYBIT_PUBLIC_MARKET_SOURCE_ID, producer_release_commit,
        ):
            raise PublicRestEvidenceUnverified(
                "PUBLIC_REST_SOURCE_EPOCH_NOT_ACTIVE"
            )
        row = connection.execute(
            """INSERT INTO analytics.public_market_rest_observations
               (market_source_id,venue_id,source_session_id,instrument_id,
                product_category,external_symbol,dataset,source_at,received_at,
                source_period,compact_values,producer_release_commit)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (source_session_id,instrument_id,dataset,source_at)
               DO NOTHING RETURNING rest_observation_id""",
            (
                fact.market_source_id, "venue-bybit", source_session_id,
                fact.instrument_id, fact.product, fact.symbol, fact.dataset,
                fact.source_at, fact.received_at, fact.interval,
                Jsonb(fact.values), producer_release_commit,
            ),
        ).fetchone()
        if row is not None:
            return int(row[0])
        previous = connection.execute(
            """SELECT rest_observation_id,compact_values,product_category,
                      external_symbol,source_period,producer_release_commit
                 FROM analytics.public_market_rest_observations
                WHERE source_session_id=%s AND instrument_id=%s
                  AND dataset=%s AND source_at=%s""",
            (
                source_session_id, fact.instrument_id, fact.dataset,
                fact.source_at,
            ),
        ).fetchone()
        if previous is None or previous[1:] != (
            fact.values, fact.product, fact.symbol, fact.interval,
            producer_release_commit,
        ):
            raise PublicRestEvidenceUnverified(
                "PUBLIC_REST_DUPLICATE_ORIGIN_CONFLICT"
            )
        return int(previous[0])


def collect_public_rest_references(
    connection: Any, *,
    source_session_id: str,
    as_of_second: datetime,
    requirements: Iterable[RequiredPublicFeed],
) -> tuple[dict[str, dict[str, dict[str, object]]],
           dict[str, dict[str, dict[str, object]]]]:
    """Read previously committed public REST source rows by instrument + TTL.

    Original ACCOUNT_RATIO sample time governs freshness, not receipt time.
    Instrument metadata is a receipt-time observation, not a signed
    Exchange trading permission. No REST query or fallback here.
    """
    if (
        as_of_second.tzinfo is None
        or as_of_second.utcoffset() != timedelta(0)
        or as_of_second.microsecond != 0
        or not isinstance(source_session_id, str)
        or len(source_session_id) != 32
    ):
        raise PublicRestEvidenceUnverified("PUBLIC_REST_REFERENCE_CLOCK_INVALID")
    grouped: dict[tuple[str, str], RequiredPublicFeed] = {}
    for item in requirements:
        if item.dataset not in _REST_DATASETS:
            raise PublicRestEvidenceUnverified(
                "PUBLIC_REST_REFERENCE_DATASET_INVALID"
            )
        key = (item.instrument_id, item.dataset)
        old = grouped.get(key)
        if old is None or item.freshness_seconds < old.freshness_seconds:
            grouped[key] = item
    if not grouped:
        return {}, {}
    if len(grouped) > 400:
        raise PublicRestEvidenceUnverified("PUBLIC_REST_REFERENCE_SCOPE_INVALID")

    refs: dict[str, dict[str, dict[str, object]]] = {}
    quality: dict[str, dict[str, dict[str, object]]] = {}
    for dataset in sorted(set(x.dataset for x in grouped.values())):
        needs = [x for x in grouped.values() if x.dataset == dataset]
        instruments = sorted(set(x.instrument_id for x in needs))
        horizon = max(x.freshness_seconds for x in needs)
        rows = connection.execute(
            """SELECT DISTINCT ON (instrument_id)
                      rest_observation_id,instrument_id,product_category,
                      external_symbol,source_at,received_at,source_period,
                      compact_values
                 FROM analytics.public_market_rest_observations
                WHERE source_session_id=%s AND dataset=%s
                  AND instrument_id=ANY(%s)
                  AND source_at < %s
                  AND received_at < %s
                  AND source_at >= %s
                ORDER BY instrument_id,source_at DESC""",
            (
                source_session_id, dataset, instruments, as_of_second,
                as_of_second, as_of_second - timedelta(seconds=horizon),
            ),
        ).fetchall()
        by_id = {str(row[1]): row for row in rows}
        for need in needs:
            quality_item = quality.setdefault(need.instrument_id, {})
            row = by_id.get(need.instrument_id)
            if row is None:
                quality_item[dataset] = {
                    "status": "MISSING_OR_STALE",
                    "required": need.required,
                    "freshness_seconds": need.freshness_seconds,
                }
                continue
            uid, instrument_id, product, symbol, source_at, received_at, period, values = row
            if (
                instrument_id != need.instrument_id
                or product != need.product
                or symbol != need.symbol
                or not isinstance(values, dict)
                or source_at.tzinfo is None
                or source_at.utcoffset() != timedelta(0)
                or received_at.tzinfo is None
                or received_at.utcoffset() != timedelta(0)
                or source_at > received_at + timedelta(seconds=5)
                or period != ("5min" if dataset == "ACCOUNT_RATIO" else "")
            ):
                raise PublicRestEvidenceUnverified(
                    "PUBLIC_REST_REFERENCE_ORIGIN_CONFLICT"
                )
            age = (as_of_second - source_at).total_seconds()
            if age > need.freshness_seconds:
                quality_item[dataset] = {
                    "status": "STALE",
                    "age_seconds": age,
                    "required": need.required,
                    "freshness_seconds": need.freshness_seconds,
                }
                continue
            status = "OBSERVED"
            if dataset == "INSTRUMENT_INFO":
                if values.get("status") == "NOT_LISTED":
                    status = "NOT_LISTED"
                elif values.get("status") != "Trading":
                    status = "NON_TRADING_STATUS"
            item[dataset] = {
                "status": status, "age_seconds": age,
                "required": need.required,
                "freshness_seconds": need.freshness_seconds,
            }
            refs.setdefault(need.instrument_id, {})[dataset] = {
                "rest_observation_id": int(uid),
                "source_session_id": source_session_id,
                "instrument_id": need.instrument_id,
                "product_category": product,
                "external_symbol": symbol,
                "dataset": dataset,
                "source_at": source_at.isoformat(),
                "received_at": received_at.isoformat(),
            }
    return refs, quality
