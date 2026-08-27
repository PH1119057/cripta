import asyncio
from collections.abc import Mapping
from typing import Any, Protocol

from .rate_limit import AsyncRateLimiter


class BybitReadTransport(Protocol):
    async def get(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        authenticated: bool,
    ) -> Mapping[str, Any]: ...


class PybitReadOnlyTransport:
    """Read-only router around an injected official pybit HTTP session."""

    _METHODS = {
        "/v5/market/time": "get_server_time",
        "/v5/user/query-api": "get_api_key_information",
        "/v5/market/instruments-info": "get_instruments_info",
        "/v5/market/kline": "get_kline",
        "/v5/market/mark-price-kline": "get_mark_price_kline",
        "/v5/market/funding/history": "get_funding_rate_history",
        "/v5/account/wallet-balance": "get_wallet_balance",
        "/v5/position/list": "get_positions",
        "/v5/order/realtime": "get_open_orders",
        "/v5/order/history": "get_order_history",
        "/v5/execution/list": "get_executions",
        "/v5/account/info": "get_account_info",
        "/v5/account/fee-rate": "get_fee_rates",
        "/v5/position/closed-pnl": "get_closed_pnl",
    }

    def __init__(
        self,
        session: Any,
        rate_limiter: AsyncRateLimiter | None = None,
    ) -> None:
        self._session = session
        self._rate_limiter = rate_limiter or AsyncRateLimiter()

    async def get(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        authenticated: bool,
    ) -> Mapping[str, Any]:
        del authenticated
        await self._rate_limiter.acquire()
        method_name = self._METHODS.get(endpoint)
        if method_name is None:
            raise ValueError(f"unsupported read-only endpoint: {endpoint}")
        method = getattr(self._session, method_name)
        response = await asyncio.to_thread(method, **dict(params))
        if not isinstance(response, Mapping):
            raise TypeError("Bybit transport response must be a mapping")
        return response
