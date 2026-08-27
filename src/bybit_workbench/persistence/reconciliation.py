from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal

from bybit_workbench.domain.models import Order, Position
from bybit_workbench.domain.types import OrderStatus

from .trading_journal import LocalProjection, TradingJournal


@dataclass(frozen=True, slots=True)
class ReconciliationDiscrepancy:
    code: str
    entity_id: str
    local_value: str | None
    exchange_value: str | None


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    reconciliation_id: str
    synchronized: bool
    discrepancies: tuple[ReconciliationDiscrepancy, ...]


def compare_projection(
    local: LocalProjection,
    exchange_position: Position,
    exchange_orders: Sequence[Order],
) -> tuple[ReconciliationDiscrepancy, ...]:
    discrepancies: list[ReconciliationDiscrepancy] = []
    if local.symbol != exchange_position.symbol:
        discrepancies.append(
            ReconciliationDiscrepancy(
                "position_symbol_mismatch",
                local.symbol,
                local.symbol,
                exchange_position.symbol,
            )
        )
    if local.position.side != exchange_position.side:
        discrepancies.append(
            ReconciliationDiscrepancy(
                "position_side_mismatch",
                local.symbol,
                local.position.side.value,
                exchange_position.side.value,
            )
        )
    if local.position.quantity != exchange_position.quantity:
        discrepancies.append(
            ReconciliationDiscrepancy(
                "position_quantity_mismatch",
                local.symbol,
                str(local.position.quantity),
                str(exchange_position.quantity),
            )
        )
    if not _decimal_equal(local.position.average_price, exchange_position.average_price):
        discrepancies.append(
            ReconciliationDiscrepancy(
                "position_average_price_mismatch",
                local.symbol,
                _decimal_text(local.position.average_price),
                _decimal_text(exchange_position.average_price),
            )
        )

    local_by_client = {order.request.client_order_id: order for order in local.active_orders}
    exchange_active = {
        order.request.client_order_id: order
        for order in exchange_orders
        if order.status in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}
    }
    for client_id in sorted(local_by_client.keys() - exchange_active.keys()):
        discrepancies.append(
            ReconciliationDiscrepancy(
                "order_missing_on_exchange",
                client_id,
                local_by_client[client_id].status.value,
                None,
            )
        )
    for client_id in sorted(exchange_active.keys() - local_by_client.keys()):
        discrepancies.append(
            ReconciliationDiscrepancy(
                "order_missing_locally",
                client_id,
                None,
                exchange_active[client_id].status.value,
            )
        )
    for client_id in sorted(local_by_client.keys() & exchange_active.keys()):
        local_order = local_by_client[client_id]
        remote_order = exchange_active[client_id]
        if local_order.status != remote_order.status:
            discrepancies.append(
                ReconciliationDiscrepancy(
                    "order_status_mismatch",
                    client_id,
                    local_order.status.value,
                    remote_order.status.value,
                )
            )
        if local_order.filled_quantity != remote_order.filled_quantity:
            discrepancies.append(
                ReconciliationDiscrepancy(
                    "order_fill_mismatch",
                    client_id,
                    str(local_order.filled_quantity),
                    str(remote_order.filled_quantity),
                )
            )
    return tuple(discrepancies)


class ReconciliationService:
    def __init__(self, journal: TradingJournal) -> None:
        self.journal = journal

    def run(
        self,
        reconciliation_id: str,
        symbol: str,
        exchange_position: Position,
        exchange_orders: Sequence[Order],
        *,
        run_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> ReconciliationResult:
        timestamp = occurred_at or datetime.now(UTC)
        self.journal.start_reconciliation(
            reconciliation_id,
            symbol,
            started_at=timestamp,
        )
        local = self.journal.load_projection(symbol, run_id=run_id)
        discrepancies = compare_projection(local, exchange_position, exchange_orders)
        result = ReconciliationResult(
            reconciliation_id,
            not discrepancies,
            discrepancies,
        )
        self.journal.finish_reconciliation(
            reconciliation_id,
            synchronized=result.synchronized,
            discrepancies=[asdict(item) for item in discrepancies],
            finished_at=timestamp,
        )
        return result


def _decimal_equal(left: Decimal | None, right: Decimal | None) -> bool:
    if left is None or right is None:
        return left is right
    return left == right


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
