from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from bybit_workbench.domain.models import require_positive
from bybit_workbench.domain.types import FillReason, OrderSide


class AmbiguousBarPolicy(StrEnum):
    CONSERVATIVE = "conservative"


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    fee_rate: Decimal = Decimal("0.0006")
    maker_fee_rate: Decimal | None = None
    taker_fee_rate: Decimal | None = None
    slippage_percent: Decimal = Decimal("0.1")
    max_fill_quantity_per_bar: Decimal | None = None
    ambiguous_bar_policy: AmbiguousBarPolicy = AmbiguousBarPolicy.CONSERVATIVE
    seed: int = 0
    execution_delay_bars: int = 0

    def __post_init__(self) -> None:
        if self.fee_rate < 0 or self.fee_rate > 1:
            raise ValueError("fee_rate must be between 0 and 1")
        for name in ("maker_fee_rate", "taker_fee_rate"):
            rate = getattr(self, name)
            if rate is not None and (rate < Decimal("-1") or rate > 1):
                raise ValueError(f"{name} must be between -1 and 1")
        if self.slippage_percent < 0 or self.slippage_percent > 100:
            raise ValueError("slippage_percent must be between 0 and 100")
        if self.max_fill_quantity_per_bar is not None:
            require_positive(self.max_fill_quantity_per_bar, "max_fill_quantity_per_bar")
        if self.execution_delay_bars < 0:
            raise ValueError("execution_delay_bars cannot be negative")

    @property
    def effective_maker_fee_rate(self) -> Decimal:
        return self.fee_rate if self.maker_fee_rate is None else self.maker_fee_rate

    @property
    def effective_taker_fee_rate(self) -> Decimal:
        return self.fee_rate if self.taker_fee_rate is None else self.taker_fee_rate


@dataclass(frozen=True, slots=True)
class ProtectionPlan:
    stop_price: Decimal
    take_profit: Decimal | None = None

    def __post_init__(self) -> None:
        require_positive(self.stop_price, "stop_price")
        if self.take_profit is not None:
            require_positive(self.take_profit, "take_profit")


@dataclass(frozen=True, slots=True)
class ReplayFill:
    execution_id: str
    client_order_id: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    fee: Decimal
    reason: FillReason
    occurred_at: datetime
    ambiguous_bar: bool = False
    slippage_cost: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.slippage_cost < 0 or not self.slippage_cost.is_finite():
            raise ValueError("slippage_cost must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class ReplayTradeResult:
    symbol: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    fees: Decimal
    funding: Decimal
    net_pnl: Decimal
    exit_reason: FillReason
    opened_at: datetime
    closed_at: datetime
    ambiguous_bar: bool = False
