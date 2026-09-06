from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .contracts import (
    CoinMarketContext,
    DataQuality,
    Freshness,
    FreshnessStatus,
    GlobalMarketContext,
    TradingAccountState,
    TradingCapacitySnapshot,
)

DISPATCHER_VERSION = "dispatcher-v2.1"
GLOBAL_SCHEMA_VERSION = "global-market-context-v2.1"
COIN_SCHEMA_VERSION = "dispatcher-coin-context-v2.1"
CAPACITY_SCHEMA_VERSION = "trading-capacity-v2.1"
MARKET_FRESH_SECONDS = 90
COIN_FRESH_SECONDS = 90
ACCOUNT_FRESH_SECONDS = 30
CONFIG_FINGERPRINT = hashlib.sha256(
    (
        f"{DISPATCHER_VERSION}|{GLOBAL_SCHEMA_VERSION}|{COIN_SCHEMA_VERSION}|"
        f"{CAPACITY_SCHEMA_VERSION}|{MARKET_FRESH_SECONDS}|{COIN_FRESH_SECONDS}|"
        f"{ACCOUNT_FRESH_SECONDS}"
    ).encode()
).hexdigest()


def build_global_market_context(source: Mapping[str, Any], *, now: datetime) -> GlobalMarketContext:
    observed_at = _datetime(source["observed_at"])
    created_at = _utc(now)
    payload = _mapping(source["payload"])
    features = _mapping(payload.get("dispatcher_features"))
    coverage = _global_coverage(features)
    facts = {
        "objective_features": _plain(features),
        "source_market_context_schema": str(source["schema_version"]),
        "source_architecture_version": payload.get("architecture_version"),
    }
    provenance = {
        "source": "mayak_v2.shared_market_contexts",
        "source_market_context_id": str(source["market_context_id"]),
        "source_content_hash": str(source["content_hash"]),
        "source_mayak_version": str(source["mayak_version"]),
        "source_config_fingerprint": str(source["config_fingerprint"]),
        "immutable": True,
        "trading_command": False,
        "coin_market_rating": "NOT_IMPLEMENTED",
    }
    canonical = {
        "source_market_context_id": str(source["market_context_id"]),
        "source_mayak_snapshot_id": int(source["mayak_snapshot_id"]),
        "observed_at": observed_at.isoformat(),
        "data_quality": str(source["data_quality"]),
        "coverage": coverage,
        "objective_facts": facts,
        "provenance": provenance,
    }
    content_hash = _stable_hash(canonical)
    return GlobalMarketContext(
        global_context_id=f"DGC-{content_hash[:32]}",
        source_market_context_id=str(source["market_context_id"]),
        source_mayak_snapshot_id=int(source["mayak_snapshot_id"]),
        observed_at=observed_at,
        created_at=created_at,
        dispatcher_version=DISPATCHER_VERSION,
        schema_version=GLOBAL_SCHEMA_VERSION,
        config_fingerprint=CONFIG_FINGERPRINT,
        data_quality=_quality(source["data_quality"]),
        freshness=_freshness(observed_at, created_at, MARKET_FRESH_SECONDS),
        coverage=coverage,
        objective_facts=facts,
        provenance=provenance,
        content_hash=content_hash,
    )


def build_coin_market_context(
    source: Mapping[str, Any], *, global_context_id: str, now: datetime
) -> CoinMarketContext:
    observed_at = _datetime(source["observed_at"])
    created_at = _utc(now)
    payload = _mapping(source["payload"])
    data_quality = _mapping(payload.get("data_quality"))
    coverage = {
        "source_coverage": data_quality.get("coverage"),
        "fresh_sources": data_quality.get("fresh_sources"),
        "expected_sources": data_quality.get("expected_sources"),
        "source_status": str(source["data_quality"]),
    }
    facts = _plain(payload)
    provenance = {
        "source": "mayak_v2.coin_market_contexts",
        "source_coin_context_id": str(source["coin_context_id"]),
        "source_content_hash": str(source["content_hash"]),
        "source_engine_version": str(source["engine_version"]),
        "source_feature_version": str(source["feature_version"]),
        "source_config_fingerprint": str(source["config_fingerprint"]),
        "immutable": True,
        "trading_command": False,
        "coin_market_rating": "NOT_IMPLEMENTED",
    }
    canonical = {
        "global_context_id": global_context_id,
        "source_coin_context_id": str(source["coin_context_id"]),
        "source_mayak_snapshot_id": int(source["mayak_snapshot_id"]),
        "symbol": str(source["symbol"]),
        "observed_at": observed_at.isoformat(),
        "data_quality": str(source["data_quality"]),
        "coverage": coverage,
        "objective_facts": facts,
        "provenance": provenance,
    }
    content_hash = _stable_hash(canonical)
    return CoinMarketContext(
        coin_context_id=f"DCC-{content_hash[:32]}",
        global_context_id=global_context_id,
        source_coin_context_id=str(source["coin_context_id"]),
        source_mayak_snapshot_id=int(source["mayak_snapshot_id"]),
        symbol=str(source["symbol"]),
        observed_at=observed_at,
        created_at=created_at,
        dispatcher_version=DISPATCHER_VERSION,
        schema_version=COIN_SCHEMA_VERSION,
        config_fingerprint=CONFIG_FINGERPRINT,
        data_quality=_quality(source["data_quality"]),
        freshness=_freshness(observed_at, created_at, COIN_FRESH_SECONDS),
        coverage=coverage,
        objective_facts=facts,
        provenance=provenance,
        content_hash=content_hash,
    )


