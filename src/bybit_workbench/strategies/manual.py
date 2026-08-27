from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from bybit_workbench.domain import Candle, EnterIntent, Execution
from bybit_workbench.domain.types import OrderType, PositionSide

from .base import (
    DataRequirements,
    IntentOutcome,
    ReadOnlyStrategyContext,
    StrategyMetadata,
    TradeIntent,
)

if TYPE_CHECKING:
    from .registry import StrategyRegistry


class ManualProtectedTrade:
    """Operator-supplied signal adapter; it never invents an entry on market data."""

    def metadata(self) -> StrategyMetadata:
        return StrategyMetadata(
            "manual_protected_trade",
            "1.0",
            "Manual protected trade",
        )

    def required_data(self) -> DataRequirements:
        return DataRequirements(("1", "3", "5", "15", "30", "60", "240", "D"), 1)

    def default_parameters(self) -> Mapping[str, object]:
        return {}

    def warmup_bars(self, parameters: Mapping[str, object]) -> int:
        return 1

    def snapshot_state(self) -> Mapping[str, Any]:
        return {"version": "1.0"}

    def restore_state(self, snapshot: Mapping[str, Any]) -> None:
        if snapshot.get("version") != "1.0":
            raise ValueError("unsupported manual strategy state version")

    async def on_start(self, context: ReadOnlyStrategyContext) -> None:
        return None

    async def on_bar_closed(
        self,
        context: ReadOnlyStrategyContext,
        bar: Candle,
    ) -> Sequence[TradeIntent]:
        return ()

    async def on_execution(
        self,
        context: ReadOnlyStrategyContext,
        execution: Execution,
    ) -> Sequence[TradeIntent]:
        return ()

    async def on_intent_outcome(
        self,
        context: ReadOnlyStrategyContext,
        outcome: IntentOutcome,
    ) -> Sequence[TradeIntent]:
        return ()

    async def on_reconcile(self, context: ReadOnlyStrategyContext) -> None:
        return None

    async def on_stop(self, reason: str) -> None:
        return None

    def create_entry(
        self,
        *,
        intent_id: str,
        symbol: str,
        direction: PositionSide,
        entry_price: Decimal,
        stop_price: Decimal,
        leverage: Decimal,
        reason: str,
        take_profit: Decimal | None = None,
    ) -> EnterIntent:
        return EnterIntent(
            intent_id=intent_id,
            symbol=symbol,
            direction=direction,
            order_type=OrderType.LIMIT,
            entry_price=entry_price,
            stop_price=stop_price,
            leverage=leverage,
            reason=reason,
            take_profit=take_profit,
        )


