import asyncio
from collections.abc import Mapping
from typing import Any, Protocol

from bybit_workbench.domain.types import AppMode

from .rate_limit import AsyncRateLimiter


class BybitWriteTransport(Protocol):
    async def post(
        self,
        endpoint: str,
        params: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class PybitTestnetWriteTransport:
    """Narrow write router that cannot be constructed for Demo or Live."""

    _METHODS = {
        "/v5/order/create": "place_order",
        "/v5/order/cancel": "cancel_order",
        "/v5/position/trading-stop": "set_trading_stop",
    }

    def __init__(
        self,
        session: Any,
        mode: AppMode,
        rate_limiter: AsyncRateLimiter | None = None,
    ) -> None:
        if mode is not AppMode.TESTNET:
            raise PermissionError("write transport is restricted to Testnet")
        self._session = session
        self._rate_limiter = rate_limiter or AsyncRateLimiter()

    async def post(
        self,
        endpoint: str,
        params: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        method_name = self._METHODS.get(endpoint)
        if method_name is None:
            raise ValueError(f"unsupported Testnet write endpoint: {endpoint}")
        method = getattr(self._session, method_name)
        await self._rate_limiter.acquire()
        return await asyncio.to_thread(method, **dict(params))


class _PybitMainnetWriteTransport:
    """Raw private delegate; callers must only receive it wrapped by MainnetMutationGateway."""

    _METHODS = {
        "/v5/order/create": "place_order",
        "/v5/order/cancel": "cancel_order",
        "/v5/position/trading-stop": "set_trading_stop",
    }

    def __init__(
        self,
        session: Any,
        mode: AppMode,
        rate_limiter: AsyncRateLimiter | None = None,
    ) -> None:
        if mode is not AppMode.LIVE:
            raise PermissionError("Mainnet raw delegate requires the Mainnet profile")
        self._session = session
        self._rate_limiter = rate_limiter or AsyncRateLimiter()

    async def post(
        self,
        endpoint: str,
        params: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        method_name = self._METHODS.get(endpoint)
        if method_name is None:
            raise ValueError(f"unsupported Mainnet write endpoint: {endpoint}")
        method = getattr(self._session, method_name)
        await self._rate_limiter.acquire()
        response = await asyncio.to_thread(method, **dict(params))
        if not isinstance(response, Mapping):
            raise TypeError("Bybit write response must be a mapping")
        return response
