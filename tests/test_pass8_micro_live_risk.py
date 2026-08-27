import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bybit_workbench.domain import EnterIntent, OrderRequest
from bybit_workbench.domain.types import OrderSide, OrderType, PositionSide
from bybit_workbench.execution.mainnet_safety import MicroLiveEntryPlan
from bybit_workbench.risk import RiskCheck, RiskDecision, RiskProfile, default_risk_profile_settings
from bybit_workbench.ui.mainnet_execution_runtime import (
    default_micro_live_limits,
    micro_live_entry_plan,
)
from bybit_workbench.ui.manual_workflow import PreparedManualTrade

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 14, tzinfo=UTC)


def prepared(
    *,
    risk_percent: Decimal = Decimal("1.00"),
    order_type: OrderType = OrderType.LIMIT,
) -> PreparedManualTrade:
    order = OrderRequest(
        "micro-live-risk-1",
        "UNIUSDT",
        OrderSide.BUY,
        order_type,
        Decimal("2.0"),
        Decimal("3.000") if order_type is OrderType.LIMIT else None,
    )
    intent = EnterIntent(
        "micro-live-risk-1",
        "UNIUSDT",
        PositionSide.LONG,
        order_type,
        Decimal("3.000"),
        Decimal("2.900"),
        Decimal("1"),
        "operator-confirmed smoke",
        Decimal("3.300"),
    )
    decision = RiskDecision(
        True,
        (RiskCheck("fixture", True, "approved"),),
        order,
        Decimal("2.900"),
        Decimal("0.21"),
        Decimal("0.205"),
        Decimal("0.004"),
        Decimal("0.001"),
        normalized_entry=Decimal("3.000"),
    )
    profile = RiskProfile(
        max_risk_amount=Decimal("0"),
        max_risk_percent=risk_percent,
        max_position_notional=Decimal("1000"),
        max_leverage=Decimal("1"),
        max_daily_loss=Decimal("0"),
        max_consecutive_losses=3,
        max_open_positions=1,
        max_pending_entries=1,
        max_slippage_percent=Decimal("0.1"),
        estimated_fee_rate=Decimal("0.00055"),
        max_market_data_age_seconds=Decimal("10"),
        max_private_stream_age_seconds=Decimal("30"),
        allowed_symbols=frozenset({"UNIUSDT"}),
        allowed_directions=frozenset({PositionSide.LONG, PositionSide.SHORT}),
        max_daily_loss_percent=Decimal("3"),
    )
    return PreparedManualTrade(
        "run-pass8",
        "decision-pass8",
        "risk-pass8",
        intent,
        decision,
        profile,
        NOW,
    )


class Pass8RiskContractTests(unittest.TestCase):
    def test_default_percentage_risk_is_editable_one_percent_with_absolute_cap_off(self) -> None:
        settings = default_risk_profile_settings()
        self.assertEqual(settings.max_risk_percent, Decimal("1.00"))
        self.assertEqual(settings.max_risk_amount, Decimal("0"))
        self.assertEqual(settings.max_leverage, Decimal("1"))
        self.assertEqual(settings.max_daily_loss_percent, Decimal("3.00"))
        self.assertEqual(settings.max_daily_loss, Decimal("0"))

    def test_micro_live_ticket_plan_preserves_percentage_risk_and_exact_order(self) -> None:
        plan = micro_live_entry_plan(prepared())
        self.assertEqual(plan.risk_percent, Decimal("1.00"))
        self.assertEqual(plan.risk_budget, Decimal("0.21"))
        self.assertEqual(plan.estimated_loss_at_stop, Decimal("0.205"))
        self.assertEqual(plan.quantity, Decimal("2.0"))
        self.assertEqual(plan.limit_price, Decimal("3.000"))
        self.assertEqual(plan.stop_loss, Decimal("2.900"))
        self.assertEqual(plan.take_profit, Decimal("3.300"))

    def test_market_micro_live_plan_preserves_reference_but_has_market_type(self) -> None:
        selected = prepared(order_type=OrderType.MARKET)
        plan = micro_live_entry_plan(selected)
        self.assertEqual(plan.order_type, OrderType.MARKET)
        self.assertEqual(plan.limit_price, Decimal("3.000"))
        self.assertIsNone(selected.decision.normalized_order.price)

    def test_market_micro_live_limits_allow_small_mark_drift_but_stay_bounded(self) -> None:
        selected = prepared(order_type=OrderType.MARKET)
        limits = default_micro_live_limits(selected)
        self.assertEqual(limits.max_order_notional, Decimal("6.120000"))
        self.assertEqual(limits.max_total_exposure, Decimal("6.120000"))
        self.assertLessEqual(
            limits.max_order_notional,
            selected.risk_profile.max_position_notional,
        )

    def test_micro_live_limits_are_derived_from_exact_checked_plan(self) -> None:
        selected = prepared()
        limits = default_micro_live_limits(selected)
        self.assertEqual(limits.allowed_symbols, frozenset({"UNIUSDT"}))
        self.assertEqual(limits.max_order_notional, Decimal("6.0000"))
        self.assertEqual(limits.max_total_exposure, Decimal("6.0000"))
        self.assertEqual(limits.max_daily_loss, Decimal("0.63"))
        self.assertEqual(limits.max_orders_per_interval, 1)
        self.assertEqual(limits.required_leverage, Decimal("1"))

    def test_micro_live_rejects_unreasonably_large_risk_percentage(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot exceed 10%"):
            MicroLiveEntryPlan(
                "UNIUSDT",
                "micro-risk-over-10",
                OrderSide.BUY,
                Decimal("1"),
                Decimal("3"),
                Decimal("2.9"),
                None,
                Decimal("10.01"),
                Decimal("2"),
                Decimal("1"),
            )

    def test_gui_exposes_limit_and_market_with_auto_exchange_slippage(self) -> None:
        source = (ROOT / "src/bybit_workbench/ui/main_window.py").read_text(encoding="utf-8")
        self.assertIn(
            'self.entry_order_type_combo.addItem("Рыночный", OrderType.MARKET.value)',
            source,
        )
        self.assertIn("slippageTolerance не задаётся", source)
        self.assertIn("Risk slippage reserve, %", source)

    def test_gui_risk_and_market_changes_invalidate_checked_plan(self) -> None:
        source = (ROOT / "src/bybit_workbench/ui/main_window.py").read_text(encoding="utf-8")
        self.assertIn('self.risk_percent_input = QLineEdit("1.00")', source)
        self.assertIn('self.risk_amount_input = QLineEdit("0")', source)
        self.assertIn("self.risk_percent_input,", source)
        self.assertIn("self.timeframe_combo.currentTextChanged.connect", source)
        self.assertIn("self.strategy_combo.currentIndexChanged.connect", source)
        self.assertIn("self._invalidate_manual_plan", source)

    def test_mainnet_still_requires_external_live_switch(self) -> None:
        config = (ROOT / "src/bybit_workbench/app/config.py").read_text(encoding="utf-8")
        self.assertIn("allow_live_trading: bool = False", config)
        runtime = (ROOT / "src/bybit_workbench/ui/mainnet_execution_runtime.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("external BYBIT_WORKBENCH_ALLOW_LIVE_TRADING switch is off", runtime)


if __name__ == "__main__":
    unittest.main()
