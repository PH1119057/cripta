from datetime import datetime
from decimal import Decimal
from typing import Any

from bybit_workbench.domain.models import Candle, Order, OrderRequest, Position
from bybit_workbench.domain.types import (
    FillReason,
    OrderRole,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)
from bybit_workbench.stops import validate_stop_update

from .models import (
    AmbiguousBarPolicy,
    ProtectionPlan,
    ReplayConfig,
    ReplayFill,
    ReplayTradeResult,
)


class ReplayEngine:
    """Deterministic candle replay with deliberately conservative intrabar semantics."""

    def __init__(self, symbol: str, config: ReplayConfig | None = None) -> None:
        if not symbol:
            raise ValueError("symbol is required")
        self.symbol = symbol
        self.config = config or ReplayConfig()
        self.position = Position(symbol, PositionSide.FLAT, Decimal("0"), None)
        self.pending_entry: Order | None = None
        self.protection: ProtectionPlan | None = None
        self.fills: list[ReplayFill] = []
        self.completed_trades: list[ReplayTradeResult] = []
        self.total_fees = Decimal("0")
        self.total_funding = Decimal("0")
        self.total_gross_pnl = Decimal("0")
        self._current_entry_fees = Decimal("0")
        self._current_funding = Decimal("0")
        self._opened_at: datetime | None = None
        self._last_closed_at: datetime | None = None
        self._sequence = 0
        self._session_key = "idle"
        self._action_results: dict[str, str | None] = {}
        self._pending_entry_age = 0

    @property
    def protected_quantity(self) -> Decimal:
        if self.protection is None:
            return Decimal("0")
        return self.position.quantity

    @property
    def pending_entry_age(self) -> int:
        return self._pending_entry_age

    @property
    def net_realized_pnl(self) -> Decimal:
        return self.total_gross_pnl - self.total_fees - self.total_funding

    def submit_entry(self, request: OrderRequest, protection: ProtectionPlan) -> Order:
        if request.symbol != self.symbol:
            raise ValueError("entry symbol does not match replay symbol")
        if request.role is not OrderRole.ENTRY or request.reduce_only:
            raise ValueError("replay entry must have Entry role and reduce_only=false")
        if self.pending_entry is not None:
            raise RuntimeError("an entry order is already pending")
        if self.position.side is not PositionSide.FLAT:
            raise RuntimeError("cannot submit a new entry while a position is open")
        if request.price is not None:
            self._validate_initial_protection(request.side, request.price, protection)
        self._sequence += 1
        self._session_key = request.client_order_id
        self.pending_entry = Order(
            order_id=f"replay-order-{request.client_order_id}",
            request=request,
            status=OrderStatus.ACCEPTED,
        )
        self.protection = protection
        self._pending_entry_age = 0
        return self.pending_entry

    def on_candle(
        self,
        candle: Candle,
        mark_candle: Candle | None = None,
    ) -> tuple[ReplayFill, ...]:
        if not candle.is_closed:
            raise ValueError("replay accepts closed candles only")
        if candle.symbol != self.symbol:
            raise ValueError("candle symbol does not match replay symbol")
        if mark_candle is not None and (
            mark_candle.symbol != candle.symbol
            or mark_candle.timeframe != candle.timeframe
            or mark_candle.opened_at != candle.opened_at
            or mark_candle.closed_at != candle.closed_at
        ):
            raise ValueError("Mark Price candle is not aligned with trade candle")
        if self._last_closed_at is not None and candle.closed_at <= self._last_closed_at:
            raise ValueError("replay candles must be strictly chronological")
        self._last_closed_at = candle.closed_at
        fill_count_before = len(self.fills)

        if self.pending_entry is not None:
            eligible = self._pending_entry_age >= self.config.execution_delay_bars
            self._pending_entry_age += 1
            entry_price = (
                self._entry_fill_price(self.pending_entry.request, candle) if eligible else None
            )
            if entry_price is not None:
                quantity = self.pending_entry.remaining_quantity
                if self.config.max_fill_quantity_per_bar is not None:
                    quantity = min(quantity, self.config.max_fill_quantity_per_bar)
                self._apply_entry_fill(
                    self.pending_entry,
                    quantity,
                    entry_price,
                    candle.opened_at,
                    candle.open,
                )

        if self.position.side is not PositionSide.FLAT and self.protection is not None:
            trigger = self._protection_trigger(mark_candle or candle, candle)
            if trigger is not None:
                reason, base_price, ambiguous = trigger
                self._cancel_pending_entry()
                self._close_position(base_price, reason, candle.closed_at, ambiguous)

        return tuple(self.fills[fill_count_before:])

    def update_stop(self, proposed_stop: Decimal, *, allow_risk_expansion: bool = False) -> bool:
        if self.position.side is PositionSide.FLAT or self.protection is None:
            raise RuntimeError("there is no protected open position")
        validate_stop_update(
            self.protection.stop_price,
            proposed_stop,
            self.position.side,
            allow_risk_expansion=allow_risk_expansion,
        )
        if proposed_stop == self.protection.stop_price:
            return False
        self.protection = ProtectionPlan(proposed_stop, self.protection.take_profit)
        return True

    def update_protection(
        self,
        *,
        stop_price: Decimal | None = None,
        take_profit: Decimal | None = None,
    ) -> bool:
        if self.position.side is PositionSide.FLAT or self.protection is None:
            raise RuntimeError("there is no protected open position")
        changed = False
        proposed_stop = self.protection.stop_price if stop_price is None else stop_price
        if proposed_stop != self.protection.stop_price:
            validate_stop_update(
                self.protection.stop_price,
                proposed_stop,
                self.position.side,
            )
            changed = True
        proposed_take = self.protection.take_profit if take_profit is None else take_profit
        reference = self.position.average_price
        if proposed_take is not None and reference is not None:
            if self.position.side is PositionSide.LONG and proposed_take <= reference:
                raise ValueError("long take profit must be above average entry")
            if self.position.side is PositionSide.SHORT and proposed_take >= reference:
                raise ValueError("short take profit must be below average entry")
        changed = changed or proposed_take != self.protection.take_profit
        if changed:
            self.protection = ProtectionPlan(proposed_stop, proposed_take)
        return changed

    def apply_funding(self, cost: Decimal) -> None:
        if self.position.side is PositionSide.FLAT:
            raise RuntimeError("funding cannot be applied without an open position")
        self._current_funding += cost
        self.total_funding += cost

    def cancel_entry_orders(self, action_id: str) -> bool:
        if action_id in self._action_results:
            return False
        cancelled = self._cancel_pending_entry()
        self._action_results[action_id] = None
        return cancelled

    def cancel_all_non_protective_orders(self, action_id: str) -> bool:
        return self.cancel_entry_orders(action_id)

    def flatten_position(
        self,
        action_id: str,
        market_price: Decimal,
        occurred_at: datetime,
    ) -> ReplayFill | None:
        previous = self._action_results.get(action_id, "missing")
        if previous != "missing":
            return self._fill_by_id(previous)
        fill = None
        if self.position.side is not PositionSide.FLAT:
            fill = self._close_position(
                market_price,
                FillReason.EMERGENCY_FLATTEN,
                occurred_at,
                False,
            )
        self._action_results[action_id] = fill.execution_id if fill is not None else None
        return fill

    def exit_position(
        self,
        action_id: str,
        market_price: Decimal,
        occurred_at: datetime,
    ) -> ReplayFill | None:
        previous = self._action_results.get(action_id, "missing")
        if previous != "missing":
            return self._fill_by_id(previous)
        fill = None
        if self.position.side is not PositionSide.FLAT:
            self._cancel_pending_entry()
            fill = self._close_position(
                market_price,
                FillReason.STRATEGY_EXIT,
                occurred_at,
                False,
            )
        self._action_results[action_id] = fill.execution_id if fill is not None else None
        return fill

    def emergency_stop(
        self,
        action_id: str,
        market_price: Decimal,
        occurred_at: datetime,
    ) -> ReplayFill | None:
        previous = self._action_results.get(action_id, "missing")
        if previous != "missing":
            return self._fill_by_id(previous)
        self._cancel_pending_entry()
        fill = None
        if self.position.side is not PositionSide.FLAT:
            fill = self._close_position(
                market_price,
                FillReason.EMERGENCY_FLATTEN,
                occurred_at,
                False,
            )
        self._action_results[action_id] = fill.execution_id if fill is not None else None
        return fill

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": 1,
            "symbol": self.symbol,
            "config": {
                "fee_rate": str(self.config.fee_rate),
                "maker_fee_rate": _decimal_or_none(self.config.maker_fee_rate),
                "taker_fee_rate": _decimal_or_none(self.config.taker_fee_rate),
                "slippage_percent": str(self.config.slippage_percent),
                "max_fill_quantity_per_bar": _decimal_or_none(
                    self.config.max_fill_quantity_per_bar
                ),
                "ambiguous_bar_policy": self.config.ambiguous_bar_policy.value,
                "seed": self.config.seed,
                "execution_delay_bars": self.config.execution_delay_bars,
            },
            "position": _position_to_dict(self.position),
            "pending_entry": _order_to_dict(self.pending_entry),
            "protection": _protection_to_dict(self.protection),
            "fills": [_fill_to_dict(fill) for fill in self.fills],
            "completed_trades": [_trade_to_dict(trade) for trade in self.completed_trades],
            "total_fees": str(self.total_fees),
            "total_funding": str(self.total_funding),
            "total_gross_pnl": str(self.total_gross_pnl),
            "current_entry_fees": str(self._current_entry_fees),
            "current_funding": str(self._current_funding),
            "opened_at": _datetime_or_none(self._opened_at),
            "last_closed_at": _datetime_or_none(self._last_closed_at),
            "sequence": self._sequence,
            "session_key": self._session_key,
            "action_results": dict(self._action_results),
            "pending_entry_age": self._pending_entry_age,
        }

    @classmethod
    def restore(cls, snapshot: dict[str, Any]) -> "ReplayEngine":
        if snapshot.get("version") != 1:
            raise ValueError("unsupported replay snapshot version")
        config_data = snapshot["config"]
        config = ReplayConfig(
            fee_rate=Decimal(config_data["fee_rate"]),
            maker_fee_rate=_parse_optional_decimal(config_data.get("maker_fee_rate")),
            taker_fee_rate=_parse_optional_decimal(config_data.get("taker_fee_rate")),
            slippage_percent=Decimal(config_data["slippage_percent"]),
            max_fill_quantity_per_bar=_parse_optional_decimal(
                config_data["max_fill_quantity_per_bar"]
            ),
            ambiguous_bar_policy=AmbiguousBarPolicy(config_data["ambiguous_bar_policy"]),
            seed=int(config_data["seed"]),
            execution_delay_bars=int(config_data.get("execution_delay_bars", 0)),
        )
        engine = cls(str(snapshot["symbol"]), config)
        engine.position = _position_from_dict(snapshot["position"])
        engine.pending_entry = _order_from_dict(snapshot["pending_entry"])
        engine.protection = _protection_from_dict(snapshot["protection"])
        engine.fills = [_fill_from_dict(item) for item in snapshot["fills"]]
        engine.completed_trades = [_trade_from_dict(item) for item in snapshot["completed_trades"]]
        engine.total_fees = Decimal(snapshot["total_fees"])
        engine.total_funding = Decimal(snapshot["total_funding"])
        engine.total_gross_pnl = Decimal(snapshot["total_gross_pnl"])
        engine._current_entry_fees = Decimal(snapshot["current_entry_fees"])
        engine._current_funding = Decimal(snapshot["current_funding"])
        engine._opened_at = _parse_optional_datetime(snapshot["opened_at"])
        engine._last_closed_at = _parse_optional_datetime(snapshot["last_closed_at"])
        engine._sequence = int(snapshot["sequence"])
        engine._session_key = str(snapshot["session_key"])
        engine._action_results = dict(snapshot["action_results"])
        engine._pending_entry_age = int(snapshot.get("pending_entry_age", 0))
        engine._validate_restored_state()
        return engine

    def _validate_restored_state(self) -> None:
        if self.position.side is not PositionSide.FLAT and self.protection is None:
            raise ValueError("restored open position has no protection")
        if self.pending_entry is not None and self.pending_entry.request.symbol != self.symbol:
            raise ValueError("restored pending order has a different symbol")
        if self.position.side is not PositionSide.FLAT and self._opened_at is None:
            raise ValueError("restored open position has no opening timestamp")

    def _entry_fill_price(self, request: OrderRequest, candle: Candle) -> Decimal | None:
        if request.order_type is OrderType.MARKET:
            return self._with_adverse_slippage(candle.open, request.side)
        assert request.price is not None
        if request.side is OrderSide.BUY and candle.low <= request.price:
            return min(candle.open, request.price)
        if request.side is OrderSide.SELL and candle.high >= request.price:
            return max(candle.open, request.price)
        return None

    def _apply_entry_fill(
        self,
        order: Order,
        quantity: Decimal,
        price: Decimal,
        occurred_at: datetime,
        base_price: Decimal,
    ) -> None:
        if quantity <= 0:
            raise ValueError("entry fill quantity must be positive")
        prior_quantity = self.position.quantity
        prior_value = (self.position.average_price or Decimal("0")) * prior_quantity
        new_quantity = prior_quantity + quantity
        average = (prior_value + price * quantity) / new_quantity
        side = PositionSide.LONG if order.request.side is OrderSide.BUY else PositionSide.SHORT
        self.position = Position(self.symbol, side, new_quantity, average)
        if self._opened_at is None:
            self._opened_at = occurred_at
        fee_rate = (
            self.config.effective_maker_fee_rate
            if order.request.order_type is OrderType.LIMIT
            else self.config.effective_taker_fee_rate
        )
        fee = price * quantity * fee_rate
        slippage_cost = (
            abs(price - base_price) * quantity
            if order.request.order_type is OrderType.MARKET
            else Decimal("0")
        )
        self._current_entry_fees += fee
        self.total_fees += fee
        fill = self._new_fill(
            order.request.client_order_id,
            order.request.side,
            quantity,
            price,
            fee,
            FillReason.ENTRY,
            occurred_at,
            slippage_cost=slippage_cost,
        )
        self.fills.append(fill)
        previous_filled = order.filled_quantity
        total_value = (order.average_price or Decimal("0")) * previous_filled + price * quantity
        order.filled_quantity += quantity
        order.average_price = total_value / order.filled_quantity
        order.status = (
            OrderStatus.FILLED if order.remaining_quantity == 0 else OrderStatus.PARTIALLY_FILLED
        )
        order.updated_at = occurred_at
        if order.status is OrderStatus.FILLED:
            self.pending_entry = None
            self._pending_entry_age = 0

    def _protection_trigger(
        self,
        trigger_candle: Candle,
        execution_candle: Candle,
    ) -> tuple[FillReason, Decimal, bool] | None:
        protection = self._required_protection()
        if self.position.side is PositionSide.LONG:
            stop_touched = trigger_candle.low <= protection.stop_price
            take_touched = (
                protection.take_profit is not None and trigger_candle.high >= protection.take_profit
            )
            stop_base = (
                execution_candle.open
                if execution_candle.open <= protection.stop_price
                else protection.stop_price
            )
            take_base = (
                execution_candle.open
                if protection.take_profit is not None
                and execution_candle.open >= protection.take_profit
                else protection.take_profit
            )
        else:
            stop_touched = trigger_candle.high >= protection.stop_price
            take_touched = (
                protection.take_profit is not None and trigger_candle.low <= protection.take_profit
            )
            stop_base = (
                execution_candle.open
                if execution_candle.open >= protection.stop_price
                else protection.stop_price
            )
            take_base = (
                execution_candle.open
                if protection.take_profit is not None
                and execution_candle.open <= protection.take_profit
                else protection.take_profit
            )
        if stop_touched:
            return FillReason.STOP_LOSS, stop_base, take_touched
        if take_touched:
            assert take_base is not None
            return FillReason.TAKE_PROFIT, take_base, False
        return None

    def _close_position(
        self,
        base_price: Decimal,
        reason: FillReason,
        occurred_at: datetime,
        ambiguous: bool,
    ) -> ReplayFill:
        if self.position.side is PositionSide.FLAT or self.position.average_price is None:
            raise RuntimeError("cannot close a flat position")
        closing_side = OrderSide.SELL if self.position.side is PositionSide.LONG else OrderSide.BUY
        exit_price = self._with_adverse_slippage(base_price, closing_side)
        quantity = self.position.quantity
        slippage_cost = abs(exit_price - base_price) * quantity
        fee = exit_price * quantity * self.config.effective_taker_fee_rate
        gross = (
            (exit_price - self.position.average_price) * quantity
            if self.position.side is PositionSide.LONG
            else (self.position.average_price - exit_price) * quantity
        )
        fill = self._new_fill(
            f"protect-{self._sequence:08d}",
            closing_side,
            quantity,
            exit_price,
            fee,
            reason,
            occurred_at,
            ambiguous,
            slippage_cost,
        )
        self.fills.append(fill)
        self.total_fees += fee
        self.total_gross_pnl += gross
        entry_price = self.position.average_price
        opened_at = self._opened_at or occurred_at
        trade_fees = self._current_entry_fees + fee
        result = ReplayTradeResult(
            symbol=self.symbol,
            side=self.position.side.value,
            quantity=quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            gross_pnl=gross,
            fees=trade_fees,
            funding=self._current_funding,
            net_pnl=gross - trade_fees - self._current_funding,
            exit_reason=reason,
            opened_at=opened_at,
            closed_at=occurred_at,
            ambiguous_bar=ambiguous,
        )
        self.completed_trades.append(result)
        self.position = Position(self.symbol, PositionSide.FLAT, Decimal("0"), None)
        self.protection = None
        self._current_entry_fees = Decimal("0")
        self._current_funding = Decimal("0")
        self._opened_at = None
        return fill

    def _with_adverse_slippage(self, price: Decimal, side: OrderSide) -> Decimal:
        fraction = self.config.slippage_percent / Decimal("100")
        if side is OrderSide.BUY:
            return price * (Decimal("1") + fraction)
        return price * (Decimal("1") - fraction)

    def _cancel_pending_entry(self) -> bool:
        if self.pending_entry is None:
            return False
        self.pending_entry.status = OrderStatus.CANCELLED
        self.pending_entry = None
        self._pending_entry_age = 0
        if self.position.side is PositionSide.FLAT:
            self.protection = None
        return True

    def _new_fill(
        self,
        client_order_id: str,
        side: OrderSide,
        quantity: Decimal,
        price: Decimal,
        fee: Decimal,
        reason: FillReason,
        occurred_at: datetime,
        ambiguous: bool = False,
        slippage_cost: Decimal = Decimal("0"),
    ) -> ReplayFill:
        self._sequence += 1
        return ReplayFill(
            execution_id=f"replay-exec-{self._session_key}-{self._sequence:08d}",
            client_order_id=client_order_id,
            side=side,
            quantity=quantity,
            price=price,
            fee=fee,
            reason=reason,
            occurred_at=occurred_at,
            ambiguous_bar=ambiguous,
            slippage_cost=slippage_cost,
        )

    def _fill_by_id(self, execution_id: str | None) -> ReplayFill | None:
        if execution_id is None:
            return None
        return next(fill for fill in self.fills if fill.execution_id == execution_id)

    def _required_protection(self) -> ProtectionPlan:
        if self.protection is None:
            raise RuntimeError("entry has no protection plan")
        return self.protection

    @staticmethod
    def _validate_initial_protection(
        side: OrderSide,
        entry_price: Decimal,
        protection: ProtectionPlan,
    ) -> None:
        if side is OrderSide.BUY:
            if protection.stop_price >= entry_price:
                raise ValueError("long stop must be below entry fill")
            if protection.take_profit is not None and protection.take_profit <= entry_price:
                raise ValueError("long take profit must be above entry fill")
        else:
            if protection.stop_price <= entry_price:
                raise ValueError("short stop must be above entry fill")
            if protection.take_profit is not None and protection.take_profit >= entry_price:
                raise ValueError("short take profit must be below entry fill")


