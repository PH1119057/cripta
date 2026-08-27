from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from bybit_workbench.domain.models import require_positive
from bybit_workbench.domain.types import PositionSide
from bybit_workbench.risk.engine import ceil_to_step, floor_to_step


@dataclass(frozen=True, slots=True)
class StopContext:
    side: PositionSide
    entry_price: Decimal
    reference_price: Decimal
    tick_size: Decimal
    current_stop: Decimal | None = None
    atr: Decimal | None = None

    def __post_init__(self) -> None:
        if self.side is PositionSide.FLAT:
            raise ValueError("stop context requires an open position")
        for name in ("entry_price", "reference_price", "tick_size"):
            require_positive(getattr(self, name), name)
        if self.current_stop is not None:
            require_positive(self.current_stop, "current_stop")
        if self.atr is not None:
            require_positive(self.atr, "atr")


class StopPolicy(Protocol):
    def calculate(self, context: StopContext) -> Decimal: ...


class ProtectionLevelStatus(StrEnum):
    PLANNED = "planned"
    REQUESTED = "requested"
    CONFIRMED = "confirmed"


@dataclass(frozen=True, slots=True)
class ProtectionLevel:
    price: Decimal
    status: ProtectionLevelStatus

    def __post_init__(self) -> None:
        require_positive(self.price, "protection price")


class RiskExpansionError(RuntimeError):
    pass


def validate_stop_update(
    current_stop: Decimal,
    proposed_stop: Decimal,
    side: PositionSide,
    *,
    allow_risk_expansion: bool = False,
) -> None:
    require_positive(current_stop, "current_stop")
    require_positive(proposed_stop, "proposed_stop")
    widens_risk = (side is PositionSide.LONG and proposed_stop < current_stop) or (
        side is PositionSide.SHORT and proposed_stop > current_stop
    )
    if side is PositionSide.FLAT:
        raise ValueError("flat position cannot update a stop")
    if widens_risk and not allow_risk_expansion:
        raise RiskExpansionError("stop update would widen position risk")


def normalize_protective_stop(
    value: Decimal,
    side: PositionSide,
    tick_size: Decimal,
) -> Decimal:
    if side is PositionSide.LONG:
        return floor_to_step(value, tick_size)
    if side is PositionSide.SHORT:
        return ceil_to_step(value, tick_size)
    raise ValueError("flat position cannot have a protective stop")


def enforce_monotonic(candidate: Decimal, context: StopContext) -> Decimal:
    require_positive(candidate, "candidate stop")
    normalized = normalize_protective_stop(candidate, context.side, context.tick_size)
    if context.current_stop is None:
        return normalized
    if context.side is PositionSide.LONG:
        return max(context.current_stop, normalized)
    return min(context.current_stop, normalized)


@dataclass(frozen=True, slots=True)
class FixedPriceStop:
    price: Decimal

    def calculate(self, context: StopContext) -> Decimal:
        require_positive(self.price, "price")
        _validate_protective_side(self.price, context)
        return enforce_monotonic(self.price, context)


@dataclass(frozen=True, slots=True)
class PercentStop:
    percent: Decimal

    def calculate(self, context: StopContext) -> Decimal:
        require_positive(self.percent, "percent")
        distance = context.entry_price * self.percent / Decimal("100")
        candidate = _subtract_for_long(context.entry_price, distance, context.side)
        _validate_protective_side(candidate, context)
        return enforce_monotonic(candidate, context)


@dataclass(frozen=True, slots=True)
class DistanceStop:
    distance: Decimal

    def calculate(self, context: StopContext) -> Decimal:
        require_positive(self.distance, "distance")
        candidate = _subtract_for_long(context.entry_price, self.distance, context.side)
        _validate_protective_side(candidate, context)
        return enforce_monotonic(candidate, context)


@dataclass(frozen=True, slots=True)
class ATRStop:
    multiplier: Decimal

    def calculate(self, context: StopContext) -> Decimal:
        require_positive(self.multiplier, "multiplier")
        if context.atr is None:
            raise ValueError("ATR value is required")
        candidate = _subtract_for_long(
            context.entry_price,
            context.atr * self.multiplier,
            context.side,
        )
        _validate_protective_side(candidate, context)
        return enforce_monotonic(candidate, context)


@dataclass(frozen=True, slots=True)
class TrailingDistanceStop:
    distance: Decimal

    def calculate(self, context: StopContext) -> Decimal:
        require_positive(self.distance, "distance")
        candidate = _subtract_for_long(context.reference_price, self.distance, context.side)
        return enforce_monotonic(candidate, context)


@dataclass(frozen=True, slots=True)
class TrailingPercentStop:
    percent: Decimal

    def calculate(self, context: StopContext) -> Decimal:
        require_positive(self.percent, "percent")
        distance = context.reference_price * self.percent / Decimal("100")
        candidate = _subtract_for_long(context.reference_price, distance, context.side)
        return enforce_monotonic(candidate, context)


def _subtract_for_long(base: Decimal, distance: Decimal, side: PositionSide) -> Decimal:
    return base - distance if side is PositionSide.LONG else base + distance


def _validate_protective_side(candidate: Decimal, context: StopContext) -> None:
    require_positive(candidate, "candidate stop")
    if context.side is PositionSide.LONG and candidate >= context.entry_price:
        raise ValueError("initial long stop must be below entry")
    if context.side is PositionSide.SHORT and candidate <= context.entry_price:
        raise ValueError("initial short stop must be above entry")
