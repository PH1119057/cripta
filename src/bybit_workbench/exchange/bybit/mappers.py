import calendar
import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from bybit_workbench.domain.models import Candle, InstrumentRules, Order, OrderRequest, Position
from bybit_workbench.domain.types import (
    OrderRole,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)

from .errors import BybitModeMismatch, BybitProtocolError
from .models import AccountSnapshot, BybitPositionSnapshot, TickerSnapshot


def decimal_value(value: Any, *, optional: bool = False) -> Decimal | None:
    if value in (None, ""):
        if optional:
            return None
        raise BybitProtocolError("required decimal field is empty")
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise BybitProtocolError(f"invalid decimal value: {value!r}") from exc


def timestamp_ms(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except Exception as exc:
        raise BybitProtocolError(f"invalid millisecond timestamp: {value!r}") from exc


def map_instrument(item: Mapping[str, Any]) -> InstrumentRules:
    price_filter = _mapping(item.get("priceFilter"), "priceFilter")
    lot_filter = _mapping(item.get("lotSizeFilter"), "lotSizeFilter")
    max_limit = lot_filter.get("maxOrderQty") or lot_filter.get("maxLimitOrderQty")
    max_market = lot_filter.get("maxMktOrderQty") or lot_filter.get("maxMarketOrderQty")
    return InstrumentRules(
        symbol=str(item["symbol"]),
        tick_size=_required_decimal(price_filter.get("tickSize")),
        qty_step=_required_decimal(lot_filter.get("qtyStep")),
        min_order_qty=_required_decimal(lot_filter.get("minOrderQty")),
        min_notional=_required_decimal(lot_filter.get("minNotionalValue")),
        max_order_qty=_required_decimal(max_limit),
        max_market_order_qty=_optional_decimal(max_market),
    )


def map_account(item: Mapping[str, Any], observed_at: datetime) -> AccountSnapshot:
    account_type = str(item["accountType"])
    total_fields = (
        item.get("totalEquity"),
        item.get("totalAvailableBalance"),
        item.get("totalWalletBalance"),
        item.get("totalPerpUPL"),
    )
    totals_present = all(value not in (None, "") for value in total_fields)
    if totals_present:
        totals = tuple(_required_decimal(value) for value in total_fields)
        # Bybit documents account-wide wallet fields as not applicable to isolated
        # margin. Some accounts return empty strings, while others can return zero
        # placeholders even when the per-coin rows contain real assets. Never let
        # those placeholders overwrite a non-zero Unified balance.
        if any(value != 0 for value in totals) or not _coin_rows_have_value(item.get("coin")):
            return AccountSnapshot(
                account_type=account_type,
                equity=totals[0],
                available_balance=totals[1],
                wallet_balance=totals[2],
                unrealized_pnl=totals[3],
                observed_at=observed_at,
            )

    equity, available, wallet_balance, unrealized = _isolated_wallet_totals(item.get("coin"))
    return AccountSnapshot(
        account_type=account_type,
        equity=equity,
        available_balance=available,
        wallet_balance=wallet_balance,
        unrealized_pnl=unrealized,
        observed_at=observed_at,
    )


def _coin_rows_have_value(value: Any) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return False
    fields = (
        "equity",
        "usdValue",
        "walletBalance",
        "unrealisedPnl",
        "totalPositionIM",
        "totalOrderIM",
        "locked",
        "bonus",
    )
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        for field in fields:
            amount = _optional_decimal(raw.get(field))
            if amount is not None and amount != 0:
                return True
    return False


def _isolated_wallet_totals(value: Any) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise BybitProtocolError(
            "account-wide wallet fields are empty and isolated coin balances are unavailable"
        )

    equity_total = Decimal("0")
    available_total = Decimal("0")
    wallet_total = Decimal("0")
    unrealized_total = Decimal("0")
    for raw in value:
        coin = _mapping(raw, "wallet coin")
        coin_name = str(coin.get("coin") or "<unknown>")
        equity = _required_decimal(coin.get("equity"))
        usd_value = _required_decimal(coin.get("usdValue"))
        wallet_balance = _required_decimal(coin.get("walletBalance"))
        unrealized = _optional_decimal(coin.get("unrealisedPnl")) or Decimal("0")
        position_im = _optional_decimal(coin.get("totalPositionIM")) or Decimal("0")
        order_im = _optional_decimal(coin.get("totalOrderIM")) or Decimal("0")
        locked = _optional_decimal(coin.get("locked")) or Decimal("0")
        bonus = _optional_decimal(coin.get("bonus")) or Decimal("0")

        if equity == 0:
            if any(
                amount != 0
                for amount in (
                    usd_value,
                    wallet_balance,
                    unrealized,
                    position_im,
                    order_im,
                    locked,
                    bonus,
                )
            ):
                raise BybitProtocolError(
                    f"cannot derive isolated USD balances for {coin_name}: equity is zero"
                )
            continue

        usd_per_coin = usd_value / equity
        available_coin = wallet_balance - position_im - order_im - locked - bonus
        equity_total += usd_value
        wallet_total += wallet_balance * usd_per_coin
        unrealized_total += unrealized * usd_per_coin
        available_total += max(Decimal("0"), available_coin) * usd_per_coin

    if equity_total <= 0:
        raise BybitProtocolError("isolated wallet equity is not positive")
    return equity_total, available_total, wallet_total, unrealized_total

def map_position(
    item: Mapping[str, Any],
    observed_at: datetime,
    *,
    require_one_way: bool = True,
) -> BybitPositionSnapshot:
    position_idx = int(item.get("positionIdx", -1))
    if position_idx not in {0, 1, 2}:
        raise BybitProtocolError(f"unknown positionIdx={position_idx}")
    if require_one_way and position_idx != 0:
        raise BybitModeMismatch(
            f"expected one-way positionIdx=0, received positionIdx={position_idx}"
        )
    quantity = _required_decimal(item.get("size"))
    raw_side = str(item.get("side", ""))
    if quantity == 0 or raw_side == "":
        side = PositionSide.FLAT
        quantity = Decimal("0")
        average_price = None
    elif raw_side == OrderSide.BUY.value:
        side = PositionSide.LONG
        average_price = _required_decimal(item.get("avgPrice"))
    elif raw_side == OrderSide.SELL.value:
        side = PositionSide.SHORT
        average_price = _required_decimal(item.get("avgPrice"))
    else:
        raise BybitProtocolError(f"unknown position side: {raw_side!r}")
    sequence = item.get("seq")
    updated = item.get("updatedTime")
    return BybitPositionSnapshot(
        position=Position(str(item["symbol"]), side, quantity, average_price),
        position_idx=position_idx,
        leverage=_optional_decimal(item.get("leverage")),
        mark_price=_optional_decimal(item.get("markPrice")),
        liquidation_price=_optional_decimal(item.get("liqPrice")),
        stop_loss=_zero_as_none(item.get("stopLoss")),
        take_profit=_zero_as_none(item.get("takeProfit")),
        trailing_stop_distance=_zero_as_none(item.get("trailingStop")),
        unrealized_pnl=_optional_decimal(item.get("unrealisedPnl")) or Decimal("0"),
        sequence=None if sequence in (None, "") else int(str(sequence)),
        observed_at=timestamp_ms(updated) if updated not in (None, "", "0", 0) else observed_at,
        break_even_price=_zero_as_none(item.get("breakEvenPrice")),
    )


def map_order(item: Mapping[str, Any]) -> Order:
    order_id = str(item["orderId"])
    client_order_id = str(item.get("orderLinkId") or _external_client_id(order_id))
    side = OrderSide(str(item["side"]))
    order_type = OrderType(str(item["orderType"]))
    reduce_only = bool(item.get("reduceOnly", False))
    stop_type = str(item.get("stopOrderType", ""))
    role = OrderRole.ENTRY
    if reduce_only:
        role = OrderRole.PROTECTIVE if stop_type else OrderRole.EXIT
    price = _optional_decimal(item.get("price"))
    if order_type is OrderType.MARKET:
        price = None
    request = OrderRequest(
        client_order_id=client_order_id,
        symbol=str(item["symbol"]),
        side=side,
        order_type=order_type,
        quantity=_required_decimal(item.get("qty")),
        price=price,
        reduce_only=reduce_only,
        role=role,
    )
    return Order(
        order_id=order_id,
        request=request,
        status=_order_status(str(item["orderStatus"])),
        filled_quantity=_optional_decimal(item.get("cumExecQty")) or Decimal("0"),
        average_price=_optional_decimal(item.get("avgPrice")),
        created_at=timestamp_ms(item["createdTime"]),
        updated_at=timestamp_ms(item["updatedTime"]),
    )


def map_rest_klines(
    rows: Sequence[Sequence[Any]],
    *,
    symbol: str,
    interval: str,
    observed_at: datetime,
) -> list[Candle]:
    candles: list[Candle] = []
    for row in rows:
        if len(row) < 6:
            raise BybitProtocolError("kline row has fewer than six fields")
        opened_at = timestamp_ms(row[0])
        closed_at = interval_end(opened_at, interval)
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=interval,
                opened_at=opened_at,
                closed_at=closed_at,
                open=_required_decimal(row[1]),
                high=_required_decimal(row[2]),
                low=_required_decimal(row[3]),
                close=_required_decimal(row[4]),
                volume=_required_decimal(row[5]),
                is_closed=closed_at <= observed_at,
            )
        )
    return sorted(candles, key=lambda item: item.opened_at)


