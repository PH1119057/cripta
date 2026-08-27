from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from bybit_workbench.domain.types import PositionSide

from .models import RiskProfile


class RiskProfileSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile_name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)
    max_risk_amount: Decimal = Field(ge=0)
    max_risk_percent: Decimal = Field(ge=0, le=100)
    max_position_notional: Decimal = Field(gt=0)
    max_leverage: Decimal = Field(gt=0)
    max_daily_loss: Decimal = Field(ge=0)
    max_daily_loss_percent: Decimal = Field(ge=0, le=100, default=Decimal("0"))
    max_slippage_percent: Decimal = Field(ge=0, le=100)
    estimated_fee_rate: Decimal = Field(ge=0, le=1)

    def to_domain(self, symbol: str) -> RiskProfile:
        return RiskProfile(
            max_risk_amount=self.max_risk_amount,
            max_risk_percent=self.max_risk_percent,
            max_position_notional=self.max_position_notional,
            max_leverage=self.max_leverage,
            max_daily_loss=self.max_daily_loss,
            max_consecutive_losses=3,
            max_open_positions=1,
            max_pending_entries=1,
            max_slippage_percent=self.max_slippage_percent,
            estimated_fee_rate=self.estimated_fee_rate,
            max_market_data_age_seconds=Decimal("10"),
            max_private_stream_age_seconds=Decimal("30"),
            allowed_symbols=frozenset({symbol.strip().upper()}),
            allowed_directions=frozenset({PositionSide.LONG, PositionSide.SHORT}),
            max_daily_loss_percent=self.max_daily_loss_percent,
        )


def default_risk_profile_settings() -> RiskProfileSettings:
    return RiskProfileSettings(
        profile_name="Default",
        version="1",
        max_risk_amount=Decimal("0"),
        max_risk_percent=Decimal("1.00"),
        max_position_notional=Decimal("1000"),
        max_leverage=Decimal("1"),
        max_daily_loss=Decimal("0"),
        max_daily_loss_percent=Decimal("3.00"),
        max_slippage_percent=Decimal("0.1"),
        estimated_fee_rate=Decimal("0.0006"),
    )
