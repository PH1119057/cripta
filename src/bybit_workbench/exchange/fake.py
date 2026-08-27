from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from bybit_workbench.domain.models import (
    Candle,
    Execution,
    InstrumentRules,
    Order,
    OrderRequest,
    Position,
)
from bybit_workbench.domain.types import (
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)


class DuplicateClientOrderId(RuntimeError):
    pass


class FakeFault(StrEnum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    CLOCK_SKEW = "clock_skew"
    SYMBOL_HALTED = "symbol_halted"
    INSUFFICIENT_MARGIN = "insufficient_margin"
    DISCONNECT_AFTER_ACCEPT = "disconnect_after_accept"


class InjectedFakeExchangeFault(RuntimeError):
    def __init__(self, fault: FakeFault) -> None:
        self.fault = fault
        super().__init__(f"injected fake exchange fault: {fault.value}")


class FakeExchange:
    """Deterministic offline adapter used for infrastructure testing."""

    def __init__(self, *, symbol: str = "BTCUSDT", initial_price: Decimal = Decimal("50000")):
        self.symbol = symbol
        self.last_price = initial_price
        self._connected = False
        self._sequence = 0
        self._orders: dict[str, Order] = {}
        self._executions: list[Execution] = []
        self._position = Position(symbol, PositionSide.FLAT, Decimal("0"), None)
        self._rules = InstrumentRules(
            symbol=symbol,
            tick_size=Decimal("0.10"),
            qty_step=Decimal("0.001"),
            min_order_qty=Decimal("0.001"),
            min_notional=Decimal("5"),
            max_order_qty=Decimal("100"),
        )
        self._faults: dict[str, list[FakeFault]] = {}

    def inject_fault(self, operation: str, fault: FakeFault) -> None:
        if not operation.strip():
            raise ValueError("fault operation is required")
        self._faults.setdefault(operation, []).append(fault)

    def _raise_injected(self, operation: str) -> None:
        queued = self._faults.get(operation)
        if not queued:
            return
        fault = queued.pop(0)
        if not queued:
            self._faults.pop(operation, None)
        raise InjectedFakeExchangeFault(fault)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def executions(self) -> tuple[Execution, ...]:
        return tuple(self._executions)

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def _require_connection(self) -> None:
        if not self._connected:
            raise ConnectionError("fake exchange is disconnected")

    async def instrument_rules(self, symbol: str) -> InstrumentRules:
        self._require_connection()
        self._raise_injected("instrument_rules")
        if symbol != self.symbol:
            raise LookupError(f"unknown symbol: {symbol}")
        return self._rules

    async def place_order(self, request: OrderRequest) -> Order:
        self._require_connection()
        self._raise_injected("place_order")
        if request.symbol != self.symbol:
            raise LookupError(f"unknown symbol: {request.symbol}")
        if request.client_order_id in self._orders:
            raise DuplicateClientOrderId(request.client_order_id)
        self._sequence += 1
        order = Order(
            order_id=f"fake-order-{self._sequence:08d}",
            request=request,
            status=OrderStatus.ACCEPTED,
        )
        self._orders[request.client_order_id] = order
        disconnect_after_accept = False
        queued = self._faults.get("after_place_order")
        if queued and queued[0] is FakeFault.DISCONNECT_AFTER_ACCEPT:
            queued.pop(0)
            if not queued:
                self._faults.pop("after_place_order", None)
            disconnect_after_accept = True
        if request.order_type is OrderType.MARKET:
            try:
                self._fill(order, request.quantity, self.last_price)
            except RuntimeError:
                order.status = OrderStatus.REJECTED
                order.updated_at = datetime.now(UTC)
        if disconnect_after_accept:
            self._connected = False
            raise ConnectionError(
                "injected disconnect after acceptance; reconcile by client_order_id"
            )
        return order

    async def cancel_order(self, client_order_id: str) -> Order:
        self._require_connection()
        order = self._orders[client_order_id]
        if order.status in {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}:
            return order
        order.status = OrderStatus.CANCELLED
        order.updated_at = datetime.now(UTC)
        return order

    async def positions(self) -> list[Position]:
        self._require_connection()
        self._raise_injected("positions")
        return [self._position]

    async def orders(self) -> list[Order]:
        self._require_connection()
        self._raise_injected("orders")
        return list(self._orders.values())

    def _fill(self, order: Order, quantity: Decimal, price: Decimal) -> None:
        if quantity <= 0 or quantity > order.remaining_quantity:
            raise ValueError("invalid fill quantity")
        projected_position = self._project_position(order.request, quantity, price)
        previous_filled = order.filled_quantity
        total_value = (order.average_price or Decimal("0")) * previous_filled + price * quantity
        order.filled_quantity += quantity
        order.average_price = total_value / order.filled_quantity
        order.status = (
            OrderStatus.FILLED if order.remaining_quantity == 0 else OrderStatus.PARTIALLY_FILLED
        )
        order.updated_at = datetime.now(UTC)
        self._sequence += 1
        execution = Execution(
            execution_id=f"fake-exec-{self._sequence:08d}",
            order_id=order.order_id,
            client_order_id=order.request.client_order_id,
            symbol=order.request.symbol,
            side=order.request.side,
            quantity=quantity,
            price=price,
        )
        self._executions.append(execution)
        self._position = projected_position

    def fill_limit_order(
        self,
        client_order_id: str,
        quantity: Decimal | None = None,
        price: Decimal | None = None,
    ) -> Order:
        self._require_connection()
        order = self._orders[client_order_id]
        if order.request.order_type is not OrderType.LIMIT:
            raise ValueError("manual fill is only valid for limit orders")
        self._fill(
            order,
            quantity or order.remaining_quantity,
            price or order.request.price or self.last_price,
        )
        return order

    def _project_position(
        self,
        request: OrderRequest,
        quantity: Decimal,
        price: Decimal,
    ) -> Position:
        signed_fill = quantity if request.side is OrderSide.BUY else -quantity
        current_signed = self._position.quantity
        if self._position.side is PositionSide.SHORT:
            current_signed = -current_signed
        new_signed = current_signed + signed_fill
        if request.reduce_only and (
            current_signed == 0
            or abs(new_signed) > abs(current_signed)
            or current_signed * new_signed < 0
        ):
            raise RuntimeError("reduce-only fill would increase or reverse the position")
        if new_signed == 0:
            return Position(self.symbol, PositionSide.FLAT, Decimal("0"), None)
        new_side = PositionSide.LONG if new_signed > 0 else PositionSide.SHORT
        if current_signed == 0 or current_signed * new_signed <= 0:
            average = price
        elif abs(new_signed) > abs(current_signed):
            added = abs(new_signed) - abs(current_signed)
            current_value = (self._position.average_price or price) * abs(current_signed)
            average = (current_value + price * added) / abs(new_signed)
        else:
            average = self._position.average_price or price
        return Position(self.symbol, new_side, abs(new_signed), average)

    def next_candle(self) -> Candle:
        self._require_connection()
        self._sequence += 1
        opened_at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=self._sequence)
        direction = Decimal("1") if self._sequence % 2 else Decimal("-1")
        close = self.last_price + direction * Decimal("25")
        candle = Candle(
            symbol=self.symbol,
            timeframe="1m",
            opened_at=opened_at,
            closed_at=opened_at + timedelta(minutes=1),
            open=self.last_price,
            high=max(self.last_price, close) + Decimal("10"),
            low=min(self.last_price, close) - Decimal("10"),
            close=close,
            volume=Decimal("1.5"),
        )
        self.last_price = close
        return candle
