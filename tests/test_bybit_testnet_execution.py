import asyncio
import unittest
from decimal import Decimal

from bybit_workbench.domain.models import OrderRequest, Position
from bybit_workbench.domain.types import (
    AppMode,
    OrderRole,
    OrderSide,
    OrderType,
    PositionSide,
)
from bybit_workbench.exchange.bybit.testnet_execution import (
    BybitTestnetExecutionAdapter,
    ExchangeProtectionPlan,
)
from bybit_workbench.exchange.bybit.write_transport import PybitTestnetWriteTransport


class RecordingWriteTransport:
    def __init__(self):
        self.calls = []

    async def post(self, endpoint, params):
        self.calls.append((endpoint, dict(params)))
        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"orderId": "exchange-1", "orderLinkId": params.get("orderLinkId", "")},
        }


class StubReadAdapter:
    pass


class TestnetExecutionAdapterTests(unittest.TestCase):
    def setUp(self):
        self.transport = RecordingWriteTransport()
        self.adapter = BybitTestnetExecutionAdapter(self.transport, StubReadAdapter())

    def test_entry_attaches_exchange_managed_hard_stop(self):
        request = OrderRequest(
            "intent-safe-1",
            "BTCUSDT",
            OrderSide.BUY,
            OrderType.LIMIT,
            Decimal("0.01"),
            Decimal("60000"),
        )
        protection = ExchangeProtectionPlan(Decimal("59000"), Decimal("63000"))

        ack = asyncio.run(self.adapter.place_entry(request, protection))

        endpoint, payload = self.transport.calls[0]
        self.assertEqual(endpoint, "/v5/order/create")
        self.assertEqual(payload["orderLinkId"], request.client_order_id)
        self.assertEqual(payload["stopLoss"], "59000")
        self.assertEqual(payload["slTriggerBy"], "MarkPrice")
        self.assertEqual(payload["tpslMode"], "Full")
        self.assertFalse(payload["reduceOnly"])
        self.assertEqual(ack.order_id, "exchange-1")

    def test_emergency_close_is_market_and_reduce_only(self):
        position = Position(
            "BTCUSDT",
            PositionSide.LONG,
            Decimal("0.02"),
            Decimal("60000"),
        )

        asyncio.run(self.adapter.emergency_close(position, "emergency-safe-1"))

        _, payload = self.transport.calls[0]
        self.assertEqual(payload["side"], "Sell")
        self.assertEqual(payload["orderType"], "Market")
        self.assertTrue(payload["reduceOnly"])
        self.assertEqual(payload["qty"], "0.02")
        self.assertNotIn("stopLoss", payload)

    def test_flat_emergency_close_sends_nothing(self):
        result = asyncio.run(
            self.adapter.emergency_close(
                Position("BTCUSDT", PositionSide.FLAT, Decimal("0"), None),
                "emergency-flat",
            )
        )
        self.assertIsNone(result)
        self.assertEqual(self.transport.calls, [])

    def test_full_protection_supports_native_trailing_distance(self):
        asyncio.run(
            self.adapter.set_full_protection(
                "BTCUSDT",
                ExchangeProtectionPlan(
                    Decimal("59000"),
                    trailing_distance=Decimal("500"),
                    trailing_active_price=Decimal("61000"),
                ),
            )
        )
        endpoint, payload = self.transport.calls[0]
        self.assertEqual(endpoint, "/v5/position/trading-stop")
        self.assertEqual(payload["trailingStop"], "500")
        self.assertEqual(payload["activePrice"], "61000")

    def test_write_transport_is_testnet_only_and_whitelisted(self):
        with self.assertRaises(PermissionError):
            PybitTestnetWriteTransport(object(), AppMode.DEMO)
        with self.assertRaises(PermissionError):
            PybitTestnetWriteTransport(object(), AppMode.LIVE)
        transport = PybitTestnetWriteTransport(object(), AppMode.TESTNET)
        with self.assertRaises(ValueError):
            asyncio.run(transport.post("/v5/account/withdraw", {}))

    def test_entry_rejects_reduce_only_request(self):
        request = OrderRequest(
            "wrong-role",
            "BTCUSDT",
            OrderSide.SELL,
            OrderType.MARKET,
            Decimal("1"),
            reduce_only=True,
            role=OrderRole.EXIT,
        )
        with self.assertRaises(ValueError):
            asyncio.run(
                self.adapter.place_entry(
                    request,
                    ExchangeProtectionPlan(Decimal("59000")),
                )
            )

    def test_market_entry_is_rejected_until_slippage_and_attached_stop_can_coexist(self):
        request = OrderRequest(
            "market-entry",
            "BTCUSDT",
            OrderSide.BUY,
            OrderType.MARKET,
            Decimal("0.01"),
        )
        with self.assertRaises(PermissionError):
            asyncio.run(
                self.adapter.place_entry(
                    request,
                    ExchangeProtectionPlan(Decimal("59000")),
                )
            )


if __name__ == "__main__":
    unittest.main()
