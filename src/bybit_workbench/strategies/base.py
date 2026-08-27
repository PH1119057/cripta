from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol

from bybit_workbench.domain.intents import (
    CancelEntryIntent,
    EnterIntent,
    ExitIntent,
    NoOpIntent,
    UpdateProtectionIntent,
)
from bybit_workbench.domain.models import Candle, Execution, Position
from bybit_workbench.domain.types import OrderSide, OrderStatus


@dataclass(frozen=True, slots=True)
class StrategyMetadata:
    strategy_id: str
    version: str
    display_name: str


@dataclass(frozen=True, slots=True)
class DataRequirements:
    timeframes: tuple[str, ...]
    minimum_closed_bars: int

    def __post_init__(self) -> None:
        if not self.timeframes:
            raise ValueError("at least one timeframe is required")
        if self.minimum_closed_bars <= 0:
            raise ValueError("minimum_closed_bars must be positive")


@dataclass(frozen=True, slots=True)
class ProtectionSnapshot:
    confirmed_stop: Decimal | None = None
    confirmed_take_profit: Decimal | None = None
    trailing_distance: Decimal | None = None


@dataclass(frozen=True, slots=True)
class PendingEntrySnapshot:
    client_order_id: str
    side: OrderSide
    price: Decimal
    original_quantity: Decimal
    remaining_quantity: Decimal
    status: OrderStatus
    age_bars: int

    def __post_init__(self) -> None:
        if not self.client_order_id or self.price <= 0 or self.original_quantity <= 0:
            raise ValueError("pending entry snapshot contains invalid order data")
        if self.remaining_quantity < 0 or self.remaining_quantity > self.original_quantity:
            raise ValueError("pending remaining quantity is outside the order quantity")
        if self.age_bars < 0:
            raise ValueError("pending entry age cannot be negative")


@dataclass(frozen=True, slots=True)
class StrategyHealthSnapshot:
    healthy: bool = True
    new_entries_allowed: bool = True
    detail: str = "healthy"


class IntentOutcomeStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    SUBMITTED = "submitted"
    CANCELLED = "cancelled"
    FILLED = "filled"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class IntentOutcome:
    intent_id: str
    status: IntentOutcomeStatus
    observed_at: datetime
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.intent_id or len(self.intent_id) > 36:
            raise ValueError("intent outcome id must contain 1..36 characters")
        if self.observed_at.tzinfo is None:
            raise ValueError("intent outcome timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ReadOnlyStrategyContext:
    symbol: str
    latest_price: Decimal
    position: Position
    parameters: Mapping[str, object]
    mark_price: Decimal | None = None
    protection: ProtectionSnapshot = ProtectionSnapshot()
    pending_entry: PendingEntrySnapshot | None = None
    latest_execution: Execution | None = None
    health: StrategyHealthSnapshot = StrategyHealthSnapshot()
    restored_state_version: str | None = None
    tick_size: Decimal | None = None

    def __post_init__(self) -> None:
        if self.latest_price <= 0:
            raise ValueError("latest_price must be positive")
        if self.mark_price is not None and self.mark_price <= 0:
            raise ValueError("mark_price must be positive")
        if self.tick_size is not None and self.tick_size <= 0:
            raise ValueError("tick_size must be positive")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


TradeIntent = EnterIntent | ExitIntent | UpdateProtectionIntent | CancelEntryIntent | NoOpIntent


class Strategy(Protocol):
    def metadata(self) -> StrategyMetadata: ...

    def required_data(self) -> DataRequirements: ...

    def default_parameters(self) -> Mapping[str, object]: ...

    def warmup_bars(self, parameters: Mapping[str, object]) -> int: ...

    def snapshot_state(self) -> Mapping[str, Any]: ...

    def restore_state(self, snapshot: Mapping[str, Any]) -> None: ...

    async def on_start(self, context: ReadOnlyStrategyContext) -> None: ...

    async def on_bar_closed(
        self,
        context: ReadOnlyStrategyContext,
        bar: Candle,
    ) -> Sequence[TradeIntent]: ...

    async def on_execution(
        self,
        context: ReadOnlyStrategyContext,
        execution: Execution,
    ) -> Sequence[TradeIntent]: ...

    async def on_intent_outcome(
        self,
        context: ReadOnlyStrategyContext,
        outcome: IntentOutcome,
    ) -> Sequence[TradeIntent]: ...

    async def on_reconcile(self, context: ReadOnlyStrategyContext) -> None: ...

    async def on_stop(self, reason: str) -> None: ...
