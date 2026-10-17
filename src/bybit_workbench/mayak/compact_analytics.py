from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    BYBIT_VENUE_ID,
    bybit_instrument_id,
)

COMPACT_ANALYTICS_SCHEMA_VERSION = "compact-market-minute-v2"


def compact_market_minute_rows(
    snapshot_id: int,
    snapshot: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    if snapshot_id <= 0:
        raise ValueError("snapshot_id must be positive")
    observed_at = _datetime(snapshot.get("observed_at"))
    observed_minute = observed_at.replace(second=0, microsecond=0)
    architecture_version = _required_text(snapshot, "architecture_version")
    engine_version = _required_text(snapshot, "engine_version")
    feature_version = _required_text(snapshot, "feature_version")
    config_fingerprint = _required_text(snapshot, "config_fingerprint")
    contexts = _mapping(snapshot.get("coin_market_contexts"))

    rows: list[dict[str, Any]] = []
    for symbol in sorted(str(value) for value in contexts):
        context = _mapping(contexts[symbol])
        payload = _mapping(context.get("payload"))
        money = _mapping(payload.get("money"))
        liquidity = _mapping(payload.get("liquidity"))
        positioning = _mapping(payload.get("positioning"))
        data_quality = _mapping(payload.get("data_quality"))
        sources = _mapping(data_quality.get("sources"))
        availability = _mapping(data_quality.get("availability"))

        for market, payload_market, product_category in (
            ("spot", "spot", "SPOT"),
            ("linear", "derivatives", "LINEAR"),
        ):
            flow_by_horizon = _mapping(money.get(payload_market))
            flow = _mapping_or_empty(flow_by_horizon.get("5m"))
            book = _mapping_or_empty(liquidity.get(payload_market))
            trade_quality = _mapping_or_empty(sources.get(f"{market}_trades"))
            book_quality = _mapping_or_empty(sources.get(f"{market}_book"))
            ticker_quality = (
                _mapping_or_empty(sources.get("linear_ticker"))
                if market == "linear"
                else {}
            )
            rows.append(
                {
                    "observed_minute": observed_minute,
                    "source_observed_at": observed_at,
                    "venue_id": BYBIT_VENUE_ID,
                    "market_source_id": BYBIT_PUBLIC_MARKET_SOURCE_ID,
                    "instrument_id": bybit_instrument_id(market, symbol),
                    "external_symbol": symbol,
                    "product_category": product_category,
                    "return_5m_pct": flow.get("return_pct"),
                    "mid_price": book.get("mid_price"),
                    "buy_usd": flow.get("buy_usd"),
                    "sell_usd": flow.get("sell_usd"),
                    "net_usd": flow.get("net_usd"),
                    "turnover_usd": flow.get("turnover_usd"),
                    "large_buy_usd": flow.get("large_buy_usd"),
                    "large_sell_usd": flow.get("large_sell_usd"),
                    "bid_usd": book.get("bid_usd"),
                    "ask_usd": book.get("ask_usd"),
                    "bid_change_pct": book.get("bid_change_pct"),
                    "ask_change_pct": book.get("ask_change_pct"),
                    "open_interest": (
                        positioning.get("open_interest")
                        if market == "linear"
                        else None
                    ),
                    "open_interest_change_pct": (
                        positioning.get("open_interest_change_5m_pct")
                        if market == "linear"
                        else None
                    ),
                    "funding_rate": (
                        positioning.get("funding_rate")
                        if market == "linear"
                        else None
                    ),
                    "last_price": (
                        positioning.get("last_price")
                        if market == "linear"
                        else None
                    ),
                    "mark_price": (
                        positioning.get("mark_price")
                        if market == "linear"
                        else None
                    ),
                    "index_price": (
                        positioning.get("index_price")
                        if market == "linear"
                        else None
                    ),
                    "long_ratio": (
                        positioning.get("long_ratio")
                        if market == "linear"
                        else None
                    ),
                    "short_ratio": (
                        positioning.get("short_ratio")
                        if market == "linear"
                        else None
                    ),
                    "trade_quality": _optional_text(trade_quality.get("quality")),
                    "trade_age_seconds": trade_quality.get("age_seconds"),
                    "book_quality": _optional_text(book_quality.get("quality")),
                    "book_age_seconds": book_quality.get("age_seconds"),
                    "ticker_quality": _optional_text(ticker_quality.get("quality")),
                    "ticker_age_seconds": ticker_quality.get("age_seconds"),
                    "transport_quality": _optional_text(
                        trade_quality.get("transport_quality")
                        or book_quality.get("transport_quality")
                        or ticker_quality.get("transport_quality")
                    ),
                    "instrument_availability": _optional_text(availability.get(market)),
                    "source_snapshot_id": snapshot_id,
                    "architecture_version": architecture_version,
                    "engine_version": engine_version,
                    "feature_version": feature_version,
                    "config_fingerprint": config_fingerprint,
                    "schema_version": COMPACT_ANALYTICS_SCHEMA_VERSION,
                }
            )
    return tuple(rows)


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    raise TypeError("expected mapping")


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value)
    else:
        raise TypeError("observed_at must be datetime or ISO-8601 text")
    if parsed.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    return parsed


def _required_text(source: Mapping[str, Any], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing compact analytics provenance field: {key}")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value)
    return rendered if rendered else None