def build_trading_capacity_snapshot(
    source: TradingAccountState, *, now: datetime
) -> TradingCapacitySnapshot:
    created_at = _utc(now)
    required = {
        "total_equity": source.total_equity,
        "wallet_balance": source.wallet_balance,
        "free_balance": source.free_balance,
    }
    optional_margin = {
        "used_position_margin": source.used_position_margin,
        "reserved_order_margin": source.reserved_order_margin,
    }
    required_present = sum(value is not None for value in required.values())
    margin_present = sum(value is not None for value in optional_margin.values())
    if required_present < len(required):
        quality = DataQuality.INSUFFICIENT
    elif margin_present < len(optional_margin):
        quality = DataQuality.MEDIUM
    else:
        quality = DataQuality.HIGH
    coverage = {
        "required_fields_present": required_present,
        "required_fields_total": len(required),
        "margin_fields_present": margin_present,
        "margin_fields_total": len(optional_margin),
    }
    provenance = {
        "source": "technical_account_sync",
        "source_adapter": source.source_adapter,
        "source_account_ref": source.source_account_ref,
        "available_for_new_trading_semantics": "exchange_reported_free_balance",
        "immutable": True,
        "trading_command": False,
    }
    canonical = {
        "observed_at": source.observed_at.isoformat(),
        "source_adapter": source.source_adapter,
        "source_account_ref": source.source_account_ref,
        "account_type": source.account_type,
        "total_equity": _decimal_text(source.total_equity),
        "wallet_balance": _decimal_text(source.wallet_balance),
        "used_position_margin": _decimal_text(source.used_position_margin),
        "reserved_order_margin": _decimal_text(source.reserved_order_margin),
        "free_balance": _decimal_text(source.free_balance),
        "open_positions_count": source.open_positions_count,
        "active_orders_count": source.active_orders_count,
        "coverage": coverage,
        "provenance": provenance,
    }
    content_hash = _stable_hash(canonical)
    return TradingCapacitySnapshot(
        capacity_snapshot_id=f"DTC-{content_hash[:32]}",
        observed_at=source.observed_at,
        created_at=created_at,
        dispatcher_version=DISPATCHER_VERSION,
        schema_version=CAPACITY_SCHEMA_VERSION,
        config_fingerprint=CONFIG_FINGERPRINT,
        source_adapter=source.source_adapter,
        source_account_ref=source.source_account_ref,
        account_type=source.account_type,
        data_quality=quality,
        freshness=_freshness(source.observed_at, created_at, ACCOUNT_FRESH_SECONDS),
        total_equity=source.total_equity,
        wallet_balance=source.wallet_balance,
        used_position_margin=source.used_position_margin,
        reserved_order_margin=source.reserved_order_margin,
        free_balance=source.free_balance,
        available_for_new_trading=source.free_balance,
        open_positions_count=source.open_positions_count,
        active_orders_count=source.active_orders_count,
        coverage=coverage,
        provenance=provenance,
        content_hash=content_hash,
    )


def _freshness(observed_at: datetime, now: datetime, threshold: int) -> Freshness:
    age = (now - observed_at).total_seconds()
    if age < 0:
        return Freshness(FreshnessStatus.UNKNOWN, None, threshold)
    return Freshness(
        FreshnessStatus.FRESH if age <= threshold else FreshnessStatus.STALE,
        round(age, 6),
        threshold,
    )


def _global_coverage(features: Mapping[str, Any]) -> dict[str, Any]:
    statuses: Counter[str] = Counter()
    source_coverage: dict[str, Any] = {}
    for feature_id, item in features.items():
        feature = _mapping(item)
        statuses[str(feature.get("status") or "UNKNOWN")] += 1
        if "coverage" in feature:
            source_coverage[str(feature_id)] = _plain(feature.get("coverage"))
    total = len(features)
    valid = statuses.get("VALID", 0)
    return {
        "feature_total": total,
        "feature_valid": valid,
        "feature_valid_ratio": 0.0 if total == 0 else valid / total,
        "feature_status_counts": dict(sorted(statuses.items())),
        "source_feature_coverage": source_coverage,
    }


def _quality(value: Any) -> DataQuality:
    try:
        return DataQuality(str(value))
    except ValueError:
        return DataQuality.INSUFFICIENT


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    if not isinstance(value, str):
        raise TypeError("datetime source must be datetime or ISO string")
    return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _stable_hash(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")
