import unittest
from decimal import Decimal

from bybit_workbench.domain.models import OrderRequest
from bybit_workbench.domain.types import (
    OrderRole,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from bybit_workbench.exchange.fake import (
    DuplicateClientOrderId,
    FakeExchange,
    FakeFault,
    InjectedFakeExchangeFault,
)


class FakeExchangeTests(unittest.IsolatedAsyncioTestCase):
    async def test_fault_injection_covers_operational_failure_classes(self) -> None:
        for fault in (
            FakeFault.AUTH,
            FakeFault.RATE_LIMIT,
            FakeFault.CLOCK_SKEW,
            FakeFault.SYMBOL_HALTED,
            FakeFault.INSUFFICIENT_MARGIN,
        ):
            self.exchange.inject_fault("instrument_rules", fault)
            with self.assertRaises(InjectedFakeExchangeFault) as caught:
                await self.exchange.instrument_rules("BTCUSDT")
            self.assertEqual(caught.exception.fault, fault)

    async def test_disconnect_after_accept_requires_reconciliation_not_retry(self) -> None:
        request = OrderRequest(
            "lost-response",
            "BTCUSDT",
            OrderSide.BUY,
            OrderType.LIMIT,
            Decimal("0.01"),
            Decimal("49000"),
        )
        self.exchange.inject_fault("after_place_order", FakeFault.DISCONNECT_AFTER_ACCEPT)
        with self.assertRaises(ConnectionError):
            await self.exchange.place_order(request)
        await self.exchange.connect()
        orders = await self.exchange.orders()
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].request.client_order_id, "lost-response")
        with self.assertRaises(DuplicateClientOrderId):
            await self.exchange.place_order(request)

    async def asyncSetUp(self) -> None:
        self.exchange = FakeExchange(initial_price=Decimal("50000"))
        await self.exchange.connect()

    async def asyncTearDown(self) -> None:
        await self.exchange.disconnect()

    async def test_market_order_fills_and_creates_position(self) -> None:
        request = OrderRequest(
            client_order_id="intent-0001",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.010"),
        )
        order = await self.exchange.place_order(request)
        position = (await self.exchange.positions())[0]
        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(position.side, PositionSide.LONG)
        self.assertEqual(position.quantity, Decimal("0.010"))

    async def test_duplicate_client_id_is_rejected(self) -> None:
        request = OrderRequest(
            client_order_id="intent-0002",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("0.010"),
            price=Decimal("49000"),
        )
        await self.exchange.place_order(request)
        with self.assertRaises(DuplicateClientOrderId):
            await self.exchange.place_order(request)

    async def test_reduce_only_closes_without_reversal(self) -> None:
        await self.exchange.place_order(
            OrderRequest(
                "intent-open",
                "BTCUSDT",
                OrderSide.BUY,
                OrderType.MARKET,
                Decimal("0.010"),
            )
        )
        await self.exchange.place_order(
            OrderRequest(
                "intent-close",
                "BTCUSDT",
                OrderSide.SELL,
                OrderType.MARKET,
                Decimal("0.010"),
                reduce_only=True,
                role=OrderRole.EXIT,
            )
        )
        position = (await self.exchange.positions())[0]
        self.assertEqual(position.side, PositionSide.FLAT)

    async def test_reduce_only_overclose_is_rejected_atomically(self) -> None:
        await self.exchange.place_order(
            OrderRequest(
                "intent-open-small",
                "BTCUSDT",
                OrderSide.BUY,
                OrderType.MARKET,
                Decimal("0.010"),
            )
        )
        executions_before = len(self.exchange.executions)
        rejected = await self.exchange.place_order(
            OrderRequest(
                "intent-overclose",
                "BTCUSDT",
                OrderSide.SELL,
                OrderType.MARKET,
                Decimal("0.020"),
                reduce_only=True,
                role=OrderRole.EXIT,
            )
        )
        position = (await self.exchange.positions())[0]
        self.assertEqual(rejected.status, OrderStatus.REJECTED)
        self.assertEqual(rejected.filled_quantity, Decimal("0"))
        self.assertEqual(len(self.exchange.executions), executions_before)
        self.assertEqual(position.side, PositionSide.LONG)
        self.assertEqual(position.quantity, Decimal("0.010"))


if __name__ == "__main__":
    unittest.main()
