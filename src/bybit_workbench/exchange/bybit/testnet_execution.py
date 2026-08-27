from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from pydantic import ConfigDict
from pydantic.dataclasses import dataclass

from bybit_workbench.domain.models import Order, OrderRequest, Position, require_positive
from bybit_workbench.domain.types import (
    OrderRole,
    OrderSide,
    OrderType,
    PositionSide,
)

from .errors import BybitProtocolError, classify_bybit_error
from .models import BybitPositionSnapshot
from .rest import BybitReadOnlyAdapter
from .write_transport import BybitWriteTransport

_DTO_CONFIG = ConfigDict(strict=True, extra="forbid", arbitrary_types_allowed=True)


class BybitWriteRejected(RuntimeError):
    def __init__(self, endpoint: str, ret_code: Any, message: Any) -> None:
        super().__init__(
            f"Bybit {endpoint} rejected request: retCode={ret_code!r} retMsg={message!r}"
        )
        self.endpoint = endpoint
        self.ret_code = ret_code
        self.category = classify_bybit_error(ret_code)


@dataclass(frozen=True, slots=True, config=_DTO_CONFIG)
class ExchangeProtectionPlan:
    stop_loss: Decimal
    take_profit: Decimal | None = None
    trailing_distance: Decimal | None = None
    trailing_active_price: Decimal | None = None

    def __post_init__(self) -> None:
        require_positive(self.stop_loss, "stop_loss")
        for name in ("take_profit", "trailing_distance", "trailing_active_price"):
            value = getattr(self, name)
            if value is not None:
                require_positive(value, name)
        if self.trailing_active_price is not None and self.trailing_distance is None:
            raise ValueError("trailing active price requires a trailing distance")


@dataclass(frozen=True, slots=True, config=_DTO_CONFIG)
class BybitOrderAcknowledgement:
    order_id: str
    client_order_id: str


class BybitTestnetExecutionAdapter:
    """Linear one-way Testnet trading API with a deliberately tiny surface."""

    def __init__(
        self,
        write_transport: BybitWriteTransport,
        read_adapter: BybitReadOnlyAdapter,
        *,
        category: str = "linear",
        position_idx: int = 0,
    ) -> None:
        if category != "linear" or position_idx != 0:
            raise ValueError("first execution slice supports linear one-way positions only")
        self._write = write_transport
        self._read = read_adapter
        self.category = category
        self.position_idx = position_idx

    async def place_entry(
        self,
        request: OrderRequest,
        protection: ExchangeProtectionPlan,
    ) -> BybitOrderAcknowledgement:
        if request.role is not OrderRole.ENTRY or request.reduce_only:
            raise ValueError("place_entry requires a non-reduce-only entry request")
        if request.order_type is not OrderType.LIMIT:
            raise PermissionError(
                "the first Testnet execution slice permits limit entries only; "
                "market entry cannot combine the configured slippage cap with attached TP/SL"
            )
        params: dict[str, Any] = {
            "category": self.category,
            "symbol": request.symbol,
            "side": request.side.value,
            "orderType": request.order_type.value,
            "qty": str(request.quantity),
            "positionIdx": self.position_idx,
            "orderLinkId": request.client_order_id,
            "reduceOnly": False,
            "stopLoss": str(protection.stop_loss),
            "slTriggerBy": "MarkPrice",
            "tpslMode": "Full",
            "slOrderType": "Market",
        }
        if request.price is None:
            raise ValueError("limit entry requires price")
        params["price"] = str(request.price)
        params["timeInForce"] = "GTC"
        if protection.take_profit is not None:
            params.update(
                takeProfit=str(protection.take_profit),
                tpTriggerBy="MarkPrice",
                tpOrderType="Market",
            )
        return _acknowledgement(
            await self._post("/v5/order/create", params),
            "/v5/order/create",
            request.client_order_id,
        )

    async def cancel_entry(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> BybitOrderAcknowledgement:
        if not order_id and not client_order_id:
            raise ValueError("order_id or client_order_id is required")
        params: dict[str, Any] = {"category": self.category, "symbol": symbol}
        if order_id:
            params["orderId"] = order_id
        elif client_order_id:
            params["orderLinkId"] = client_order_id
        return _acknowledgement(
            await self._post("/v5/order/cancel", params),
            "/v5/order/cancel",
            client_order_id or "",
        )

    async def cancel_order(
        self,
        *,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> BybitOrderAcknowledgement:
        return await self.cancel_entry(
            symbol=symbol,
            order_id=order_id,
            client_order_id=client_order_id,
        )

    async def open_orders(self, symbol: str) -> list[Order]:
        return await self._read.open_orders(symbol)

    async def set_full_protection(
        self,
        symbol: str,
        protection: ExchangeProtectionPlan,
    ) -> None:
        params: dict[str, Any] = {
            "category": self.category,
            "symbol": symbol,
            "tpslMode": "Full",
            "positionIdx": self.position_idx,
            "stopLoss": str(protection.stop_loss),
            "slTriggerBy": "MarkPrice",
            "slOrderType": "Market",
        }
        if protection.take_profit is not None:
            params.update(
                takeProfit=str(protection.take_profit),
                tpTriggerBy="MarkPrice",
                tpOrderType="Market",
            )
        if protection.trailing_distance is not None:
            params["trailingStop"] = str(protection.trailing_distance)
        if protection.trailing_active_price is not None:
            params["activePrice"] = str(protection.trailing_active_price)
        await self._post("/v5/position/trading-stop", params)

    async def emergency_close(
        self,
        position: Position,
        client_order_id: str,
    ) -> BybitOrderAcknowledgement | None:
        if position.side is PositionSide.FLAT or position.quantity == 0:
            return None
        side = OrderSide.SELL if position.side is PositionSide.LONG else OrderSide.BUY
        params = {
            "category": self.category,
            "symbol": position.symbol,
            "side": side.value,
            "orderType": OrderType.MARKET.value,
            "qty": str(position.quantity),
            "positionIdx": self.position_idx,
            "orderLinkId": client_order_id,
            "reduceOnly": True,
        }
        return _acknowledgement(
            await self._post("/v5/order/create", params),
            "/v5/order/create",
            client_order_id,
        )

    async def position(self, symbol: str) -> BybitPositionSnapshot:
        return await self._read.position_snapshot(symbol)

    async def order_by_client_id(self, symbol: str, client_order_id: str) -> Order | None:
        return await self._read.order_by_client_id(symbol, client_order_id)

    async def _post(
        self,
        endpoint: str,
        params: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        response = await self._write.post(endpoint, params)
        if response.get("retCode") != 0:
            raise BybitWriteRejected(
                endpoint,
                response.get("retCode"),
                response.get("retMsg"),
            )
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise BybitProtocolError("Bybit write response.result must be an object")
        return response


def _acknowledgement(
    response: Mapping[str, Any],
    endpoint: str,
    fallback_client_id: str,
) -> BybitOrderAcknowledgement:
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise BybitProtocolError(f"Bybit {endpoint} response.result must be an object")
    order_id = str(result.get("orderId") or "")
    client_order_id = str(result.get("orderLinkId") or fallback_client_id)
    if not order_id:
        raise BybitProtocolError(f"Bybit {endpoint} acknowledgement has no orderId")
    return BybitOrderAcknowledgement(order_id, client_order_id)
