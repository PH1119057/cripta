"""R05: immutable references from one MarketPublication to compact public facts.

No second copy of prices/orderbook/liquidations is embedded in the publication.
Coverage distinguishes a missing observation from a genuine zero-event claim;
the compact trade stream never becomes P90 original execId/seq evidence.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from bybit_workbench.market_data.source_required_feed_manifest import (
    RequiredPublicFeed,
)

_SUPPORTED = frozenset({"TICKER", "ORDERBOOK_50", "LIQUIDATION", "PUBLIC_TRADE"})
_JSON_KEYS = {
    "TICKER": "ticker",
    "ORDERBOOK_50": "book",
    "LIQUIDATION": "liquidation_count",
    "PUBLIC_TRADE": "trade_count",
}
# Only these two are persistent snapshot facts. Trades and liquidations are
# event streams: missing seconds do not imply that there were zero events.
_EVENT_STREAMS = frozenset({"LIQUIDATION", "PUBLIC_TRADE"})


class CompactPublicationUnverified(RuntimeError):
    """The consumer/source manifest is invalid or source evidence is unverified."""


def collect_compact_public_references(
    connection: Any, *,
    source_session_id: str,
    as_of_second: datetime,
    requirements: Iterable[RequiredPublicFeed],
) -> tuple[dict[str, dict[str, dict[str, object]]],
           dict[str, dict[str, dict[str, object]]]]:
    """Refer to committed compact second rows with explicit quality/TTL.

    Output key is exact instrument ID, *not* ambiguous USDT symbol.
    A reference is an immutable (source_session_id,instrument_id,second) PK.
    The writer must be the only active MarketSource producer. No direct Bybit
    REST/WS reader is introduced in this function.
    """
    if (
        as_of_second.tzinfo is None
        or as_of_second.utcoffset() != timedelta(0)
        or as_of_second.microsecond != 0
        or not isinstance(source_session_id, str)
        or len(source_session_id) != 32
    ):
        raise CompactPublicationUnverified("COMPACT_PUBLICATION_BOUNDARY_INVALID")

    # Two consumers of one physical topic do NOT cause duplicate PG reads or
    # multiple statements of source truth. Strictest required freshness wins.
    grouped: dict[tuple[str, str], RequiredPublicFeed] = {}
    for item in requirements:
        if item.dataset not in _SUPPORTED:
            raise CompactPublicationUnverified(
                "COMPACT_PUBLICATION_UNSUPPORTED_REQUIRED_FEED"
            )
        key = (item.instrument_id, item.dataset)
        old = grouped.get(key)
        if old is None or item.freshness_seconds < old.freshness_seconds:
            grouped[key] = item
    if len(grouped) > 400:
        raise CompactPublicationUnverified("COMPACT_PUBLICATION_SCOPE_EXCEEDED")
    if not grouped:
        return {}, {}

    refs: dict[str, dict[str, dict[str, object]]] = {}
    quality: dict[str, dict[str, dict[str, object]]] = {}
    for dataset in sorted({x.dataset for x in grouped.values()}):
        wanted = [
            x for x in grouped.values() if x.dataset == dataset
        ]
        instrument_ids = sorted({x.instrument_id for x in wanted})
        horizon = max(x.freshness_seconds for x in wanted)
        key = _JSON_KEYS[dataset]
        # This expression is a hard-coded dataset selector, not a caller SQL
        # interpolation. Query is bounded by manifest TTL and original epoch.
        if dataset in _EVENT_STREAMS:
            present_sql = f"(compact_features->>'{key}')::bigint > 0"
        else:
            present_sql = (
                f"jsonb_typeof(compact_features->'{key}')='object'"
            )
        rows = connection.execute(
            f"""SELECT DISTINCT ON (instrument_id)
                       instrument_id, product_category, external_symbol,
                       observed_second,source_time_max,compact_features
                  FROM analytics.public_market_source_seconds
                 WHERE source_session_id=%s
                   AND instrument_id=ANY(%s)
                   AND observed_second < %s
                   AND observed_second >= %s
                   AND {present_sql}
                 ORDER BY instrument_id, observed_second DESC""",
            (
                source_session_id, instrument_ids, as_of_second,
                as_of_second - timedelta(seconds=horizon),
            ),
        ).fetchall()
        by_id = {str(row[0]): row for row in rows}
        for need in wanted:
            ref_key = need.instrument_id
            obj = quality.setdefault(ref_key, {})
            row = by_id.get(ref_key)
            if row is None:
                obj[dataset] = {
                    "status": (
                        "ZERO_OR_GAP_UNVERIFIED" if dataset in _EVENT_STREAMS
                        else "MISSING_OR_STALE"
                    ),
                    "required": need.required,
                    "freshness_seconds": need.freshness_seconds,
                }
                continue
            inst, product, symbol, sec, source_time, compact = row
            if (
                product != need.product or symbol != need.symbol
                or sec.tzinfo is None or sec.utcoffset() != timedelta(0)
                or sec >= as_of_second or source_time > as_of_second
                or not isinstance(compact, dict)
            ):
                raise CompactPublicationUnverified(
                    "COMPACT_PUBLICATION_SOURCE_ROW_CONFLICT"
                )
            age = (as_of_second - sec).total_seconds()
            if age > need.freshness_seconds:
                obj[dataset] = {
                    "status": "STALE",
                    "age_seconds": age,
                    "required": need.required,
                    "freshness_seconds": need.freshness_seconds,
                }
                continue
            status = (
                "COMPACT_ONLY_NOT_NATIVE" if dataset == "PUBLIC_TRADE"
                else "EVENT_OBSERVED" if dataset == "LIQUIDATION"
                else "OBSERVED"
            )
            obj[dataset] = {
                "status": status, "age_seconds": age,
                "required": need.required,
                "freshness_seconds": need.freshness_seconds,
            }
            if dataset == "TICKER":
                field_times = compact.get("ticker_field_times")
                if not isinstance(field_times, dict):
                    raise CompactPublicationUnverified(
                        "COMPACT_PUBLICATION_FIELD_TIMES_MISSING"
                    )
                field_quality: dict[str, str] = {}
                for field, raw in field_times.items():
                    if not isinstance(field, str) or not isinstance(raw, str):
                        raise CompactPublicationUnverified(
                            "COMPACT_PUBLICATION_FIELD_TIME_INVALID"
                        )
                    try:
                        field_at = datetime.fromisoformat(raw)
                    except ValueError as exc:
                        raise CompactPublicationUnverified(
                            "COMPACT_PUBLICATION_FIELD_TIME_INVALID"
                        ) from exc
                    if (
                        field_at.tzinfo is None
                        or field_at.utcoffset() != timedelta(0)
                        or field_at > as_of_second
                    ):
                        raise CompactPublicationUnverified(
                            "COMPACT_PUBLICATION_FIELD_TIME_INVALID"
                        )
                    field_quality[field] = (
                        "FRESH" if (
                            as_of_second - field_at
                        ).total_seconds() <= need.freshness_seconds
                        else "STALE"
                    )
                obj[dataset]["fields"] = field_quality
                if not any(x == "FRESH" for x in field_quality.values()):
                    obj[dataset]["status"] = "STALE"
                    continue
            refs.setdefault(ref_key, {})[dataset] = {
                "source_session_id": source_session_id,
                "instrument_id": inst,
                "product_category": product,
                "external_symbol": symbol,
                "observed_second": sec.isoformat(),
                "source_time_max": source_time.isoformat(),
            }
    return refs, quality
