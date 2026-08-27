from __future__ import annotations

from datetime import UTC, datetime

from bybit_workbench.app.config import MAINNET_REST_URLS

from .errors import BybitApiError, BybitClockSkewError, BybitErrorCategory, BybitModeMismatch
from .models import ApiKeyInfo, MainnetConnectionTestReport
from .rest import BybitReadOnlyAdapter


class MainnetConnectionTester:
    """Ordered, GET-only Mainnet diagnostic. It has no write transport dependency."""

    def __init__(
        self,
        adapter: BybitReadOnlyAdapter,
        endpoint: str,
        *,
        max_clock_offset_ms: int = 750,
    ) -> None:
        if not endpoint.startswith("https://"):
            raise ValueError("Mainnet endpoint must use https://")
        if max_clock_offset_ms < 0:
            raise ValueError("max_clock_offset_ms cannot be negative")
        self.adapter = adapter
        self.endpoint = endpoint.rstrip("/")
        self.max_clock_offset_ms = max_clock_offset_ms

    async def run(
        self,
        symbol: str,
        *,
        local_time: datetime | None = None,
    ) -> MainnetConnectionTestReport:
        if not symbol.strip():
            raise ValueError("symbol is required")
        if local_time is not None:
            local = local_time.astimezone(UTC)
            server_time = await self.adapter.server_time()
        else:
            local_before = datetime.now(UTC)
            server_time = await self.adapter.server_time()
            local_after = datetime.now(UTC)
            local = local_before + (local_after - local_before) / 2
        offset = int((server_time - local).total_seconds() * 1_000)
        if abs(offset) > self.max_clock_offset_ms:
            raise BybitClockSkewError(offset, self.max_clock_offset_ms)
        # Core authenticated state is more important for read-only operation than
        # the API-key metadata endpoint.  The Kazakhstan regional endpoint has
        # occasionally returned an HTTP 400 for /v5/user/query-api while wallet,
        # positions and orders remain available.  In that case keep read-only
        # market/account synchronization alive but fail closed for execution
        # arming until key metadata becomes available again.
        wallet = await self.adapter.wallet_snapshot()
        position = await self.adapter.position_snapshot(symbol.upper())
        orders = await self.adapter.open_orders(symbol.upper())
        key_info: ApiKeyInfo | None = None
        key_step = "api_key_info"
        try:
            key_info = await self.adapter.api_key_info()
        except BybitApiError as exc:
            if exc.category is BybitErrorCategory.AUTHENTICATION:
                known = "preset" if self.endpoint in MAINNET_REST_URLS else "custom"
                raise BybitModeMismatch(
                    f"API key was rejected by {known} endpoint {self.endpoint}; "
                    "check Mainnet/environment/domain selection (no fallback was attempted)"
                ) from exc
            if not _api_key_metadata_failure_is_nonfatal(exc):
                raise
            key_step = "api_key_info_unavailable"
        except Exception as exc:
            if not _api_key_metadata_failure_is_nonfatal(exc):
                raise
            key_step = "api_key_info_unavailable"
        return MainnetConnectionTestReport(
            endpoint=self.endpoint,
            server_time=server_time,
            clock_offset_ms=offset,
            api_key=key_info,
            wallet=wallet,
            position=position,
            open_order_count=len(orders),
            completed_steps=(
                "server_time",
                "wallet_balance",
                "positions",
                "open_orders",
                key_step,
            ),
        )


def _api_key_metadata_failure_is_nonfatal(exc: Exception) -> bool:
    if isinstance(exc, BybitApiError):
        return exc.category in {
            BybitErrorCategory.RATE_LIMIT,
            BybitErrorCategory.TRANSIENT,
            BybitErrorCategory.REQUEST,
            BybitErrorCategory.UNKNOWN,
        }
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "bad request",
            "errcode: 400",
            "retries exceeded maximum",
            "too many requests",
            "errcode: 429",
        )
    )
