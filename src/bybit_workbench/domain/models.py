from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from .types import OrderRole, OrderSide, OrderStatus, OrderType, PositionSide


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_positive(value: Decimal, name: str) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class Candle:
    symbol: str
    timeframe: str
    opened_at: datetime
    closed_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_closed: bool = True

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol is required")
        if self.opened_at.tzinfo is None or self.closed_at.tzinfo is None:
            raise ValueError("candle timestamps must be timezone-aware")
        if self.closed_at <= self.opened_at:
            raise ValueError("closed_at must be after opened_at")
        for name in ("open", "high", "low", "close"):
            require_positive(getattr(self, name), name)
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("OHLC values are inconsistent")
        if self.low > self.high:
            raise ValueError("low cannot exceed high")
        if self.volume < 0:
            raise ValueError("volume cannot be negative")


@dataclass(frozen=True, slots=True)
class InstrumentRules:
    symbol: str
    tick_size: Decimal
    qty_step: Decimal
    min_order_qty: Decimal
    min_notional: Decimal
    max_order_qty: Decimal
    max_market_order_qty: Decimal | None = None

    def __post_init__(self) -> None:
        for name in (
            "tick_size",
            "qty_step",
            "min_order_qty",
            "min_notional",
            "max_order_qty",
        ):
            require_positive(getattr(self, name), name)
        if self.min_order_qty > self.max_order_qty:
            raise ValueError("min_order_qty cannot exceed max_order_qty")
        if self.max_market_order_qty is not None:
            require_positive(self.max_market_order_qty, "max_market_order_qty")


@dataclass(frozen=True, slots=True)
class OrderRequest:
    client_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Decimal | None = None
    reduce_only: bool = False
    role: OrderRole = OrderRole.ENTRY

    def __post_init__(self) -> None:
        if not self.client_order_id or len(self.client_order_id) > 36:
            raise ValueError("client_order_id must contain 1..36 characters")
        require_positive(self.quantity, "quantity")
        if self.order_type is OrderType.LIMIT and self.price is None:
            raise ValueError("limit order requires price")
        if self.price is not None:
            require_positive(self.price, "price")
        if self.role in {OrderRole.EXIT, OrderRole.PROTECTIVE} and not self.reduce_only:
            raise ValueError("exit and protective orders must be reduce-only")
        if self.role is OrderRole.ENTRY and self.reduce_only:
            raise ValueError("entry orders cannot be reduce-only")


@dataclass(slots=True)
class Order:
    order_id: str
    request: OrderRequest
    status: OrderStatus = OrderStatus.CREATED
    filled_quantity: Decimal = Decimal("0")
    average_price: Decimal | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def remaining_quantity(self) -> Decimal:
        return self.request.quantity - self.filled_quantity


@dataclass(frozen=True, slots=True)
class Execution:
    execution_id: str
    order_id: str
    client_order_id: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    executed_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    side: PositionSide
    quantity: Decimal
    average_price: Decimal | None

    def __post_init__(self) -> None:
        if self.quantity < 0:
            raise ValueError("position quantity cannot be negative")
        if self.side is PositionSide.FLAT and self.quantity != 0:
            raise ValueError("flat position must have zero quantity")
        if self.side is not PositionSide.FLAT and self.quantity == 0:
            raise ValueError("non-flat position must have positive quantity")
