import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bybit_workbench.domain import Order, OrderRequest, Position
from bybit_workbench.domain.types import OrderSide, OrderStatus, OrderType, PositionSide
from bybit_workbench.persistence import ReconciliationService, TradingJournal

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def active_order() -> Order:
    return Order(
        order_id="order-1",
        request=OrderRequest(
            client_order_id="client-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("1"),
            price=Decimal("100"),
        ),
        status=OrderStatus.ACCEPTED,
        created_at=NOW,
        updated_at=NOW,
    )


class ReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.journal = TradingJournal(Path(self.temp.name) / "reconcile.db")
        self.position = Position("BTCUSDT", PositionSide.LONG, Decimal("1"), Decimal("100"))
        self.order = active_order()
        self.journal.record_position_snapshot(self.position, source="local", observed_at=NOW)
        self.journal.upsert_order(self.order, event_id="event-1")

    def tearDown(self) -> None:
        self.journal.close()
        self.temp.cleanup()

    def test_matching_exchange_snapshot_is_synchronized(self) -> None:
        result = ReconciliationService(self.journal).run(
            "reconcile-1",
            "BTCUSDT",
            self.position,
            [self.order],
            occurred_at=NOW,
        )
        self.assertTrue(result.synchronized)
        self.assertEqual(result.discrepancies, ())

    def test_mismatch_is_explicit_and_persisted(self) -> None:
        remote = Position("BTCUSDT", PositionSide.LONG, Decimal("2"), Decimal("101"))
        result = ReconciliationService(self.journal).run(
            "reconcile-2",
            "BTCUSDT",
            remote,
            [],
            occurred_at=NOW,
        )
        self.assertFalse(result.synchronized)
        codes = {item.code for item in result.discrepancies}
        self.assertIn("position_quantity_mismatch", codes)
        self.assertIn("position_average_price_mismatch", codes)
        self.assertIn("order_missing_on_exchange", codes)
        self.assertEqual(self.journal.table_count("reconciliation_runs"), 1)


if __name__ == "__main__":
    unittest.main()
