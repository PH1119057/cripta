from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from bybit_workbench.domain.models import OrderRequest
from bybit_workbench.domain.types import PositionSide


@dataclass(frozen=True, slots=True)
class RiskProfile:
    max_risk_amount: Decimal
    max_risk_percent: Decimal
    max_position_notional: Decimal
    max_leverage: Decimal
    max_daily_loss: Decimal
    max_consecutive_losses: int
    max_open_positions: int
    max_pending_entries: int
    max_slippage_percent: Decimal
    estimated_fee_rate: Decimal
    max_market_data_age_seconds: Decimal
    max_private_stream_age_seconds: Decimal
    allowed_symbols: frozenset[str]
    allowed_directions: frozenset[PositionSide]
    prohibit_position_increase: bool = True
    allowed_utc_hours: frozenset[int] = frozenset(range(24))
    min_liquidation_buffer_percent: Decimal = Decimal("0")
    max_daily_loss_percent: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        positive_fields = (
            "max_position_notional",
            "max_leverage",
            "max_market_data_age_seconds",
            "max_private_stream_age_seconds",
        )
        for name in positive_fields:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.max_risk_amount < 0 or self.max_risk_percent < 0:
            raise ValueError("risk limits cannot be negative")
        if self.max_risk_amount == 0 and self.max_risk_percent == 0:
            raise ValueError("at least one risk limit must be enabled")
        if self.max_daily_loss < 0 or self.max_daily_loss_percent < 0:
            raise ValueError("daily loss limits cannot be negative")
        if self.max_daily_loss == 0 and self.max_daily_loss_percent == 0:
            raise ValueError("at least one daily loss limit must be enabled")
        if self.max_slippage_percent < 0 or self.estimated_fee_rate < 0:
            raise ValueError("slippage and fee rate cannot be negative")
        if (
            self.max_risk_percent > 100
            or self.max_slippage_percent > 100
            or self.max_daily_loss_percent > 100
        ):
            raise ValueError("percentage limits cannot exceed 100")
        if self.estimated_fee_rate > 1:
            raise ValueError("fee rate cannot exceed 1")
        if self.min_liquidation_buffer_percent < 0:
            raise ValueError("liquidation buffer cannot be negative")
        if self.max_consecutive_losses < 1:
            raise ValueError("max_consecutive_losses must be positive")
        if self.max_open_positions < 1 or self.max_pending_entries < 1:
            raise ValueError("position and pending entry limits must be positive")
        if not self.allowed_symbols or not self.allowed_directions:
            raise ValueError("allowed symbols and directions cannot be empty")
        if PositionSide.FLAT in self.allowed_directions:
            raise ValueError("flat cannot be an allowed entry direction")
        if not self.allowed_utc_hours or any(
            hour not in range(24) for hour in self.allowed_utc_hours
        ):
            raise ValueError("allowed UTC hours must contain values from 0 to 23")

    def daily_loss_limit(self, equity: Decimal) -> Decimal:
        if equity <= 0:
            raise ValueError("equity must be positive")
        candidates: list[Decimal] = []
        if self.max_daily_loss > 0:
            candidates.append(self.max_daily_loss)
        if self.max_daily_loss_percent > 0:
            candidates.append(equity * self.max_daily_loss_percent / Decimal("100"))
        if not candidates:
            raise RuntimeError("daily loss profile has no enabled limits")
        return min(candidates)


@dataclass(frozen=True, slots=True)
class RiskContext:
    equity: Decimal
    available_balance: Decimal
    daily_realized_pnl: Decimal
    consecutive_losses: int
    open_positions: int
    pending_entries: int
    market_data_at: datetime
    private_stream_at: datetime
    evaluated_at: datetime
    current_position_side: PositionSide = PositionSide.FLAT
    position_is_protected: bool = False
    estimated_liquidation_price: Decimal | None = None
    cooldown_until: datetime | None = None

    def __post_init__(self) -> None:
        if self.equity <= 0 or self.available_balance < 0:
            raise ValueError("equity must be positive and available balance non-negative")
        if self.consecutive_losses < 0 or self.open_positions < 0 or self.pending_entries < 0:
            raise ValueError("risk counters cannot be negative")
        for value in (self.market_data_at, self.private_stream_at, self.evaluated_at):
            if value.tzinfo is None:
                raise ValueError("risk timestamps must be timezone-aware")
        if self.cooldown_until is not None and self.cooldown_until.tzinfo is None:
            raise ValueError("cooldown timestamp must be timezone-aware")
        if self.estimated_liquidation_price is not None and self.estimated_liquidation_price <= 0:
            raise ValueError("estimated liquidation price must be positive")


@dataclass(frozen=True, slots=True)
class RiskCheck:
    code: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    checks: tuple[RiskCheck, ...]
    normalized_order: OrderRequest | None = None
    normalized_stop: Decimal | None = None
    risk_budget: Decimal | None = None
    estimated_loss_at_stop: Decimal | None = None
    estimated_fees: Decimal | None = None
    estimated_slippage: Decimal | None = None
    normalized_entry: Decimal | None = None
    candidate_quantity: Decimal | None = None
    minimum_viable_quantity: Decimal | None = None
    minimum_viable_loss_at_stop: Decimal | None = None
    minimum_viable_risk_percent: Decimal | None = None

    @property
    def rejection_codes(self) -> tuple[str, ...]:
        return tuple(check.code for check in self.checks if not check.passed)