def map_ws_klines(message: Mapping[str, Any]) -> tuple[Candle, ...]:
    """Map every kline item carried by one public WebSocket message.

    Bybit documents kline ``data`` as an array. Most pushes contain a single
    candle, but the transport contract must not assume that a batch can never
    contain more than one item. Empty batches are harmless no-op messages.
    """

    data = message.get("data")
    if not isinstance(data, list):
        raise BybitProtocolError("kline data must be an array")
    topic = str(message.get("topic", ""))
    parts = topic.split(".")
    if len(parts) != 3 or parts[0] != "kline":
        raise BybitProtocolError(f"invalid kline topic: {topic!r}")

    candles: list[Candle] = []
    for raw in data:
        item = _mapping(raw, "kline data")
        candles.append(
            Candle(
                symbol=parts[2],
                timeframe=str(item["interval"]),
                opened_at=timestamp_ms(item["start"]),
                closed_at=timestamp_ms(int(item["end"]) + 1),
                open=_required_decimal(item["open"]),
                high=_required_decimal(item["high"]),
                low=_required_decimal(item["low"]),
                close=_required_decimal(item["close"]),
                volume=_required_decimal(item["volume"]),
                is_closed=bool(item["confirm"]),
            )
        )
    return tuple(sorted(candles, key=lambda candle: candle.opened_at))


