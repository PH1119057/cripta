from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from bybit_workbench.market_identity import (
    BYBIT_PUBLIC_MARKET_SOURCE_ID,
    BYBIT_VENUE_ID,
    bybit_instrument_id,
)

COMPACT_ANALYTICS_SCHEMA_VERSION = "compact-market-minute-v3"
FLOW_EXTRA_HORIZONS = ("1m", "15m", "30m", "60m")
BOOK_DEPTH_BPS = (5, 10, 25, 50)
BOOK_CHANGE_HORIZONS = ("1m", "5m", "15m")

_BASE_COLUMNS = (
    "observed_minute",
    "source_observed_at",
    "venue_id",
    "market_source_id",
    "instrument_id",
    "external_symbol",
    "product_category",
    "return_5m_pct",
    "mid_price",
    "buy_usd",
    "sell_usd",
    "net_usd",
    "turnover_usd",
    "large_buy_usd",
    "large_sell_usd",
    "bid_usd",
    "ask_usd",
    "bid_change_pct",
    "ask_change_pct",
    "open_interest",
    "open_interest_change_pct",
    "funding_rate",
    "last_price",
    "mark_price",
    "index_price",
    "long_ratio",
    "short_ratio",
    "trade_quality",
    "trade_age_seconds",
    "book_quality",
    "book_age_seconds",
    "ticker_quality",
    "ticker_age_seconds",
    "transport_quality",
    "instrument_availability",
    "source_snapshot_id",
    "architecture_version",
    "engine_version",
    "feature_version",
    "config_fingerprint",
    "schema_version",
)

_FLOW_EXTRA_COLUMNS = tuple(
    f"flow_{horizon}_{field}"
    for horizon in FLOW_EXTRA_HORIZONS
    for field in (
        "return_pct",
        "buy_usd",
        "sell_usd",
        "net_usd",
        "turnover_usd",
        "large_buy_usd",
        "large_sell_usd",
        "prior_turnover_usd",
        "acceleration_usd_per_min2",
    )
)

_BOOK_DEPTH_COLUMNS = tuple(
    f"{side}_depth_{bps}bps_usd"
    for bps in BOOK_DEPTH_BPS
    for side in ("bid", "ask")
)

_BOOK_CHANGE_COLUMNS = tuple(
    column
    for horizon in BOOK_CHANGE_HORIZONS
    for column in (
        f"bid_change_{horizon}_pct",
        f"ask_change_{horizon}_pct",
        f"imbalance_change_{horizon}",
    )
)

_R8C_COLUMNS = (
    "flow_5m_prior_turnover_usd",
    "flow_5m_acceleration_usd_per_min2",
    *_FLOW_EXTRA_COLUMNS,
    "best_bid",
    "best_ask",
    *_BOOK_DEPTH_COLUMNS,
    *_BOOK_CHANGE_COLUMNS,
    "open_interest_value",
    "open_interest_change_15m_pct",
    "open_interest_change_30m_pct",
    "open_interest_change_60m_pct",
    "open_interest_speed_5m_pct_per_min",
    "open_interest_acceleration_5m_pct_per_min2",
    "funding_rate_change_from_previous",
)

