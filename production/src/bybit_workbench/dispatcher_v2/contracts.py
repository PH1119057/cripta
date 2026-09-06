from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class DataQuality(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"


class FreshnessStatus(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Freshness:
    status: FreshnessStatus
    age_seconds: float | None
    threshold_seconds: int

    def __post_init__(self) -> None:
        if self.threshold_seconds <= 0:
            raise ValueError("freshness threshold must be positive")
        if self.age_seconds is not None and self.age_seconds < 0:
            raise ValueError("freshness age cannot be negative")


@dataclass(frozen=True, slots=True)
class GlobalMarketContext:
    global_context_id: str
    source_market_context_id: str
    source_mayak_snapshot_id: int
    observed_at: datetime
    created_at: datetime
    dispatcher_version: str
    schema_version: str
    config_fingerprint: str
    data_quality: DataQuality
    freshness: Freshness
    coverage: Mapping[str, Any]
    objective_facts: Mapping[str, Any]
    provenance: Mapping[str, Any]
    content_hash: str
    trading_effect: str = "NONE"

    def __post_init__(self) -> None:
        _validate_common(
            self.global_context_id,
            self.observed_at,
            self.created_at,
            self.dispatcher_version,
            self.schema_version,
            self.config_fingerprint,
            self.content_hash,
            self.trading_effect,
        )
        if not self.source_market_context_id:
            raise ValueError("source market context id is required")
        if self.source_mayak_snapshot_id <= 0:
            raise ValueError("source Mayak snapshot id must be positive")
        object.__setattr__(self, "coverage", _freeze_mapping(self.coverage))
        object.__setattr__(self, "objective_facts", _freeze_mapping(self.objective_facts))
        object.__setattr__(self, "provenance", _freeze_mapping(self.provenance))


@dataclass(frozen=True, slots=True)
class CoinMarketContext:
    coin_context_id: str
    global_context_id: str
    source_coin_context_id: str
    source_mayak_snapshot_id: int
    symbol: str
    observed_at: datetime
    created_at: datetime
    dispatcher_version: str
    schema_version: str
    config_fingerprint: str
    data_quality: DataQuality
    freshness: Freshness
    coverage: Mapping[str, Any]
    objective_facts: Mapping[str, Any]
    provenance: Mapping[str, Any]
    content_hash: str
    trading_effect: str = "NONE"

    def __post_init__(self) -> None:
        _validate_common(
            self.coin_context_id,
            self.observed_at,
            self.created_at,
            self.dispatcher_version,
            self.schema_version,
            self.config_fingerprint,
            self.content_hash,
            self.trading_effect,
        )
        if not self.global_context_id or not self.source_coin_context_id:
            raise ValueError("global and source coin context ids are required")
        if self.source_mayak_snapshot_id <= 0 or not self.symbol:
            raise ValueError("source snapshot id and symbol are required")
        object.__setattr__(self, "coverage", _freeze_mapping(self.coverage))
        object.__setattr__(self, "objective_facts", _freeze_mapping(self.objective_facts))
        object.__setattr__(self, "provenance", _freeze_mapping(self.provenance))


@dataclass(frozen=True, slots=True)
class TradingAccountState:
    observed_at: datetime
    source_adapter: str
    source_account_ref: str
    account_type: str | None
    total_equity: Decimal | None
    wallet_balance: Decimal | None
    used_position_margin: Decimal | None
    reserved_order_margin: Decimal | None
    free_balance: Decimal | None
    open_positions_count: int
    active_orders_count: int
    source_payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_utc(self.observed_at, "observed_at")
        if not self.source_adapter or not self.source_account_ref:
            raise ValueError("account source identity is required")
        if self.open_positions_count < 0 or self.active_orders_count < 0:
            raise ValueError("account counts cannot be negative")
        object.__setattr__(self, "source_payload", _freeze_mapping(self.source_payload))


@dataclass(frozen=True, slots=True)
class TradingCapacitySnapshot:
    capacity_snapshot_id: str
    observed_at: datetime
    created_at: datetime
    dispatcher_version: str
    schema_version: str
    config_fingerprint: str
    source_adapter: str
    source_account_ref: str
    account_type: str | None
    data_quality: DataQuality
    freshness: Freshness
    total_equity: Decimal | None
    wallet_balance: Decimal | None
    used_position_margin: Decimal | None
    reserved_order_margin: Decimal | None
    free_balance: Decimal | None
    available_for_new_trading: Decimal | None
    open_positions_count: int
    active_orders_count: int
    coverage: Mapping[str, Any]
    provenance: Mapping[str, Any]
    content_hash: str
    trading_effect: str = "NONE"

    def __post_init__(self) -> None:
        _validate_common(
            self.capacity_snapshot_id,
            self.observed_at,
            self.created_at,
            self.dispatcher_version,
            self.schema_version,
            self.config_fingerprint,
            self.content_hash,
            self.trading_effect,
        )
        if not self.source_adapter or not self.source_account_ref:
            raise ValueError("capacity source identity is required")
        if self.open_positions_count < 0 or self.active_orders_count < 0:
            raise ValueError("capacity counts cannot be negative")
        object.__setattr__(self, "coverage", _freeze_mapping(self.coverage))
        object.__setattr__(self, "provenance", _freeze_mapping(self.provenance))


def _validate_common(
    context_id: str,
    observed_at: datetime,
    created_at: datetime,
    dispatcher_version: str,
    schema_version: str,
    config_fingerprint: str,
    content_hash: str,
    trading_effect: str,
) -> None:
    if not all((context_id, dispatcher_version, schema_version, config_fingerprint, content_hash)):
        raise ValueError("context identity/version fields are required")
    _require_utc(observed_at, "observed_at")
    _require_utc(created_at, "created_at")
    if trading_effect != "NONE":
        raise ValueError("Dispatcher V2 trading effect must remain NONE")


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{name} must be timezone-aware UTC")


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")
