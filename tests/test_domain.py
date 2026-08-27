import unittest
from decimal import Decimal

from bybit_workbench.domain.intents import (
    CancelEntryIntent,
    ExitIntent,
    NoOpIntent,
    UpdateProtectionIntent,
)
from bybit_workbench.domain.models import OrderRequest
from bybit_workbench.domain.types import OrderRole, OrderSide, OrderType


class OrderRequestTests(unittest.TestCase):
    def test_order_link_id_must_fit_bybit_limit(self) -> None:
        with self.assertRaises(ValueError):
            OrderRequest(
                client_order_id="x" * 37,
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=Decimal("0.001"),
            )


class AdditionalIntentTests(unittest.TestCase):
    def test_non_entry_intents_require_auditable_identity(self) -> None:
        for intent_type in (ExitIntent, CancelEntryIntent, NoOpIntent):
            with self.assertRaises(ValueError):
                intent_type("", "BTCUSDT", "reason")

    def test_protection_update_requires_at_least_one_level(self) -> None:
        with self.assertRaises(ValueError):
            UpdateProtectionIntent("protect-1", "BTCUSDT", "trail")
        intent = UpdateProtectionIntent("protect-2", "BTCUSDT", "trail", stop_price=Decimal("99"))
        self.assertEqual(intent.stop_price, Decimal("99"))

    def test_limit_order_requires_price(self) -> None:
        with self.assertRaises(ValueError):
            OrderRequest(
                client_order_id="intent-1",
                symbol="BTCUSDT",
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=Decimal("0.001"),
            )

    def test_entry_cannot_be_reduce_only(self) -> None:
        with self.assertRaises(ValueError):
            OrderRequest(
                client_order_id="invalid-entry",
                symbol="BTCUSDT",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=Decimal("0.001"),
                reduce_only=True,
                role=OrderRole.ENTRY,
            )

    def test_exit_must_be_reduce_only(self) -> None:
        with self.assertRaises(ValueError):
            OrderRequest(
                client_order_id="invalid-exit",
                symbol="BTCUSDT",
                side=OrderSide.SELL,
                order_type=OrderType.MARKET,
                quantity=Decimal("0.001"),
                role=OrderRole.EXIT,
            )


if __name__ == "__main__":
    unittest.main()
