from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from .contracts import CoinMarketContext, GlobalMarketContext, TradingCapacitySnapshot


def global_context_record(item: GlobalMarketContext) -> dict[str, Any]:
    return {
        "global_context_id": item.global_context_id,
        "source_market_context_id": item.source_market_context_id,
        "source_mayak_snapshot_id": item.source_mayak_snapshot_id,
        "observed_at": item.observed_at,
        "created_at": item.created_at,
        "dispatcher_version": item.dispatcher_version,
        "schema_version": item.schema_version,
        "config_fingerprint": item.config_fingerprint,
        "data_quality": item.data_quality.value,
        "freshness_status": item.freshness.status.value,
        "freshness_age_seconds": item.freshness.age_seconds,
        "coverage": thaw_json(item.coverage),
        "payload": {"objective_facts": thaw_json(item.objective_facts)},
        "provenance": thaw_json(item.provenance),
        "content_hash": item.content_hash,
        "trading_effect": item.trading_effect,
    }


def coin_context_record(item: CoinMarketContext) -> dict[str, Any]:
    return {
        "coin_context_id": item.coin_context_id,
        "global_context_id": item.global_context_id,
        "source_coin_context_id": item.source_coin_context_id,
        "source_mayak_snapshot_id": item.source_mayak_snapshot_id,
        "symbol": item.symbol,
        "observed_at": item.observed_at,
        "created_at": item.created_at,
        "dispatcher_version": item.dispatcher_version,
        "schema_version": item.schema_version,
        "config_fingerprint": item.config_fingerprint,
        "data_quality": item.data_quality.value,
        "freshness_status": item.freshness.status.value,
        "freshness_age_seconds": item.freshness.age_seconds,
        "coverage": thaw_json(item.coverage),
        "payload": {"objective_facts": thaw_json(item.objective_facts)},
        "provenance": thaw_json(item.provenance),
        "content_hash": item.content_hash,
        "trading_effect": item.trading_effect,
    }


def capacity_record(item: TradingCapacitySnapshot) -> dict[str, Any]:
    return {
        "capacity_snapshot_id": item.capacity_snapshot_id,
        "observed_at": item.observed_at,
        "created_at": item.created_at,
        "dispatcher_version": item.dispatcher_version,
        "schema_version": item.schema_version,
        "config_fingerprint": item.config_fingerprint,
        "source_adapter": item.source_adapter,
        "source_account_ref": item.source_account_ref,
        "account_type": item.account_type,
        "data_quality": item.data_quality.value,
        "freshness_status": item.freshness.status.value,
        "freshness_age_seconds": item.freshness.age_seconds,
        "total_equity": item.total_equity,
        "wallet_balance": item.wallet_balance,
        "used_position_margin": item.used_position_margin,
        "reserved_order_margin": item.reserved_order_margin,
        "free_balance": item.free_balance,
        "available_for_new_trading": item.available_for_new_trading,
        "open_positions_count": item.open_positions_count,
        "active_orders_count": item.active_orders_count,
        "coverage": thaw_json(item.coverage),
        "payload": {
            "available_for_new_trading": _decimal_text(item.available_for_new_trading),
            "used_position_margin": _decimal_text(item.used_position_margin),
            "reserved_order_margin": _decimal_text(item.reserved_order_margin),
        },
        "provenance": thaw_json(item.provenance),
        "content_hash": item.content_hash,
        "trading_effect": item.trading_effect,
    }


def thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")
