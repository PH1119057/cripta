import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from bybit_workbench.app.config import AppSettings
from bybit_workbench.app.state_machine import AppStateMachine
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
    BybitWriteRejected,
    ExchangeProtectionPlan,
)
from bybit_workbench.execution import (
    AmbiguousExecutionCommand,
    ExecutionCommandStatus,
)
from bybit_workbench.execution.mainnet_coordinator import MainnetExecutionCoordinator
from bybit_workbench.execution.mainnet_safety import MainnetMutation, MutationKind
from bybit_workbench.persistence import TradingJournal

NOW = datetime(2026, 8, 14, 10, 0, tzinfo=UTC)


def entry_request() -> OrderRequest:
    return OrderRequest(
        "entry-uni-1",
        "UNIUSDT",
        OrderSide.BUY,
        OrderType.LIMIT,
        Decimal("1"),
        Decimal("3"),
    )


def market_entry_request() -> OrderRequest:
    return OrderRequest(
        "entry-uni-market-1",
        "UNIUSDT",
        OrderSide.BUY,
        OrderType.MARKET,
        Decimal("1"),
    )


def position(
    *,
    side: PositionSide = PositionSide.LONG,
    stop: Decimal | None = Decimal("2.8"),
) -> BybitPositionSnapshot:
    quantity = Decimal("0") if side is PositionSide.FLAT else Decimal("1")
    average = None if side is PositionSide.FLAT else Decimal("3")
    return BybitPositionSnapshot(
        Position("UNIUSDT", side, quantity, average),
        0,
        Decimal("1"),
        Decimal("3.1"),
        None,
        stop,
        Decimal("3.5") if stop is not None else None,
        None,
        Decimal("0"),
        1,
        NOW,
    )


def order(
    status: OrderStatus = OrderStatus.ACCEPTED,
    *,
    filled: Decimal = Decimal("0"),
) -> Order:
    return Order(
        "exchange-entry-1",
        entry_request(),
        status,
        filled_quantity=filled,
        created_at=NOW,
        updated_at=NOW,
    )


def healthy() -> BybitHealthSnapshot:
    channel = ChannelHealth(True, True, NOW, None)
    return BybitHealthSnapshot(channel, channel, channel)


class InvalidRequestError(RuntimeError):
    __module__ = "pybit.exceptions"

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[MainnetMutation] = []
        self.exception: Exception | None = None
        self.response_override: dict[str, Any] | None = None

    async def submit(self, mutation: MainnetMutation) -> dict[str, Any]:
        self.calls.append(mutation)
        if self.exception is not None:
            raise self.exception
        if self.response_override is not None:
            return self.response_override
        if mutation.endpoint == "/v5/position/trading-stop":
            return {"retCode": 0, "result": {}}
        if mutation.endpoint == "/v5/order/cancel":
            return {
                "retCode": 0,
                "result": {
                    "orderId": str(mutation.params.get("orderId")),
                    "orderLinkId": "entry-uni-1",
                },
            }
        link = str(mutation.params["orderLinkId"])
        return {
            "retCode": 0,
            "result": {
                "orderId": "exchange-close-1"
                if mutation.kind is MutationKind.REDUCE_ONLY
                else "exchange-entry-1",
                "orderLinkId": link,
            },
        }


class FakeReader:
    def __init__(self) -> None:
        self.lookup_order: Order | None = None
        self.lookup_errors: list[Exception] = []
        self.lookup_calls = 0
        self.position = position()
        self.orders: list[Order] = []

    async def position_snapshot(self, symbol: str) -> BybitPositionSnapshot:
        if symbol != "UNIUSDT":
            raise AssertionError(symbol)
        return self.position

    async def open_orders(self, symbol: str) -> list[Order]:
        if symbol != "UNIUSDT":
            raise AssertionError(symbol)
        return list(self.orders)

    async def order_by_client_id(
        self,
        symbol: str,
        client_order_id: str,
    ) -> Order | None:
        if symbol != "UNIUSDT" or not client_order_id:
            raise AssertionError((symbol, client_order_id))
        self.lookup_calls += 1
        if self.lookup_errors:
            raise self.lookup_errors.pop(0)
        return self.lookup_order


class MainnetExecutionCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal = TradingJournal(Path(self.temp.name) / "mainnet.db")
        self.machine = AppStateMachine()
        self.machine.transition(AppState.SYNCING, "fixture sync")
        self.machine.transition(AppState.READY, "fixture ready")
        self.machine.transition(AppState.ARMED, "fixture armed")
        self.machine.transition(AppState.RUNNING, "fixture running")
        self.gateway = FakeGateway()
        self.reader = FakeReader()
        self.coordinator = MainnetExecutionCoordinator(
            AppSettings(mode=AppMode.LIVE, allow_live_trading=True),
            self.gateway,  # type: ignore[arg-type]
            self.reader,
            self.journal,
            self.machine,
            confirmation_attempts=2,
            confirmation_delay=0,
        )

    async def asyncTearDown(self) -> None:
        self.journal.close()
        self.temp.cleanup()

    async def test_entry_uses_gateway_and_duplicate_is_not_sent_twice(self) -> None:
        request = entry_request()
        protection = ExchangeProtectionPlan(Decimal("2.8"), Decimal("3.5"))
        acknowledgement = await self.coordinator.submit_entry(request, protection)
        self.assertEqual(acknowledgement.order_id, "exchange-entry-1")
        mutation = self.gateway.calls[0]
        self.assertEqual(mutation.kind, MutationKind.ENTRY)
        self.assertEqual(mutation.params["tpslMode"], "Full")
        self.assertEqual(mutation.params["stopLoss"], "2.8")
        self.assertEqual(mutation.params["slTriggerBy"], "MarkPrice")
        self.assertEqual(mutation.params["slOrderType"], "Market")
        self.assertEqual(mutation.params["takeProfit"], "3.5")
        self.assertEqual(mutation.params["tpTriggerBy"], "MarkPrice")
        self.assertEqual(mutation.params["tpOrderType"], "Market")
        self.assertNotIn("reduceOnly", mutation.params)
        self.assertNotIn("closeOnTrigger", mutation.params)
        command = self.journal.execution_command(
            idempotency_key="mainnet:entry:entry-uni-1"
        )
        self.assertEqual(command.status, ExecutionCommandStatus.ACKNOWLEDGED)
        self.coordinator.confirm_entry(order())
        duplicate = await self.coordinator.submit_entry(request, protection)
        self.assertEqual(duplicate.order_id, "exchange-entry-1")
        self.assertEqual(len(self.gateway.calls), 1)

    async def test_market_entry_uses_ioc_without_price_or_custom_slippage(self) -> None:
        request = market_entry_request()
        protection = ExchangeProtectionPlan(Decimal("2.8"), Decimal("3.5"))
        acknowledgement = await self.coordinator.submit_entry(request, protection)
        self.assertEqual(acknowledgement.order_id, "exchange-entry-1")
        mutation = self.gateway.calls[0]
        self.assertEqual(mutation.params["orderType"], "Market")
        self.assertNotIn("timeInForce", mutation.params)
        self.assertNotIn("price", mutation.params)
        self.assertNotIn("slippageToleranceType", mutation.params)
        self.assertNotIn("slippageTolerance", mutation.params)
        self.assertEqual(mutation.params["tpslMode"], "Full")
        self.assertEqual(mutation.params["stopLoss"], "2.8")
        self.assertEqual(mutation.params["takeProfit"], "3.5")
        self.assertEqual(mutation.params["slTriggerBy"], "MarkPrice")
        self.assertEqual(mutation.params["tpTriggerBy"], "MarkPrice")

    async def test_ambiguous_entry_is_recovered_by_link_without_retry(self) -> None:
        self.gateway.exception = TimeoutError("response lost")
        with self.assertRaisesRegex(
            AmbiguousExecutionCommand,
            "write_error=TimeoutError: response lost",
        ):
            await self.coordinator.submit_entry(
                entry_request(),
                ExchangeProtectionPlan(Decimal("2.8")),
            )
        self.assertEqual(len(self.gateway.calls), 1)
        self.assertEqual(self.reader.lookup_calls, 2)
        self.reader.lookup_order = order()
        recovered = await self.coordinator.submit_entry(
            entry_request(),
            ExchangeProtectionPlan(Decimal("2.8")),
        )
        self.assertEqual(recovered.order_id, "exchange-entry-1")
        self.assertEqual(len(self.gateway.calls), 1)

    async def test_pybit_invalid_request_is_definite_rejection_not_ambiguous(self) -> None:
        self.gateway.exception = InvalidRequestError(
            "Order price exceeds allowable range",
            110003,
        )

        with self.assertRaisesRegex(
            BybitWriteRejected,
            "retCode=110003.*Order price exceeds allowable range",
        ):
            await self.coordinator.submit_entry(
                entry_request(),
                ExchangeProtectionPlan(Decimal("2.8")),
            )

        self.assertEqual(len(self.gateway.calls), 1)
        self.assertEqual(self.reader.lookup_calls, 0)
        command = self.journal.execution_command(
            idempotency_key="mainnet:entry:entry-uni-1"
        )
        self.assertEqual(command.status, ExecutionCommandStatus.FAILED)
        self.assertIn("retCode=110003", command.error or "")

    async def test_ambiguous_entry_retries_lookup_without_resending_post(self) -> None:
        self.gateway.exception = TimeoutError("response lost")
        self.reader.lookup_errors = [TimeoutError("first lookup timed out")]
        self.reader.lookup_order = order()

        acknowledgement = await self.coordinator.submit_entry(
            entry_request(),
            ExchangeProtectionPlan(Decimal("2.8")),
        )

        self.assertEqual(acknowledgement.order_id, "exchange-entry-1")
        self.assertEqual(len(self.gateway.calls), 1)
        self.assertEqual(self.reader.lookup_calls, 2)
        command = self.journal.execution_command(
            idempotency_key="mainnet:entry:entry-uni-1"
        )
        self.assertEqual(command.status, ExecutionCommandStatus.CONFIRMED)

    async def test_malformed_success_response_is_ambiguous_and_never_retried(self) -> None:
        self.gateway.response_override = {"retCode": 0, "result": {}}
        with self.assertRaises(AmbiguousExecutionCommand):
            await self.coordinator.submit_entry(
                entry_request(),
                ExchangeProtectionPlan(Decimal("2.8")),
            )
        command = self.journal.execution_command(
            idempotency_key="mainnet:entry:entry-uni-1"
        )
        self.assertEqual(command.status, ExecutionCommandStatus.AMBIGUOUS)
        self.assertEqual(len(self.gateway.calls), 1)

        with self.assertRaises(AmbiguousExecutionCommand):
            await self.coordinator.submit_entry(
                entry_request(),
                ExchangeProtectionPlan(Decimal("2.8")),
            )
        self.assertEqual(len(self.gateway.calls), 1)

    async def test_private_ws_confirmation_precedes_rest_fallback(self) -> None:
        expected = order()
        self.coordinator.private_snapshot_provider = lambda: (
            BybitStreamSnapshot(None, None, None, position(), (expected,), ()),
            healthy(),
        )
        observed = await self.coordinator.observe_order("UNIUSDT", "entry-uni-1")
        self.assertIs(observed, expected)
        self.assertEqual(self.coordinator.last_observation_source, "Private WS")

    async def test_cancel_never_targets_protective_order(self) -> None:
        active = order()
        command = await self.coordinator.cancel_order(active, entries_only=True)
        self.assertEqual(command.status, ExecutionCommandStatus.ACKNOWLEDGED)
        cancelled = order(OrderStatus.CANCELLED)
        confirmed = self.coordinator.confirm_cancel(cancelled)
        self.assertEqual(confirmed.status, ExecutionCommandStatus.CONFIRMED)
        protective_request = OrderRequest(
            "protective-1",
            "UNIUSDT",
            OrderSide.SELL,
            OrderType.MARKET,
            Decimal("1"),
            reduce_only=True,
            role=OrderRole.PROTECTIVE,
        )
        protective = Order(
            "protective-order",
            protective_request,
            OrderStatus.ACCEPTED,
            created_at=NOW,
            updated_at=NOW,
        )
        with self.assertRaisesRegex(ValueError, "protective"):
            await self.coordinator.cancel_order(protective, entries_only=False)

    async def test_missing_attached_stop_is_reapplied_and_confirmed(self) -> None:
        unprotected = position(stop=None)
        self.reader.position = position()
        command = await self.coordinator.set_protection(
            unprotected,
            ExchangeProtectionPlan(Decimal("2.8"), Decimal("3.5")),
        )
        self.assertEqual(command.status, ExecutionCommandStatus.CONFIRMED)
        self.assertEqual(self.gateway.calls[-1].kind, MutationKind.PROTECTION)

    async def test_lost_protection_response_is_confirmed_from_position(self) -> None:
        self.gateway.exception = TimeoutError("response lost")
        command = await self.coordinator.set_protection(
            position(stop=None),
            ExchangeProtectionPlan(Decimal("2.8"), Decimal("3.5")),
        )
        self.assertEqual(command.status, ExecutionCommandStatus.CONFIRMED)
        self.assertEqual(len(self.gateway.calls), 1)

    async def test_close_is_market_reduce_only_and_flat_is_confirmed(self) -> None:
        acknowledgement = await self.coordinator.close_position(
            position().position,
        )
        self.assertIsNotNone(acknowledgement)
        mutation = self.gateway.calls[-1]
        self.assertEqual(mutation.kind, MutationKind.REDUCE_ONLY)
        self.assertIs(mutation.params["reduceOnly"], True)
        self.assertEqual(mutation.params["orderType"], "Market")
        command = self.journal.execution_command(
            idempotency_key="mainnet:close:-:UNIUSDT:Long:1"
        )
        confirmed = self.coordinator.confirm_flat(
            command.command_id,
            position(side=PositionSide.FLAT).position,
        )
        self.assertEqual(confirmed.status, ExecutionCommandStatus.CONFIRMED)

    async def test_lost_close_response_is_confirmed_from_flat_without_retry(self) -> None:
        self.gateway.exception = TimeoutError("response lost")
        self.reader.position = position(side=PositionSide.FLAT)
        acknowledgement = await self.coordinator.close_position(position().position)
        self.assertIsNotNone(acknowledgement)
        self.assertEqual(len(self.gateway.calls), 1)
        command = self.journal.execution_command(
            idempotency_key="mainnet:close:-:UNIUSDT:Long:1"
        )
        self.assertEqual(command.status, ExecutionCommandStatus.CONFIRMED)


if __name__ == "__main__":
    unittest.main()
