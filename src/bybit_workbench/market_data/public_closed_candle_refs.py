"""One canonical closed-candle evidence pointer per real SPOT/LINEAR instrument.

Bybit symbols overlap across SPOT and LINEAR. Never key these shared source
facts by bare symbol+timeframe: that silently merges different markets.
The existing analytics.closed_candles is the sole original closed OHLCV store;
MarketPublication stores immutable references, not another candle copy.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

from bybit_workbench.market_data.source_required_feed_manifest import (
    RequiredPublicFeed,
)
from bybit_workbench.market_identity import BYBIT_PUBLIC_MARKET_SOURCE_ID


class ClosedCandleReferenceUnverified(RuntimeError):
    """Missing or cross-product source data can never become a closed bar."""


def collect_instrument_closed_candle_references(
    connection: Any, *,
    as_of_second: datetime,
    requirements: Iterable[RequiredPublicFeed],
) -> tuple[
    dict[str, dict[str, dict[str, object]]],
    dict[str, dict[str, dict[str, object]]],
]:
    """Reference the latest *confirmed* close in the existing canonical store.

    A latest-bar reference is NOT proof of any preceding warmup window or
    continuous interval. No synthetic fill, RESAMPLE or rolling forward.
    """
    if (
        as_of_second.tzinfo is None
        or as_of_second.utcoffset() != timedelta(0)
        or as_of_second.microsecond != 0
    ):
        raise ClosedCandleReferenceUnverified(
            "CLOSED_REFERENCE_BOUNDARY_NOT_UTC_SECOND"
        )
    grouped: dict[tuple[str, str], RequiredPublicFeed] = {}
    historical: dict[tuple[str, str], int] = {}
    for need in requirements:
        if (
            need.dataset != "CLOSED_KLINE"
            or need.product not in ("SPOT", "LINEAR")
            or need.timeframe not in ("1", "5", "15", "60")
        ):
            raise ClosedCandleReferenceUnverified(
                "CLOSED_REFERENCE_MANIFEST_UNSUPPORTED"
            )
        key = (need.instrument_id, need.timeframe)
        old = grouped.get(key)
        if old is None or need.freshness_seconds < old.freshness_seconds:
            grouped[key] = need
        historical[key] = max(historical.get(key, 0), need.lookback_bars)
    if not grouped:
        return {}, {}
    if len(grouped) > 400:
        raise ClosedCandleReferenceUnverified("CLOSED_REFERENCE_SCOPE_TOO_LARGE")
    insts = sorted({x[0] for x in grouped})
    frames = sorted({x[1] for x in grouped}, key=int)
    rows = connection.execute(
        """SELECT DISTINCT ON (instrument_id,timeframe)
                  instrument_id,product_category,external_symbol,
                  timeframe,closed_at
             FROM analytics.closed_candles_effective
            WHERE market_source_id=%s AND instrument_id=ANY(%s)
              AND timeframe=ANY(%s) AND closed_at<=%s
            ORDER BY instrument_id,timeframe,closed_at DESC""",
        (
            BYBIT_PUBLIC_MARKET_SOURCE_ID, insts, frames, as_of_second,
        ),
    ).fetchall()
    by_key = {(str(r[0]), str(r[3])): r for r in rows}
    refs: dict[str, dict[str, dict[str, object]]] = {}
    quality: dict[str, dict[str, dict[str, object]]] = {}
    for (instrument, tf), need in grouped.items():
        row = by_key.get((instrument, tf))
        if row is None:
            quality.setdefault(instrument, {})[tf] = {
                "status": "MISSING_OR_STALE",
                "required": need.required,
                "freshness_seconds": need.freshness_seconds,
                "history_required_bars": historical[(instrument, tf)],
                "history_window": "NOT_VERIFIED",
            }
            continue
        inst, product, symbol, timeframe, closed = row
        if (
            inst != instrument or product != need.product
            or symbol != need.symbol or timeframe != tf
            or closed.tzinfo is None or closed.utcoffset() != timedelta(0)
            or closed > as_of_second
            or closed.second != 0 or closed.microsecond != 0
        ):
            raise ClosedCandleReferenceUnverified(
                "CLOSED_REFERENCE_ORIGIN_MISMATCH"
            )
        age = (as_of_second - closed).total_seconds()
        status = (
            "LAST_CLOSED_OBSERVED_WINDOW_UNVERIFIED"
            if age <= need.freshness_seconds else "STALE"
        )
        quality.setdefault(instrument, {})[tf] = {
            "status": status, "age_seconds": age,
            "required": need.required,
            "freshness_seconds": need.freshness_seconds,
            "history_required_bars": historical[(instrument, tf)],
            "history_window": "NOT_VERIFIED",
        }
        if status == "STALE":
            continue
        refs.setdefault(instrument, {})[tf] = {
            "market_source_id": BYBIT_PUBLIC_MARKET_SOURCE_ID,
            "instrument_id": instrument,
            "product_category": product,
            "external_symbol": symbol,
            "timeframe": timeframe,
            "closed_at": closed.isoformat(),
        }
    return refs, quality