COMPACT_MARKET_MINUTE_INSERT_COLUMNS = (*_BASE_COLUMNS, *_R8C_COLUMNS)


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
        if _required_text(context, "symbol") != symbol:
            raise ValueError(f"compact context symbol mismatch: {symbol}")
        if _required_text(context, "market_source_id") != BYBIT_PUBLIC_MARKET_SOURCE_ID:
            raise ValueError(f"compact market source mismatch: {symbol}")
        if _datetime(context.get("observed_at")) != observed_at:
            raise ValueError(f"compact context observed_at mismatch: {symbol}")

        payload = _mapping(context.get("payload"))
        money = _mapping(payload.get("money"))
        liquidity = _mapping(payload.get("liquidity"))
        positioning = _mapping(payload.get("positioning"))
        data_quality = _mapping(payload.get("data_quality"))
        sources = _mapping(data_quality.get("sources"))
        availability = _mapping(data_quality.get("availability"))

        for market, payload_market, product_category, context_identity_field in (
            ("spot", "spot", "SPOT", "spot_instrument_id"),
            ("linear", "derivatives", "LINEAR", "linear_instrument_id"),
        ):
            expected_instrument = bybit_instrument_id(market, symbol)
            if _required_text(context, context_identity_field) != expected_instrument:
                raise ValueError(
                    f"compact instrument identity mismatch: {symbol}:{market}"
                )

            flow_by_horizon = _mapping(money.get(payload_market))
            flow_5m = _mapping_or_empty(flow_by_horizon.get("5m"))
            book = _mapping_or_empty(liquidity.get(payload_market))
            trade_quality = _mapping_or_empty(sources.get(f"{market}_trades"))
            book_quality = _mapping_or_empty(sources.get(f"{market}_book"))
            ticker_quality = (
                _mapping_or_empty(sources.get("linear_ticker"))
                if market == "linear"
                else {}
            )

            row: dict[str, Any] = {
                "observed_minute": observed_minute,
                "source_observed_at": observed_at,
                "venue_id": BYBIT_VENUE_ID,
                "market_source_id": BYBIT_PUBLIC_MARKET_SOURCE_ID,
                "instrument_id": expected_instrument,
                "external_symbol": symbol,
                "product_category": product_category,
                "return_5m_pct": flow_5m.get("return_pct"),
                "mid_price": book.get("mid_price"),
                "buy_usd": flow_5m.get("buy_usd"),
                "sell_usd": flow_5m.get("sell_usd"),
                "net_usd": flow_5m.get("net_usd"),
                "turnover_usd": flow_5m.get("turnover_usd"),
                "large_buy_usd": flow_5m.get("large_buy_usd"),
                "large_sell_usd": flow_5m.get("large_sell_usd"),
                "bid_usd": book.get("bid_usd"),
                "ask_usd": book.get("ask_usd"),
                "bid_change_pct": book.get("bid_change_pct"),
                "ask_change_pct": book.get("ask_change_pct"),
                "open_interest": (
                    positioning.get("open_interest") if market == "linear" else None
                ),
                "open_interest_change_pct": (
                    positioning.get("open_interest_change_5m_pct")
                    if market == "linear"
                    else None
                ),
                "funding_rate": (
                    positioning.get("funding_rate") if market == "linear" else None
                ),
                "last_price": (
                    positioning.get("last_price") if market == "linear" else None
                ),
                "mark_price": (
                    positioning.get("mark_price") if market == "linear" else None
                ),
                "index_price": (
                    positioning.get("index_price") if market == "linear" else None
                ),
                "long_ratio": (
                    positioning.get("long_ratio") if market == "linear" else None
                ),
                "short_ratio": (
                    positioning.get("short_ratio") if market == "linear" else None
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
                "flow_5m_prior_turnover_usd": flow_5m.get("prior_turnover_usd"),
                "flow_5m_acceleration_usd_per_min2": flow_5m.get(
                    "acceleration_usd_per_min2"
                ),
                "best_bid": book.get("best_bid"),
                "best_ask": book.get("best_ask"),
            }

            for horizon in FLOW_EXTRA_HORIZONS:
                flow = _mapping_or_empty(flow_by_horizon.get(horizon))
                for field in (
                    "return_pct",
                    "buy_usd",
                    "sell_usd",
                    "net_usd",
                    "turnover_usd",
                    "large_buy_usd",
                    "large_sell_usd",
                    "prior_turnover_usd",
                    "acceleration_usd_per_min2",
                ):
                    row[f"flow_{horizon}_{field}"] = flow.get(field)

            for bps in BOOK_DEPTH_BPS:
                row[f"bid_depth_{bps}bps_usd"] = book.get(
                    f"bid_depth_{bps}bps_usd"
                )
                row[f"ask_depth_{bps}bps_usd"] = book.get(
                    f"ask_depth_{bps}bps_usd"
                )
            for horizon in BOOK_CHANGE_HORIZONS:
                row[f"bid_change_{horizon}_pct"] = book.get(
                    f"bid_change_{horizon}_pct"
                )
                row[f"ask_change_{horizon}_pct"] = book.get(
                    f"ask_change_{horizon}_pct"
                )
                row[f"imbalance_change_{horizon}"] = book.get(
                    f"imbalance_change_{horizon}"
                )

            for field in (
                "open_interest_value",
                "open_interest_change_15m_pct",
                "open_interest_change_30m_pct",
                "open_interest_change_60m_pct",
                "open_interest_speed_5m_pct_per_min",
                "open_interest_acceleration_5m_pct_per_min2",
                "funding_rate_change_from_previous",
            ):
                row[field] = positioning.get(field) if market == "linear" else None

            if set(row) != set(COMPACT_MARKET_MINUTE_INSERT_COLUMNS):
                missing = sorted(set(COMPACT_MARKET_MINUTE_INSERT_COLUMNS) - set(row))
                extra = sorted(set(row) - set(COMPACT_MARKET_MINUTE_INSERT_COLUMNS))
                raise RuntimeError(
                    f"compact row shape mismatch missing={missing} extra={extra}"
                )
            rows.append(row)
    return tuple(rows)


def compact_market_minute_insert_sql() -> str:
    columns = ",".join(COMPACT_MARKET_MINUTE_INSERT_COLUMNS)
    values = ",".join(f"%({column})s" for column in COMPACT_MARKET_MINUTE_INSERT_COLUMNS)
    return (
        f"INSERT INTO analytics.market_minutes({columns}) VALUES({values}) "
        "ON CONFLICT(market_source_id,instrument_id,observed_minute) DO NOTHING"
    )


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
