from dataclasses import dataclass
from decimal import Decimal

from .models import require_positive
from .types import OrderType, PositionSide


@dataclass(frozen=True, slots=True)
class EnterIntent:
    intent_id: str
    symbol: str
    direction: PositionSide
    order_type: OrderType
    entry_price: Decimal
    stop_price: Decimal
    leverage: Decimal
    reason: str
    take_profit: Decimal | None = None
    requested_notional: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.intent_id or len(self.intent_id) > 36:
            raise ValueError("intent_id must contain 1..36 characters")
        if not self.symbol or not self.reason.strip():
            raise ValueError("symbol and reason are required")
        if self.direction is PositionSide.FLAT:
            raise ValueError("entry direction cannot be flat")
        for name in ("entry_price", "stop_price", "leverage"):
            require_positive(getattr(self, name), name)
        if self.direction is PositionSide.LONG and self.stop_price >= self.entry_price:
            raise ValueError("long stop must be below entry")
        if self.direction is PositionSide.SHORT and self.stop_price <= self.entry_price:
            raise ValueError("short stop must be above entry")
        if self.requested_notional is not None:
            require_positive(self.requested_notional, "requested_notional")
        if self.take_profit is not None:
            require_positive(self.take_profit, "take_profit")
            if self.direction is PositionSide.LONG and self.take_profit <= self.entry_price:
                raise ValueError("long take profit must be above entry")
            if self.direction is PositionSide.SHORT and self.take_profit >= self.entry_price:
                raise ValueError("short take profit must be below entry")


@dataclass(frozen=True, slots=True)
class ExitIntent:
    intent_id: str
    symbol: str
    reason: str

    def __post_init__(self) -> None:
        _validate_common(self.intent_id, self.symbol, self.reason)


@dataclass(frozen=True, slots=True)
class UpdateProtectionIntent:
    intent_id: str
    symbol: str
    reason: str
    stop_price: Decimal | None = None
    take_profit: Decimal | None = None

    def __post_init__(self) -> None:
        _validate_common(self.intent_id, self.symbol, self.reason)
        if self.stop_price is None and self.take_profit is None:
            raise ValueError("protection update requires stop or take profit")
        if self.stop_price is not None:
            require_positive(self.stop_price, "stop_price")
        if self.take_profit is not None:
            require_positive(self.take_profit, "take_profit")


@dataclass(frozen=True, slots=True)
class CancelEntryIntent:
    intent_id: str
    symbol: str
    reason: str

    def __post_init__(self) -> None:
        _validate_common(self.intent_id, self.symbol, self.reason)


@dataclass(frozen=True, slots=True)
class NoOpIntent:
    intent_id: str
    symbol: str
    reason: str

    def __post_init__(self) -> None:
        _validate_common(self.intent_id, self.symbol, self.reason)


def _validate_common(intent_id: str, symbol: str, reason: str) -> None:
    if not intent_id or len(intent_id) > 36:
        raise ValueError("intent_id must contain 1..36 characters")
    if not symbol or not reason.strip():
        raise ValueError("symbol and reason are required")
