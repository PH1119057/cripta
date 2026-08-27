import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.domain import EnterIntent, InstrumentRules
from bybit_workbench.domain.types import OrderType, PositionSide
from bybit_workbench.risk import RiskContext, RiskEngine, RiskProfile

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def profile(**overrides: object) -> RiskProfile:
    values: dict[str, object] = {
        "max_risk_amount": Decimal("100"),
        "max_risk_percent": Decimal("1"),
        "max_position_notional": Decimal("5000"),
        "max_leverage": Decimal("5"),
        "max_daily_loss": Decimal("300"),
        "max_consecutive_losses": 3,
        "max_open_positions": 1,
        "max_pending_entries": 1,
        "max_slippage_percent": Decimal("0.1"),
        "estimated_fee_rate": Decimal("0.0006"),
        "max_market_data_age_seconds": Decimal("5"),
        "max_private_stream_age_seconds": Decimal("10"),
        "allowed_symbols": frozenset({"BTCUSDT"}),
        "allowed_directions": frozenset({PositionSide.LONG, PositionSide.SHORT}),
    }
    values.update(overrides)
    return RiskProfile(**values)  # type: ignore[arg-type]


def context(**overrides: object) -> RiskContext:
    values: dict[str, object] = {
        "equity": Decimal("10000"),
        "available_balance": Decimal("1000"),
        "daily_realized_pnl": Decimal("0"),
        "consecutive_losses": 0,
        "open_positions": 0,
        "pending_entries": 0,
        "market_data_at": NOW - timedelta(seconds=1),
        "private_stream_at": NOW - timedelta(seconds=1),
        "evaluated_at": NOW,
    }
    values.update(overrides)
    return RiskContext(**values)  # type: ignore[arg-type]


def rules(**overrides: object) -> InstrumentRules:
    values: dict[str, object] = {
        "symbol": "BTCUSDT",
        "tick_size": Decimal("0.1"),
        "qty_step": Decimal("0.001"),
        "min_order_qty": Decimal("0.001"),
        "min_notional": Decimal("5"),
        "max_order_qty": Decimal("100"),
    }
    values.update(overrides)
    return InstrumentRules(**values)  # type: ignore[arg-type]


def long_intent(**overrides: object) -> EnterIntent:
    values: dict[str, object] = {
        "intent_id": "risk-test-1",
        "symbol": "BTCUSDT",
        "direction": PositionSide.LONG,
        "order_type": OrderType.LIMIT,
        "entry_price": Decimal("50000"),
        "stop_price": Decimal("49000"),
        "leverage": Decimal("5"),
        "reason": "manual protected trade",
    }
    values.update(overrides)
    return EnterIntent(**values)  # type: ignore[arg-type]


class RiskEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = RiskEngine()

    def test_approved_size_respects_budget_and_steps(self) -> None:
        decision = self.engine.evaluate_entry(long_intent(), profile(), context(), rules())
        self.assertTrue(decision.approved, decision.rejection_codes)
        self.assertIsNotNone(decision.normalized_order)
        order = decision.normalized_order
        assert order is not None
        self.assertEqual(order.quantity, Decimal("0.090"))
        self.assertEqual(order.quantity % Decimal("0.001"), 0)
        self.assertLessEqual(decision.estimated_loss_at_stop or Decimal("999"), Decimal("100"))

    def test_amount_only_risk_limit_is_supported(self) -> None:
        decision = self.engine.evaluate_entry(
            long_intent(),
            profile(max_risk_amount=Decimal("50"), max_risk_percent=Decimal("0")),
            context(),
            rules(),
        )
        self.assertTrue(decision.approved, decision.rejection_codes)
        self.assertEqual(decision.risk_budget, Decimal("50"))

    def test_percentage_risk_budget_scales_with_equity_when_absolute_cap_is_off(self) -> None:
        lower = self.engine.evaluate_entry(
            long_intent(),
            profile(max_risk_amount=Decimal("0"), max_risk_percent=Decimal("1")),
            context(equity=Decimal("2000"), available_balance=Decimal("2000")),
            rules(),
        )
        higher = self.engine.evaluate_entry(
            long_intent(),
            profile(max_risk_amount=Decimal("0"), max_risk_percent=Decimal("1")),
            context(equity=Decimal("4000"), available_balance=Decimal("4000")),
            rules(),
        )
        self.assertTrue(lower.approved, lower.rejection_codes)
        self.assertTrue(higher.approved, higher.rejection_codes)
        self.assertEqual(lower.risk_budget, Decimal("20"))
        self.assertEqual(higher.risk_budget, Decimal("40"))
        assert lower.normalized_order is not None
        assert higher.normalized_order is not None
        self.assertGreater(higher.normalized_order.quantity, lower.normalized_order.quantity)

    def test_stale_market_data_fails_closed(self) -> None:
        decision = self.engine.evaluate_entry(
            long_intent(),
            profile(),
            context(market_data_at=NOW - timedelta(seconds=6)),
            rules(),
        )
        self.assertFalse(decision.approved)
        self.assertIn("market_data_fresh", decision.rejection_codes)
        self.assertIsNone(decision.normalized_order)

    def test_future_private_timestamp_fails_closed(self) -> None:
        decision = self.engine.evaluate_entry(
            long_intent(),
            profile(),
            context(private_stream_at=NOW + timedelta(seconds=1)),
            rules(),
        )
        self.assertFalse(decision.approved)
        self.assertIn("private_timestamp_valid", decision.rejection_codes)

    def test_percentage_daily_loss_limit_scales_with_equity(self) -> None:
        decision = self.engine.evaluate_entry(
            long_intent(),
            profile(max_daily_loss=Decimal("0"), max_daily_loss_percent=Decimal("3")),
            context(
                equity=Decimal("20"),
                available_balance=Decimal("20"),
                daily_realized_pnl=Decimal("-0.61"),
            ),
            rules(),
        )
        self.assertFalse(decision.approved)
        self.assertIn("daily_loss_limit", decision.rejection_codes)
        detail = next(
            item.detail for item in decision.checks if item.code == "daily_loss_limit"
        )
        self.assertIn("limit=-0.6", detail)

    def test_daily_loss_boundary_blocks_entry(self) -> None:
        decision = self.engine.evaluate_entry(
            long_intent(),
            profile(),
            context(daily_realized_pnl=Decimal("-300")),
            rules(),
        )
        self.assertFalse(decision.approved)
        self.assertIn("daily_loss_limit", decision.rejection_codes)

    def test_existing_position_blocks_averaging(self) -> None:
        decision = self.engine.evaluate_entry(
            long_intent(),
            profile(),
            context(current_position_side=PositionSide.LONG),
            rules(),
        )
        self.assertFalse(decision.approved)
        self.assertIn("position_increase_forbidden", decision.rejection_codes)

    def test_unprotected_position_blocks_increase_when_averaging_enabled(self) -> None:
        decision = self.engine.evaluate_entry(
            long_intent(),
            profile(prohibit_position_increase=False, max_open_positions=2),
            context(
                current_position_side=PositionSide.LONG,
                open_positions=1,
                position_is_protected=False,
            ),
            rules(),
        )
        self.assertFalse(decision.approved)
        self.assertIn("position_protected_for_increase", decision.rejection_codes)

    def test_disallowed_utc_hour_blocks_entry(self) -> None:
        decision = self.engine.evaluate_entry(
            long_intent(),
            profile(allowed_utc_hours=frozenset({13})),
            context(),
            rules(),
        )
        self.assertFalse(decision.approved)
        self.assertIn("trading_hour_allowed", decision.rejection_codes)

    def test_missing_liquidation_estimate_fails_closed_when_buffer_required(self) -> None:
        decision = self.engine.evaluate_entry(
            long_intent(),
            profile(min_liquidation_buffer_percent=Decimal("2")),
            context(),
            rules(),
        )
        self.assertFalse(decision.approved)
        self.assertIn("liquidation_buffer", decision.rejection_codes)

    def test_stop_must_be_safely_ahead_of_liquidation(self) -> None:
        decision = self.engine.evaluate_entry(
            long_intent(),
            profile(min_liquidation_buffer_percent=Decimal("2")),
            context(estimated_liquidation_price=Decimal("47500")),
            rules(),
        )
        self.assertTrue(decision.approved, decision.rejection_codes)

    def test_too_small_quantity_is_rejected(self) -> None:
        decision = self.engine.evaluate_entry(
            long_intent(stop_price=Decimal("1")),
            profile(max_risk_amount=Decimal("1"), max_risk_percent=Decimal("0.001")),
            context(),
            rules(min_order_qty=Decimal("0.01")),
        )
        self.assertFalse(decision.approved)
        self.assertIn("minimum_quantity", decision.rejection_codes)


    def test_minimum_notional_rejection_keeps_diagnostic_sizing(self) -> None:
        decision = self.engine.evaluate_entry(
            long_intent(
                symbol="UNIUSDT",
                entry_price=Decimal("3.40"),
                stop_price=Decimal("3.00"),
                leverage=Decimal("1"),
            ),
            profile(
                max_risk_amount=Decimal("0"),
                max_risk_percent=Decimal("1"),
                max_position_notional=Decimal("1000"),
                max_leverage=Decimal("1"),
                allowed_symbols=frozenset({"UNIUSDT"}),
            ),
            context(equity=Decimal("20"), available_balance=Decimal("20")),
            rules(
                symbol="UNIUSDT",
                tick_size=Decimal("0.001"),
                qty_step=Decimal("0.1"),
                min_order_qty=Decimal("0.1"),
                min_notional=Decimal("5"),
                max_order_qty=Decimal("32000"),
            ),
        )
        self.assertFalse(decision.approved)
        self.assertIn("minimum_notional", decision.rejection_codes)
        self.assertEqual(decision.normalized_entry, Decimal("3.40"))
        self.assertIsNotNone(decision.candidate_quantity)
        self.assertEqual(decision.minimum_viable_quantity, Decimal("1.5"))
        self.assertIsNotNone(decision.minimum_viable_loss_at_stop)
        self.assertIsNotNone(decision.minimum_viable_risk_percent)
        detail = next(
            item.detail for item in decision.checks if item.code == "minimum_notional"
        )
        self.assertIn("exchange_min_qty=1.5", detail)
        self.assertIn("min_risk_pct=", detail)

    def test_many_price_remainders_never_exceed_risk_budget(self) -> None:
        for entry_remainder in range(1, 10):
            for stop_remainder in range(1, 10):
                entry = Decimal("50000") + Decimal(entry_remainder) / Decimal("100")
                stop = Decimal("49000") + Decimal(stop_remainder) / Decimal("100")
                decision = self.engine.evaluate_entry(
                    long_intent(entry_price=entry, stop_price=stop),
                    profile(),
                    context(),
                    rules(),
                )
                self.assertTrue(decision.approved, decision.rejection_codes)
                self.assertLessEqual(
                    decision.estimated_loss_at_stop or Decimal("999"),
                    decision.risk_budget or Decimal("0"),
                )

    def test_requested_notional_is_used_when_it_fits_risk_budget(self) -> None:
        decision = self.engine.evaluate_entry(
            long_intent(requested_notional=Decimal("100")),
            profile(max_risk_percent=Decimal("5")),
            context(available_balance=Decimal("1000")),
            rules(),
        )
        self.assertTrue(decision.approved, decision.rejection_codes)
        self.assertIsNotNone(decision.normalized_order)
        order = decision.normalized_order
        assert order is not None
        self.assertLessEqual(order.quantity * Decimal("50000"), Decimal("100"))
        self.assertGreater(order.quantity, Decimal("0"))

    def test_requested_notional_is_rejected_when_stop_risk_exceeds_budget(self) -> None:
        decision = self.engine.evaluate_entry(
            long_intent(requested_notional=Decimal("1000")),
            profile(max_risk_amount=Decimal("0"), max_risk_percent=Decimal("1")),
            context(equity=Decimal("1000"), available_balance=Decimal("1000")),
            rules(),
        )
        self.assertFalse(decision.approved)
        self.assertIn("risk_budget_respected", decision.rejection_codes)


if __name__ == "__main__":
    unittest.main()