def _decimal_or_none(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _datetime_or_none(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse_optional_decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _parse_optional_datetime(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _position_to_dict(position: Position) -> dict[str, str | None]:
    return {
        "symbol": position.symbol,
        "side": position.side.value,
        "quantity": str(position.quantity),
        "average_price": _decimal_or_none(position.average_price),
    }


def _position_from_dict(data: dict[str, str | None]) -> Position:
    return Position(
        str(data["symbol"]),
        PositionSide(str(data["side"])),
        Decimal(str(data["quantity"])),
        _parse_optional_decimal(data["average_price"]),
    )


def _order_to_dict(order: Order | None) -> dict[str, Any] | None:
    if order is None:
        return None
    request = order.request
    return {
        "order_id": order.order_id,
        "request": {
            "client_order_id": request.client_order_id,
            "symbol": request.symbol,
            "side": request.side.value,
            "order_type": request.order_type.value,
            "quantity": str(request.quantity),
            "price": _decimal_or_none(request.price),
            "reduce_only": request.reduce_only,
            "role": request.role.value,
        },
        "status": order.status.value,
        "filled_quantity": str(order.filled_quantity),
        "average_price": _decimal_or_none(order.average_price),
        "created_at": order.created_at.isoformat(),
        "updated_at": order.updated_at.isoformat(),
    }


def _order_from_dict(data: dict[str, Any] | None) -> Order | None:
    if data is None:
        return None
    request_data = data["request"]
    request = OrderRequest(
        client_order_id=request_data["client_order_id"],
        symbol=request_data["symbol"],
        side=OrderSide(request_data["side"]),
        order_type=OrderType(request_data["order_type"]),
        quantity=Decimal(request_data["quantity"]),
        price=_parse_optional_decimal(request_data["price"]),
        reduce_only=bool(request_data["reduce_only"]),
        role=OrderRole(request_data["role"]),
    )
    return Order(
        order_id=data["order_id"],
        request=request,
        status=OrderStatus(data["status"]),
        filled_quantity=Decimal(data["filled_quantity"]),
        average_price=_parse_optional_decimal(data["average_price"]),
        created_at=datetime.fromisoformat(data["created_at"]),
        updated_at=datetime.fromisoformat(data["updated_at"]),
    )


def _protection_to_dict(protection: ProtectionPlan | None) -> dict[str, str | None] | None:
    if protection is None:
        return None
    return {
        "stop_price": str(protection.stop_price),
        "take_profit": _decimal_or_none(protection.take_profit),
    }


def _protection_from_dict(data: dict[str, str | None] | None) -> ProtectionPlan | None:
    if data is None:
        return None
    return ProtectionPlan(
        Decimal(str(data["stop_price"])),
        _parse_optional_decimal(data["take_profit"]),
    )


def _fill_to_dict(fill: ReplayFill) -> dict[str, Any]:
    return {
        "execution_id": fill.execution_id,
        "client_order_id": fill.client_order_id,
        "side": fill.side.value,
        "quantity": str(fill.quantity),
        "price": str(fill.price),
        "fee": str(fill.fee),
        "slippage_cost": str(fill.slippage_cost),
        "reason": fill.reason.value,
        "occurred_at": fill.occurred_at.isoformat(),
        "ambiguous_bar": fill.ambiguous_bar,
    }


def _fill_from_dict(data: dict[str, Any]) -> ReplayFill:
    return ReplayFill(
        execution_id=data["execution_id"],
        client_order_id=data["client_order_id"],
        side=OrderSide(data["side"]),
        quantity=Decimal(data["quantity"]),
        price=Decimal(data["price"]),
        fee=Decimal(data["fee"]),
        reason=FillReason(data["reason"]),
        occurred_at=datetime.fromisoformat(data["occurred_at"]),
        ambiguous_bar=bool(data["ambiguous_bar"]),
        slippage_cost=Decimal(str(data.get("slippage_cost", "0"))),
    )


def _trade_to_dict(trade: ReplayTradeResult) -> dict[str, Any]:
    return {
        "symbol": trade.symbol,
        "side": trade.side,
        "quantity": str(trade.quantity),
        "entry_price": str(trade.entry_price),
        "exit_price": str(trade.exit_price),
        "gross_pnl": str(trade.gross_pnl),
        "fees": str(trade.fees),
        "funding": str(trade.funding),
        "net_pnl": str(trade.net_pnl),
        "exit_reason": trade.exit_reason.value,
        "opened_at": trade.opened_at.isoformat(),
        "closed_at": trade.closed_at.isoformat(),
        "ambiguous_bar": trade.ambiguous_bar,
    }


def _trade_from_dict(data: dict[str, Any]) -> ReplayTradeResult:
    return ReplayTradeResult(
        symbol=data["symbol"],
        side=data["side"],
        quantity=Decimal(data["quantity"]),
        entry_price=Decimal(data["entry_price"]),
        exit_price=Decimal(data["exit_price"]),
        gross_pnl=Decimal(data["gross_pnl"]),
        fees=Decimal(data["fees"]),
        funding=Decimal(data["funding"]),
        net_pnl=Decimal(data["net_pnl"]),
        exit_reason=FillReason(data["exit_reason"]),
        opened_at=datetime.fromisoformat(data["opened_at"]),
        closed_at=datetime.fromisoformat(data["closed_at"]),
        ambiguous_bar=bool(data["ambiguous_bar"]),
    )