def map_ws_kline(message: Mapping[str, Any]) -> Candle:
    """Compatibility mapper for callers that explicitly require one candle."""

    candles = map_ws_klines(message)
    if len(candles) != 1:
        raise BybitProtocolError("expected exactly one kline update")
    return candles[0]


def map_ticker(message: Mapping[str, Any]) -> TickerSnapshot:
    item = _mapping(message.get("data"), "ticker data")
    next_funding = item.get("nextFundingTime")
    return TickerSnapshot(
        symbol=str(item["symbol"]),
        last_price=_required_decimal(item.get("lastPrice")),
        mark_price=_optional_decimal(item.get("markPrice")),
        index_price=_optional_decimal(item.get("indexPrice")),
        funding_rate=_optional_decimal(item.get("fundingRate")),
        next_funding_at=(None if next_funding in (None, "") else timestamp_ms(next_funding)),
        observed_at=timestamp_ms(message["ts"]),
    )


def interval_end(opened_at: datetime, interval: str) -> datetime:
    if interval.isdigit():
        return opened_at + timedelta(minutes=int(interval))
    if interval == "D":
        return opened_at + timedelta(days=1)
    if interval == "W":
        return opened_at + timedelta(days=7)
    if interval == "M":
        year = opened_at.year + (1 if opened_at.month == 12 else 0)
        month = 1 if opened_at.month == 12 else opened_at.month + 1
        day = min(opened_at.day, calendar.monthrange(year, month)[1])
        return opened_at.replace(year=year, month=month, day=day)
    raise BybitProtocolError(f"unsupported kline interval: {interval!r}")


def _order_status(value: str) -> OrderStatus:
    mapping = {
        "Created": OrderStatus.CREATED,
        "New": OrderStatus.ACCEPTED,
        "Active": OrderStatus.ACCEPTED,
        "PartiallyFilled": OrderStatus.PARTIALLY_FILLED,
        "PartiallyFilledCanceled": OrderStatus.CANCELLED,
        "Filled": OrderStatus.FILLED,
        "Untriggered": OrderStatus.ACCEPTED,
        "Triggered": OrderStatus.ACCEPTED,
        "Cancelled": OrderStatus.CANCELLED,
        "Canceled": OrderStatus.CANCELLED,
        "Rejected": OrderStatus.REJECTED,
        "Deactivated": OrderStatus.CANCELLED,
    }
    try:
        return mapping[value]
    except KeyError as exc:
        raise BybitProtocolError(f"unknown order status: {value!r}") from exc


def _external_client_id(order_id: str) -> str:
    digest = hashlib.sha256(order_id.encode("utf-8")).hexdigest()[:24]
    return f"external-{digest}"


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BybitProtocolError(f"{field} must be an object")
    return value


def _required_decimal(value: Any) -> Decimal:
    result = decimal_value(value)
    assert result is not None
    return result


def _optional_decimal(value: Any) -> Decimal | None:
    return decimal_value(value, optional=True)


def _zero_as_none(value: Any) -> Decimal | None:
    parsed = _optional_decimal(value)
    return None if parsed == 0 else parsed
