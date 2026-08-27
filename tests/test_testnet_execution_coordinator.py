import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.state_machine import AppStateMachine
from bybit_workbench.domain import ExitIntent
from bybit_workbench.domain.models import Order, OrderRequest, Position
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
from bybit_workbench.exchange.bybit.models import BybitPositionSnapshot
from bybit_workbench.exchange.bybit.streams import BybitStreamSnapshot
from bybit_workbench.exchange.bybit.testnet_execution import (
    BybitOrderAcknowledgement,
    ExchangeProtectionPlan,
)
from bybit_workbench.execution import (
    AmbiguousExecutionCommand,
    ExecutionCommandStatus,
    ProtectionConfirmationError,
)
from bybit_workbench.execution.testnet_coordinator import (
    TestnetExecutionCoordinator as CoordinatorUnderTest,
)
from bybit_workbench.persistence import TradingJournal
from bybit_workbench.stops import RiskExpansionError

NOW = datetime(2026, 8, 13, 14, 0, tzinfo=UTC)


def snapshot(*, stop=Decimal("59000"), take_profit=Decimal("63000")):
    return BybitPositionSnapshot(
        Position("BTCUSDT", PositionSide.LONG, Decimal("0.01"), Decimal("60000")),
        0,
        Decimal("2"),
        Decimal("61000"),
        Decimal("30000"),
        stop,
        take_profit,
        None,
        Decimal("10"),
        1,
        NOW,
    )


def healthy():
    channel = ChannelHealth(True, True, NOW, None)
    return BybitHealthSnapshot(channel, channel, channel)


def entry_request():
    return OrderRequest(
        "intent-entry-safe",
        "BTCUSDT",
        OrderSide.BUY,
        OrderType.LIMIT,
        Decimal("0.01"),
        Decimal("60000"),
    )


class FakeExecutionAdapter:
    def __init__(self):
        self.place_exception = None
        self.lookup_order = None
        self.positions = [snapshot()]
        self.entry_calls = 0
        self.protection_calls = []
        self.emergency_calls = []
        self.cancel_calls = []
        self.protection_exception = None
        self.emergency_exception = None

    async def place_entry(self, request, protection):
        self.entry_calls += 1
        if self.place_exception:
            raise self.place_exception
        return BybitOrderAcknowledgement("exchange-entry-1", request.client_order_id)

    async def order_by_client_id(self, symbol, client_order_id):
        if self.lookup_order == "dynamic-emergency":
            return Order(
                "exchange-emergency-recovered",
                OrderRequest(
                    client_order_id,
                    symbol,
                    OrderSide.SELL,
                    OrderType.MARKET,
                    Decimal("0.01"),
                    reduce_only=True,
                    role=OrderRole.EXIT,
                ),
                OrderStatus.ACCEPTED,
                created_at=NOW,
                updated_at=NOW,
            )
        return self.lookup_order

    async def set_full_protection(self, symbol, protection):
        self.protection_calls.append((symbol, protection))
        if self.protection_exception:
            raise self.protection_exception

    async def cancel_entry(self, **kwargs):
        self.cancel_calls.append(kwargs)
        return BybitOrderAcknowledgement(kwargs["order_id"], "intent-entry-safe")

    async def cancel_order(self, **kwargs):
        self.cancel_calls.append(kwargs)
        return BybitOrderAcknowledgement(kwargs["order_id"], "exit-client")

    async def position(self, symbol):
        if len(self.positions) > 1:
            return self.positions.pop(0)
        return self.positions[0]

    async def emergency_close(self, position, client_order_id):
        self.emergency_calls.append((position, client_order_id))
        if self.emergency_exception:
            raise self.emergency_exception
        return BybitOrderAcknowledgement("exchange-emergency-1", client_order_id)


class TestnetExecutionCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = TradingJournal(Path(self.temp.name) / "execution.db")
        self.machine = AppStateMachine()
        self.machine.transition(AppState.SYNCING, "test sync")
        self.machine.transition(AppState.READY, "test ready")
        self.machine.transition(AppState.ARMED, "test armed")
        self.machine.transition(AppState.RUNNING, "test running")
        self.adapter = FakeExecutionAdapter()
        self.coordinator = CoordinatorUnderTest(
            AppSettings(
                mode=AppMode.TESTNET,
                enable_testnet_execution=True,
            ),
            self.adapter,
            self.journal,
            self.machine,
            protection_confirmation_attempts=2,
            protection_confirmation_delay=0,
        )

    async def asyncTearDown(self):
        self.journal.close()
        self.temp.cleanup()

    async def test_entry_is_requested_once_then_confirmed_by_exchange_order(self):
        request = entry_request()
        protection = ExchangeProtectionPlan(Decimal("59000"), Decimal("63000"))

        acknowledgement = await self.coordinator.submit_entry(request, protection, healthy())
        command = self.journal.execution_command(idempotency_key="entry:intent-entry-safe")

        self.assertEqual(acknowledgement.order_id, "exchange-entry-1")
        self.assertEqual(command.status, ExecutionCommandStatus.ACKNOWLEDGED)
        order = Order(
            "exchange-entry-1",
            request,
            OrderStatus.ACCEPTED,
            created_at=NOW,
            updated_at=NOW,
        )
        confirmed = self.coordinator.confirm_entry(order)
        self.assertEqual(confirmed.status, ExecutionCommandStatus.CONFIRMED)

        duplicate = await self.coordinator.submit_entry(request, protection, healthy())
        self.assertEqual(duplicate.order_id, "exchange-entry-1")
        self.assertEqual(self.adapter.entry_calls, 1)

    async def test_private_ws_confirmation_is_preferred_to_rest_fallback(self):
        order = Order(
            "exchange-entry-1",
            entry_request(),
            OrderStatus.ACCEPTED,
            created_at=NOW,
            updated_at=NOW,
        )
        self.coordinator.private_snapshot_provider = lambda: (
            BybitStreamSnapshot(None, None, None, snapshot(), (order,), ()),
            healthy(),
        )
        observed = await self.coordinator.observe_order("BTCUSDT", "intent-entry-safe")
        self.assertIs(observed, order)
        self.assertEqual(self.coordinator.last_observation_source, "Private WS")
        self.assertIsNone(self.adapter.lookup_order)

    async def test_cancel_non_protective_exit_is_journalled_and_confirmed(self):
        request = OrderRequest(
            "exit-client",
            "BTCUSDT",
            OrderSide.SELL,
            OrderType.LIMIT,
            Decimal("0.01"),
            Decimal("62000"),
            reduce_only=True,
            role=OrderRole.EXIT,
        )
        active = Order(
            "exit-order",
            request,
            OrderStatus.ACCEPTED,
            created_at=NOW,
            updated_at=NOW,
        )
        command = await self.coordinator.cancel_non_protective(active)
        self.assertEqual(command.status, ExecutionCommandStatus.ACKNOWLEDGED)
        cancelled = Order(
            "exit-order",
            request,
            OrderStatus.CANCELLED,
            created_at=NOW,
            updated_at=NOW,
        )
        confirmed = self.coordinator.confirm_cancel(cancelled)
        self.assertEqual(confirmed.status, ExecutionCommandStatus.CONFIRMED)

        protective = Order(
            "protective-order",
            OrderRequest(
                "protective-client",
                "BTCUSDT",
                OrderSide.SELL,
                OrderType.MARKET,
                Decimal("0.01"),
                reduce_only=True,
                role=OrderRole.PROTECTIVE,
            ),
            OrderStatus.ACCEPTED,
            created_at=NOW,
            updated_at=NOW,
        )
        with self.assertRaises(ValueError):
            await self.coordinator.cancel_non_protective(protective)

    async def test_entry_requires_running_engine_and_fresh_channels(self):
        stale = ChannelHealth(True, False, NOW, None)
        with self.assertRaises(PermissionError):
            await self.coordinator.submit_entry(
                entry_request(),
                ExchangeProtectionPlan(Decimal("59000")),
                BybitHealthSnapshot(stale, stale, stale),
            )
        self.assertEqual(self.adapter.entry_calls, 0)

    async def test_ambiguous_entry_is_not_retried_and_can_be_recovered(self):
        request = entry_request()
        protection = ExchangeProtectionPlan(Decimal("59000"))
        self.adapter.place_exception = TimeoutError("response lost")

        with self.assertRaises(AmbiguousExecutionCommand):
            await self.coordinator.submit_entry(request, protection, healthy())
        self.assertEqual(self.adapter.entry_calls, 1)
        command = self.journal.execution_command(idempotency_key="entry:intent-entry-safe")
        self.assertEqual(command.status, ExecutionCommandStatus.AMBIGUOUS)

        self.adapter.lookup_order = Order(
            "exchange-recovered",
            request,
            OrderStatus.ACCEPTED,
            created_at=NOW,
            updated_at=NOW,
        )
        recovered = await self.coordinator.submit_entry(request, protection, healthy())
        self.assertEqual(recovered.order_id, "exchange-recovered")
        self.assertEqual(self.adapter.entry_calls, 1)
        command = self.journal.execution_command(idempotency_key="entry:intent-entry-safe")
        self.assertEqual(command.status, ExecutionCommandStatus.CONFIRMED)

    async def test_protection_is_confirmed_from_position_snapshot(self):
        outcome = await self.coordinator.ensure_protection(
            snapshot(stop=None, take_profit=None),
            ExchangeProtectionPlan(Decimal("59000"), Decimal("63000")),
        )
        self.assertEqual(
            outcome.protection_command.status,
            ExecutionCommandStatus.CONFIRMED,
        )
        self.assertEqual(len(self.adapter.protection_calls), 1)
        self.assertEqual(self.adapter.emergency_calls, [])

    async def test_unconfirmed_protection_requests_reduce_only_emergency_close(self):
        self.adapter.positions = [snapshot(stop=None, take_profit=None)]
        with self.assertRaises(ProtectionConfirmationError):
            await self.coordinator.ensure_protection(
                snapshot(stop=None, take_profit=None),
                ExchangeProtectionPlan(Decimal("59000"), Decimal("63000")),
            )
        self.assertEqual(self.machine.state, AppState.EMERGENCY_STOP)
        self.assertEqual(len(self.adapter.emergency_calls), 1)
        position, client_id = self.adapter.emergency_calls[0]
        self.assertEqual(position.quantity, Decimal("0.01"))
        self.assertLessEqual(len(client_id), 36)
        emergency = self.journal.execution_command(idempotency_key="emergency:-:BTCUSDT:Long:0.01")
        self.assertEqual(emergency.status, ExecutionCommandStatus.ACKNOWLEDGED)

    async def test_trailing_stop_cannot_move_backwards(self):
        with self.assertRaises(RiskExpansionError):
            await self.coordinator.move_stop(
                snapshot(stop=Decimal("59000")),
                ExchangeProtectionPlan(Decimal("58000")),
            )
        self.assertEqual(self.adapter.protection_calls, [])

    async def test_lost_protection_response_is_recovered_from_position(self):
        self.adapter.protection_exception = TimeoutError("response lost")
        outcome = await self.coordinator.ensure_protection(
            snapshot(stop=None, take_profit=None),
            ExchangeProtectionPlan(Decimal("59000"), Decimal("63000")),
        )
        self.assertEqual(outcome.protection_command.status, ExecutionCommandStatus.CONFIRMED)
        self.assertEqual(self.adapter.emergency_calls, [])

    async def test_lost_emergency_response_is_recovered_by_client_id(self):
        self.adapter.emergency_exception = TimeoutError("response lost")
        self.adapter.lookup_order = "dynamic-emergency"
        command = await self.coordinator.emergency_close(snapshot().position)
        self.assertEqual(command.status, ExecutionCommandStatus.ACKNOWLEDGED)
        self.assertEqual(command.exchange_order_id, "exchange-emergency-recovered")
        self.assertEqual(len(self.adapter.emergency_calls), 1)

    async def test_strategy_exit_is_reduce_only_idempotent_without_emergency_state(self):
        self.journal.start_strategy_run(
            "automatic-run",
            strategy_id="user_algorithm_1",
            strategy_version="0.1.0",
            mode="Testnet",
            symbol="BTCUSDT",
            parameters={},
        )
        exit_intent = ExitIntent(
            "opposite-breakout-exit",
            "BTCUSDT",
            "opposite causal breakout",
        )
        self.journal.record_strategy_decision(
            "automatic-exit-decision",
            "automatic-run",
            inputs={},
            decision={"action": "exit"},
        )
        self.journal.record_trade_intent(
            exit_intent,
            "automatic-run",
            decision_id="automatic-exit-decision",
        )
        first = await self.coordinator.close_position(
            snapshot().position,
            intent_id=exit_intent.intent_id,
        )
        second = await self.coordinator.close_position(
            snapshot().position,
            intent_id=exit_intent.intent_id,
        )
        self.assertEqual(first.command_id, second.command_id)
        self.assertEqual(first.status, ExecutionCommandStatus.ACKNOWLEDGED)
        self.assertEqual(len(self.adapter.emergency_calls), 1)
        self.assertTrue(self.adapter.emergency_calls[0][1].startswith("bw-exit-"))
        self.assertEqual(self.machine.state, AppState.RUNNING)

    async def test_active_entry_cancel_is_durable_and_waits_for_ws_confirmation(self):
        request = entry_request()
        order = Order(
            "exchange-entry-1",
            request,
            OrderStatus.PARTIALLY_FILLED,
            filled_quantity=Decimal("0.005"),
            average_price=Decimal("60000"),
            created_at=NOW,
            updated_at=NOW,
        )
        command = await self.coordinator.cancel_entry(order)
        self.assertEqual(command.status, ExecutionCommandStatus.ACKNOWLEDGED)
        self.assertEqual(self.adapter.cancel_calls[0]["order_id"], order.order_id)
        duplicate = await self.coordinator.cancel_entry(order)
        self.assertEqual(duplicate.command_id, command.command_id)
        self.assertEqual(len(self.adapter.cancel_calls), 1)
        order.status = OrderStatus.CANCELLED
        confirmed = self.coordinator.confirm_cancel(order)
        self.assertEqual(confirmed.status, ExecutionCommandStatus.CONFIRMED)


if __name__ == "__main__":
    unittest.main()
