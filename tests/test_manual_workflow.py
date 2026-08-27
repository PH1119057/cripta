import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.domain.models import InstrumentRules, Order, OrderRequest, Position
from bybit_workbench.domain.types import (
    AppMode,
    AppState,
    OrderRole,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from bybit_workbench.exchange.bybit.health import BybitHealthSnapshot, ChannelHealth
from bybit_workbench.exchange.bybit.models import (
    AccountSnapshot,
    BybitPositionSnapshot,
    BybitReadSnapshot,
)
from bybit_workbench.ui.manual_workflow import ManualTradeDraft, ManualTradeWorkflow
from bybit_workbench.ui.view_model import WorkbenchViewModel

NOW = datetime(2026, 8, 13, 15, 0, tzinfo=UTC)


def ready_workflow():
    machine = AppStateMachine()
    machine.transition(AppState.SYNCING, "test sync")
    machine.transition(AppState.READY, "test ready")
    model = WorkbenchViewModel(AppMode.TESTNET)
    model.apply_read_snapshot(
        BybitReadSnapshot(
            InstrumentRules(
                "BTCUSDT",
                Decimal("0.1"),
                Decimal("0.001"),
                Decimal("0.001"),
                Decimal("5"),
                Decimal("100"),
            ),
            AccountSnapshot(
                "UNIFIED",
                Decimal("10000"),
                Decimal("1000"),
                Decimal("1000"),
                Decimal("0"),
                NOW,
            ),
            BybitPositionSnapshot(
                Position("BTCUSDT", PositionSide.FLAT, Decimal("0"), None),
                0,
                None,
                Decimal("60000"),
                None,
                None,
                None,
                None,
                Decimal("0"),
                1,
                NOW,
            ),
            (),
            NOW,
        )
    )
    fresh = ChannelHealth(True, True, NOW, None)
    model.apply_health(BybitHealthSnapshot(fresh, fresh, fresh))
    return machine, model, ManualTradeWorkflow(machine, model)


class ManualTradeWorkflowTests(unittest.TestCase):
    def test_check_arm_run_stop_happy_path(self):
        machine, model, workflow = ready_workflow()
        prepared = workflow.check(
            ManualTradeDraft(
                "BTCUSDT",
                PositionSide.LONG,
                Decimal("60000"),
                Decimal("59000"),
                Decimal("63000"),
                Decimal("1"),
            ),
            evaluated_at=NOW,
        )
        self.assertTrue(prepared.decision.approved)
        self.assertEqual(model.state.protection.planned_stop, Decimal("59000"))
        workflow.arm()
        self.assertEqual(machine.state, AppState.ARMED)
        workflow.run()
        self.assertEqual(machine.state, AppState.RUNNING)
        workflow.stop()
        self.assertEqual(machine.state, AppState.READY)
        self.assertIsNone(workflow.prepared)

    def test_market_draft_uses_market_order_without_exchange_price(self):
        _, _, workflow = ready_workflow()
        prepared = workflow.check(
            ManualTradeDraft(
                "BTCUSDT",
                PositionSide.LONG,
                Decimal("60000"),
                Decimal("59000"),
                Decimal("63000"),
                Decimal("1"),
                order_type=OrderType.MARKET,
            ),
            evaluated_at=NOW,
        )
        self.assertTrue(prepared.decision.approved)
        order = prepared.decision.normalized_order
        self.assertIsNotNone(order)
        assert order is not None
        self.assertEqual(order.order_type, OrderType.MARKET)
        self.assertIsNone(order.price)
        self.assertEqual(prepared.decision.normalized_entry, Decimal("60000"))

    def test_stale_channels_reject_and_cannot_arm(self):
        _, model, workflow = ready_workflow()
        stale = ChannelHealth(True, False, NOW - timedelta(minutes=5), None)
        model.apply_health(BybitHealthSnapshot(stale, stale, stale))
        prepared = workflow.check(
            ManualTradeDraft(
                "BTCUSDT",
                PositionSide.LONG,
                Decimal("60000"),
                Decimal("59000"),
                None,
                Decimal("2"),
            ),
            evaluated_at=NOW,
        )
        self.assertFalse(prepared.decision.approved)
        self.assertIn("market_data_fresh", prepared.decision.rejection_codes)
        with self.assertRaises(PermissionError):
            workflow.arm()

    def test_open_position_is_rejected(self):
        _, model, workflow = ready_workflow()
        model._state = replace(  # test-only immutable state replacement
            model.state,
            position_side="Long",
            position_quantity=Decimal("0.01"),
        )
        prepared = workflow.check(
            ManualTradeDraft(
                "BTCUSDT",
                PositionSide.LONG,
                Decimal("60000"),
                Decimal("59000"),
                None,
                Decimal("2"),
            ),
            evaluated_at=NOW,
        )
        self.assertFalse(prepared.decision.approved)
        self.assertIn("open_position_limit", prepared.decision.rejection_codes)

    def test_protective_orders_do_not_consume_pending_entry_limit(self):
        _, model, workflow = ready_workflow()
        protective_orders = tuple(
            Order(
                f"protective-{index}",
                OrderRequest(
                    f"protective-client-{index}",
                    "BTCUSDT",
                    OrderSide.SELL,
                    OrderType.MARKET,
                    Decimal("0.01"),
                    reduce_only=True,
                    role=OrderRole.PROTECTIVE,
                ),
                OrderStatus.ACCEPTED,
            )
            for index in range(2)
        )
        model._state = replace(model.state, orders=protective_orders)  # test-only state setup

        prepared = workflow.check(
            ManualTradeDraft(
                "BTCUSDT",
                PositionSide.LONG,
                Decimal("60000"),
                Decimal("59000"),
                Decimal("63000"),
                Decimal("1"),
            ),
            evaluated_at=NOW,
        )

        self.assertTrue(prepared.decision.approved, prepared.decision.rejection_codes)
        pending = next(
            check for check in prepared.decision.checks if check.code == "pending_entry_limit"
        )
        self.assertTrue(pending.passed)
        self.assertIn("current=0", pending.detail)

    def test_invalid_take_profit_is_rejected_before_risk_engine(self):
        _, _, workflow = ready_workflow()
        with self.assertRaises(ValueError):
            workflow.check(
                ManualTradeDraft(
                    "BTCUSDT",
                    PositionSide.LONG,
                    Decimal("60000"),
                    Decimal("59000"),
                    Decimal("58000"),
                    Decimal("2"),
                ),
                evaluated_at=NOW,
            )


if __name__ == "__main__":
    unittest.main()
