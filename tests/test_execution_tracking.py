import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.domain import Execution, Order, OrderRequest
from bybit_workbench.domain.types import OrderSide, OrderStatus, OrderType
from bybit_workbench.execution import ExecutionLedger, OrderTracker, OrderUpdate

NOW = datetime(2026, 8, 13, tzinfo=UTC)


def tracked_order() -> Order:
    return Order(
        order_id="exchange-order-1",
        request=OrderRequest(
            client_order_id="client-order-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("2"),
            price=Decimal("100"),
        ),
        status=OrderStatus.ACCEPTED,
        created_at=NOW,
        updated_at=NOW,
    )


def update(
    event_id: str,
    status: OrderStatus,
    cumulative: str,
    *,
    seconds: int,
) -> OrderUpdate:
    quantity = Decimal(cumulative)
    return OrderUpdate(
        event_id=event_id,
        order_id="exchange-order-1",
        client_order_id="client-order-1",
        status=status,
        cumulative_filled_quantity=quantity,
        average_price=Decimal("100") if quantity > 0 else None,
        occurred_at=NOW + timedelta(seconds=seconds),
    )


class OrderTrackerTests(unittest.TestCase):
    def test_duplicate_event_is_ignored(self) -> None:
        tracker = OrderTracker(tracked_order())
        event = update("event-1", OrderStatus.PARTIALLY_FILLED, "1", seconds=1)
        self.assertTrue(tracker.apply(event))
        self.assertFalse(tracker.apply(event))

    def test_older_event_with_more_fill_is_still_useful(self) -> None:
        tracker = OrderTracker(tracked_order())
        tracker.apply(update("newer", OrderStatus.PARTIALLY_FILLED, "1", seconds=5))
        changed = tracker.apply(update("older-but-full", OrderStatus.FILLED, "2", seconds=3))
        self.assertTrue(changed)
        self.assertEqual(tracker.order.status, OrderStatus.FILLED)
        self.assertEqual(tracker.order.filled_quantity, Decimal("2"))

    def test_filled_order_does_not_regress_on_cancel_race(self) -> None:
        tracker = OrderTracker(tracked_order())
        tracker.apply(update("filled", OrderStatus.FILLED, "2", seconds=1))
        changed = tracker.apply(update("cancel-race", OrderStatus.CANCELLED, "2", seconds=2))
        self.assertFalse(changed)
        self.assertEqual(tracker.order.status, OrderStatus.FILLED)

    def test_cumulative_fill_cannot_exceed_order(self) -> None:
        tracker = OrderTracker(tracked_order())
        with self.assertRaises(ValueError):
            tracker.apply(update("invalid", OrderStatus.FILLED, "3", seconds=1))


class ExecutionLedgerTests(unittest.TestCase):
    def test_execution_is_deduplicated_by_execution_id(self) -> None:
        ledger = ExecutionLedger()
        execution = Execution(
            execution_id="exec-1",
            order_id="exchange-order-1",
            client_order_id="client-order-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Decimal("100"),
            executed_at=NOW,
        )
        self.assertTrue(ledger.record(execution))
        self.assertFalse(ledger.record(execution))
        self.assertEqual(len(ledger.executions), 1)


if __name__ == "__main__":
    unittest.main()
