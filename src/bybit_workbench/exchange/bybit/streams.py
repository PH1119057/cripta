from collections import deque
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from threading import RLock
from typing import Any

from bybit_workbench.domain.models import Candle, Execution, Order
from bybit_workbench.domain.types import OrderSide
from bybit_workbench.execution import ExecutionLedger, OrderTracker, OrderUpdate

from .errors import BybitProtocolError
from .health import HealthMonitor
from .mappers import (
    map_account,
    map_order,
    map_position,
    map_ticker,
    map_ws_klines,
    timestamp_ms,
)
from .models import AccountSnapshot, BybitPositionSnapshot, TickerSnapshot


@dataclass(frozen=True, slots=True)
class BybitStreamSnapshot:
    ticker: TickerSnapshot | None
    latest_candle: Candle | None
    account: AccountSnapshot | None
    position: BybitPositionSnapshot | None
    orders: tuple[Order, ...]
    executions: tuple[Execution, ...]


class BybitStreamProcessor:
    def __init__(self, symbol: str, health: HealthMonitor | None = None) -> None:
        self.symbol = symbol
        self.health = health or HealthMonitor()
        self.ticker: TickerSnapshot | None = None
        self.latest_candle: Candle | None = None
        self.account: AccountSnapshot | None = None
        self.position: BybitPositionSnapshot | None = None
        self.orders: dict[str, OrderTracker] = {}
        self.executions = ExecutionLedger()
        self._ticker_raw: dict[str, Any] | None = None
        self._closed_candle_keys: set[tuple[str, str, datetime]] = set()
        self._closed_candle_order: deque[tuple[str, str, datetime]] = deque()
        self._position_keys: set[tuple[str, int | None, datetime]] = set()
        self._lock = RLock()

    def on_public(
        self,
        message: Mapping[str, Any],
        *,
        received_at: datetime,
    ) -> Candle | TickerSnapshot | None:
        with self._lock:
            try:
                result = self._process_public(message)
            except Exception as exc:
                self.health.mark_error("public", str(exc))
                raise
            self.health.mark_message("public", received_at)
            return result

    def _process_public(
        self,
        message: Mapping[str, Any],
    ) -> Candle | TickerSnapshot | None:
        if message.get("op") in {"pong", "ping", "subscribe"}:
            return None
        topic = str(message.get("topic", ""))
        if topic.startswith("kline."):
            emitted: Candle | None = None
            for candle in map_ws_klines(message):
                if candle.symbol != self.symbol:
                    continue
                self.latest_candle = candle
                if not candle.is_closed:
                    continue
                key = (candle.symbol, candle.timeframe, candle.opened_at)
                if key in self._closed_candle_keys:
                    continue
                self._closed_candle_keys.add(key)
                self._closed_candle_order.append(key)
                while len(self._closed_candle_order) > 10_000:
                    expired = self._closed_candle_order.popleft()
                    self._closed_candle_keys.discard(expired)
                emitted = candle
            return emitted
        if topic.startswith("tickers."):
            raw_data = message.get("data")
            if not isinstance(raw_data, Mapping):
                raise BybitProtocolError("ticker data must be an object")
            if message.get("type") == "snapshot":
                self._ticker_raw = dict(raw_data)
            else:
                if self._ticker_raw is None:
                    raise BybitProtocolError("ticker delta received before snapshot")
                self._ticker_raw.update(raw_data)
            mapped_message = dict(message)
            mapped_message["data"] = dict(self._ticker_raw)
            ticker = map_ticker(mapped_message)
            if ticker.symbol != self.symbol:
                return None
            self.ticker = ticker
            return ticker
        raise BybitProtocolError(f"unsupported public topic: {topic!r}")

    def on_private(
        self,
        message: Mapping[str, Any],
        *,
        received_at: datetime,
    ) -> tuple[Any, ...]:
        with self._lock:
            try:
                result = self._process_private(message, received_at)
            except Exception as exc:
                self.health.mark_error("private", str(exc))
                raise
            self.health.mark_message("private", received_at)
            return result

    def snapshot(self) -> BybitStreamSnapshot:
        """Copy callback-owned state for consumption on another thread."""

        with self._lock:
            return BybitStreamSnapshot(
                self.ticker,
                self.latest_candle,
                self.account,
                self.position,
                tuple(deepcopy(tracker.order) for tracker in self.orders.values()),
                deepcopy(self.executions.executions),
            )

    def _process_private(
        self,
        message: Mapping[str, Any],
        received_at: datetime,
    ) -> tuple[Any, ...]:
        if message.get("op") in {"pong", "ping", "auth", "subscribe"}:
            return ()
        topic = str(message.get("topic", ""))
        base_topic = topic.split(".", 1)[0]
        data = message.get("data")
        if not isinstance(data, list):
            raise BybitProtocolError("private message data must be an array")
        observed_at = _message_time(message, received_at)
        changed: list[Any] = []
        if base_topic == "wallet":
            for item in data:
                account = map_account(_mapping(item), observed_at)
                self.account = account
                changed.append(account)
            return tuple(changed)
        if base_topic == "position":
            for item in data:
                mapped = map_position(_mapping(item), observed_at)
                if mapped.position.symbol != self.symbol:
                    continue
                key = (mapped.position.symbol, mapped.sequence, mapped.observed_at)
                if key in self._position_keys:
                    continue
                self._position_keys.add(key)
                if len(self._position_keys) > 10_000:
                    self._position_keys.clear()
                    self._position_keys.add(key)
                self.position = mapped
                changed.append(mapped)
            return tuple(changed)
        if base_topic == "order":
            for item in data:
                raw = _mapping(item)
                mapped_order = map_order(raw)
                if mapped_order.request.symbol != self.symbol:
                    continue
                tracker = self.orders.get(mapped_order.order_id)
                if tracker is None:
                    tracker = OrderTracker(mapped_order)
                    self.orders[mapped_order.order_id] = tracker
                    changed.append(mapped_order)
                event_id = _order_event_id(message, raw)
                update = OrderUpdate(
                    event_id=event_id,
                    order_id=mapped_order.order_id,
                    client_order_id=mapped_order.request.client_order_id,
                    status=mapped_order.status,
                    cumulative_filled_quantity=mapped_order.filled_quantity,
                    average_price=mapped_order.average_price,
                    occurred_at=mapped_order.updated_at,
                )
                if tracker.apply(update) and tracker.order not in changed:
                    changed.append(tracker.order)
            return tuple(changed)
        if base_topic == "execution":
            for item in data:
                raw = _mapping(item)
                if str(raw.get("symbol")) != self.symbol:
                    continue
                execution = Execution(
                    execution_id=str(raw["execId"]),
                    order_id=str(raw["orderId"]),
                    client_order_id=str(raw.get("orderLinkId") or ""),
                    symbol=str(raw["symbol"]),
                    side=OrderSide(str(raw["side"])),
                    quantity=_decimal(raw.get("execQty")),
                    price=_decimal(raw.get("execPrice")),
                    executed_at=timestamp_ms(raw["execTime"]),
                )
                if self.executions.record(execution):
                    changed.append(execution)
            return tuple(changed)
        raise BybitProtocolError(f"unsupported private topic: {topic!r}")


def _message_time(message: Mapping[str, Any], fallback: datetime) -> datetime:
    value = message.get("creationTime") or message.get("ts")
    return fallback if value in (None, "") else timestamp_ms(value)


def _order_event_id(message: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    return ":".join(
        (
            str(message.get("id") or message.get("creationTime") or "message"),
            str(item.get("orderId")),
            str(item.get("updatedTime")),
            str(item.get("orderStatus")),
            str(item.get("cumExecQty")),
        )
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BybitProtocolError("stream data item must be an object")
    return value


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise BybitProtocolError(f"invalid decimal stream field: {value!r}") from exc
