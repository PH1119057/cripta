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


class LifecycleAccountReadTransport:
    """Authenticated GET-only REST for the Lifecycle protection auditor.

    Only position and open-order inventory are reachable. No POST, cancel,
    amend, close or other Exchange mutation method exists in this class.
    """

    _ALLOWED = frozenset({"/v5/position/list", "/v5/order/realtime"})

    def __init__(self, *, api_key: str, api_secret: str,
                 base_url: str = "https://api.bybit.kz",
                 timeout_seconds: float = 5.0) -> None:
        if not api_key or not api_secret or timeout_seconds <= 0:
            raise ValueError("invalid account-read configuration")
        self._key = api_key
        self._secret = api_secret
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._limiter = AsyncRateLimiter()

    async def get(self, endpoint: str, params: Mapping[str, Any],
                  *, authenticated: bool) -> Mapping[str, Any]:
        if endpoint not in self._ALLOWED or not authenticated:
            raise ValueError("Lifecycle read transport endpoint is not permitted")
        await self._limiter.acquire()
        return await asyncio.to_thread(self._get_blocking, endpoint, params)

    def _get_blocking(self, endpoint: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        import hashlib
        import hmac
        import json
        import time
        import urllib.parse
        import urllib.request

        query = urllib.parse.urlencode(sorted((str(k), str(v)) for k, v in params.items()))
        timestamp = str(int(time.time() * 1000))
        window = "5000"
        signature = hmac.new(
            self._secret.encode(),
            f"{timestamp}{self._key}{window}{query}".encode(),
            hashlib.sha256,
        ).hexdigest()
        request = urllib.request.Request(
            f"{self._base_url}{endpoint}?{query}",
            headers={
                "X-BAPI-API-KEY": self._key,
                "X-BAPI-TIMESTAMP": timestamp,
                "X-BAPI-RECV-WINDOW": window,
                "X-BAPI-SIGN": signature,
                "User-Agent": "cripta-lifecycle-readonly/1",
            },
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, Mapping):
            raise TypeError("malformed Bybit account-read response")
        return payload
