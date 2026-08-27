from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from bybit_workbench.domain.models import Order
from bybit_workbench.domain.types import OrderStatus


@dataclass(frozen=True, slots=True)
class OrderUpdate:
    event_id: str
    order_id: str
    client_order_id: str
    status: OrderStatus
    cumulative_filled_quantity: Decimal
    average_price: Decimal | None
    occurred_at: datetime

    def __post_init__(self) -> None:
        if not self.event_id or not self.order_id or not self.client_order_id:
            raise ValueError("event and order identifiers are required")
        if self.cumulative_filled_quantity < 0:
            raise ValueError("cumulative fill cannot be negative")
        if self.cumulative_filled_quantity > 0 and self.average_price is None:
            raise ValueError("average price is required for a filled quantity")
        if self.occurred_at.tzinfo is None:
            raise ValueError("order update timestamp must be timezone-aware")


class OrderTracker:
    """Applies duplicate and out-of-order exchange updates without state regression."""

    def __init__(self, order: Order) -> None:
        self.order = order
        self._processed_event_ids: set[str] = set()

    def apply(self, update: OrderUpdate) -> bool:
        if update.event_id in self._processed_event_ids:
            return False
        if update.order_id != self.order.order_id:
            raise ValueError("update order_id does not match tracked order")
        if update.client_order_id != self.order.request.client_order_id:
            raise ValueError("update client_order_id does not match tracked order")
        if update.cumulative_filled_quantity > self.order.request.quantity:
            raise ValueError("cumulative fill exceeds requested quantity")
        self._processed_event_ids.add(update.event_id)

        current_filled = self.order.filled_quantity
        if update.cumulative_filled_quantity < current_filled:
            return False
        if self.order.status is OrderStatus.FILLED:
            return False
        if (
            self.order.status in {OrderStatus.CANCELLED, OrderStatus.REJECTED}
            and update.cumulative_filled_quantity <= current_filled
        ):
            return False

        status = update.status
        if update.cumulative_filled_quantity == self.order.request.quantity:
            status = OrderStatus.FILLED
        elif self.order.status in {OrderStatus.CANCELLED, OrderStatus.REJECTED}:
            status = self.order.status
        elif update.cumulative_filled_quantity > 0 and status is OrderStatus.ACCEPTED:
            status = OrderStatus.PARTIALLY_FILLED

        same_fill = update.cumulative_filled_quantity == current_filled
        if same_fill and update.occurred_at < self.order.updated_at:
            return False
        if same_fill and _status_rank(status) < _status_rank(self.order.status):
            return False

        changed = (
            status is not self.order.status
            or update.cumulative_filled_quantity != current_filled
            or update.average_price != self.order.average_price
        )
        if not changed:
            return False
        self.order.status = status
        self.order.filled_quantity = update.cumulative_filled_quantity
        self.order.average_price = update.average_price
        self.order.updated_at = max(self.order.updated_at, update.occurred_at)
        return True


def _status_rank(status: OrderStatus) -> int:
    return {
        OrderStatus.CREATED: 0,
        OrderStatus.ACCEPTED: 1,
        OrderStatus.PARTIALLY_FILLED: 2,
        OrderStatus.CANCELLED: 3,
        OrderStatus.REJECTED: 3,
        OrderStatus.FILLED: 4,
    }[status]