def default_strategy_registry() -> StrategyRegistry:
    from .power_channel import PowerChannelRejection
    from .registry import (
        ParameterType,
        StrategyKind,
        StrategyParameter,
        StrategyRegistration,
        StrategyRegistry,
    )
    from .trend_breakout import TrendBreakoutRetest

    registry = StrategyRegistry()
    manual = ManualProtectedTrade()
    registry.register(
        StrategyRegistration(
            metadata=manual.metadata(),
            kind=StrategyKind.MANUAL,
            factory=ManualProtectedTrade,
            requires_historical_validation=False,
        )
    )
    trend = TrendBreakoutRetest()
    registry.register(
        StrategyRegistration(
            metadata=trend.metadata(),
            kind=StrategyKind.AUTOMATIC,
            factory=TrendBreakoutRetest,
            parameters=(
                StrategyParameter(
                    "entry_lookback",
                    "Entry lookback",
                    ParameterType.INTEGER,
                    55,
                    minimum=20,
                    maximum=200,
                ),
                StrategyParameter(
                    "atr_period", "ATR period", ParameterType.INTEGER, 20, minimum=5, maximum=100
                ),
                StrategyParameter(
                    "initial_stop_atr",
                    "Initial stop ATR",
                    ParameterType.DECIMAL,
                    Decimal("2.0"),
                    minimum=Decimal("0.5"),
                    maximum=Decimal("10"),
                ),
                StrategyParameter(
                    "trailing_stop_atr",
                    "Trailing stop ATR",
                    ParameterType.DECIMAL,
                    Decimal("3.0"),
                    minimum=Decimal("0.5"),
                    maximum=Decimal("15"),
                ),
                StrategyParameter(
                    "entry_valid_bars",
                    "Entry valid bars",
                    ParameterType.INTEGER,
                    2,
                    minimum=1,
                    maximum=10,
                ),
                StrategyParameter(
                    "cooldown_bars",
                    "Cooldown bars",
                    ParameterType.INTEGER,
                    1,
                    minimum=0,
                    maximum=100,
                ),
                StrategyParameter(
                    "requested_leverage",
                    "Requested leverage",
                    ParameterType.DECIMAL,
                    Decimal("1"),
                    minimum=Decimal("1"),
                    maximum=Decimal("10"),
                ),
                StrategyParameter(
                    "direction_mode",
                    "Direction",
                    ParameterType.TEXT,
                    "both",
                    choices=("long", "short", "both"),
                ),
                StrategyParameter(
                    "take_profit_r",
                    "Take profit R",
                    ParameterType.DECIMAL,
                    Decimal("0"),
                    minimum=Decimal("0"),
                    maximum=Decimal("20"),
                ),
                StrategyParameter(
                    "exit_on_opposite_breakout",
                    "Exit on opposite breakout",
                    ParameterType.BOOLEAN,
                    True,
                ),
            ),
        )
    )
    power = PowerChannelRejection()
    registry.register(
        StrategyRegistration(
            metadata=power.metadata(),
            kind=StrategyKind.AUTOMATIC,
            factory=PowerChannelRejection,
            parameters=(
                StrategyParameter(
                    "range_lookback",
                    "Range lookback",
                    ParameterType.INTEGER,
                    130,
                    minimum=20,
                    maximum=300,
                ),
                StrategyParameter(
                    "atr_period", "ATR period", ParameterType.INTEGER, 200, minimum=20, maximum=300
                ),
                StrategyParameter(
                    "zone_half_width_atr",
                    "Zone half-width ATR",
                    ParameterType.DECIMAL,
                    Decimal("0.5"),
                    minimum=Decimal("0.1"),
                    maximum=Decimal("3"),
                ),
                StrategyParameter(
                    "min_center_range_atr",
                    "Minimum center range ATR",
                    ParameterType.DECIMAL,
                    Decimal("3.0"),
                    minimum=Decimal("1"),
                    maximum=Decimal("50"),
                ),
                StrategyParameter(
                    "confirmation_bars",
                    "Confirmation bars",
                    ParameterType.INTEGER,
                    1,
                    minimum=1,
                    maximum=1,
                ),
                StrategyParameter(
                    "entry_valid_bars",
                    "Entry valid bars",
                    ParameterType.INTEGER,
                    2,
                    minimum=1,
                    maximum=10,
                ),
                StrategyParameter(
                    "stop_buffer_atr",
                    "Stop buffer ATR",
                    ParameterType.DECIMAL,
                    Decimal("0.1"),
                    minimum=Decimal("0"),
                    maximum=Decimal("3"),
                ),
                StrategyParameter(
                    "minimum_reward_risk",
                    "Minimum reward/risk",
                    ParameterType.DECIMAL,
                    Decimal("1.0"),
                    minimum=Decimal("0"),
                    maximum=Decimal("20"),
                ),
                StrategyParameter(
                    "trailing_activation_r",
                    "Trailing activation R",
                    ParameterType.DECIMAL,
                    Decimal("1.0"),
                    minimum=Decimal("0"),
                    maximum=Decimal("20"),
                ),
                StrategyParameter(
                    "cooldown_bars",
                    "Cooldown bars",
                    ParameterType.INTEGER,
                    3,
                    minimum=0,
                    maximum=100,
                ),
                StrategyParameter(
                    "requested_leverage",
                    "Requested leverage",
                    ParameterType.DECIMAL,
                    Decimal("1"),
                    minimum=Decimal("1"),
                    maximum=Decimal("10"),
                ),
                StrategyParameter(
                    "direction_mode",
                    "Direction",
                    ParameterType.TEXT,
                    "both",
                    choices=("long", "short", "both"),
                ),
                StrategyParameter(
                    "take_profit_mode",
                    "Take profit mode",
                    ParameterType.TEXT,
                    "midline",
                    choices=("midline", "none"),
                ),
                StrategyParameter(
                    "use_candle_power_filter", "Use Candle Power", ParameterType.BOOLEAN, False
                ),
                StrategyParameter(
                    "minimum_power_share",
                    "Minimum power share",
                    ParameterType.DECIMAL,
                    Decimal("0.55"),
                    minimum=Decimal("0.5"),
                    maximum=Decimal("1"),
                ),
            ),
        )
    )
    return registry
