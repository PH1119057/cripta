"""Independent authenticated Exchange account inventory for Lifecycle protection.

Only the read-only transport is reachable. No trading mutation API is imported.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from collections.abc import Mapping
from typing import Any

from .transport import LifecycleAccountReadTransport


def _rows(response: Mapping[str, Any], symbol: str) -> tuple[list[dict[str, Any]], str]:
    if response.get("retCode") != 0:
        raise RuntimeError(
            "Bybit account read rejected (code=" + str(response.get("retCode")) + ")"
        )
    result = response.get("result")
    if not isinstance(result, Mapping) or not isinstance(result.get("list"), list):
        raise RuntimeError("incomplete Bybit account result")
    rows = result["list"]
    if any(not isinstance(x, dict) or x.get("symbol") != symbol for x in rows):
        raise RuntimeError("unexpected Bybit account row")
    return rows, str(result.get("nextPageCursor") or "")


async def _all_rows(
    reader: LifecycleAccountReadTransport,
    *,
    path: str,
    symbol: str,
    extra: Mapping[str, str],
) -> list[dict[str, Any]]:
    cursor = ""
    seen: set[str] = set()
    gathered: list[dict[str, Any]] = []
    for _ in range(20):
        params = {"category": "linear", "symbol": symbol, **extra}
        if cursor:
            params["cursor"] = cursor
        payload = await reader.get(path, params, authenticated=True)
        rows, following = _rows(payload, symbol)
        gathered.extend(rows)
        if not following:
            return gathered
        if following in seen:
            raise RuntimeError("Bybit pagination cursor repeated")
        seen.add(following)
        cursor = following
    raise RuntimeError("Bybit account inventory pagination incomplete")


async def _symbol_snapshot(
    reader: LifecycleAccountReadTransport,
    symbol: str,
) -> tuple[str, dict[str, object]]:
    positions, orders, instrument = await asyncio.gather(
        _all_rows(reader, path="/v5/position/list", symbol=symbol, extra={}),
        _all_rows(
            reader, path="/v5/order/realtime", symbol=symbol,
            extra={"openOnly": "0", "limit": "50"},
        ),
        reader.get(
            "/v5/market/instruments-info",
            {"category": "linear", "symbol": symbol},
            authenticated=False,
        ),
    )
    instrument_rows, _ = _rows(instrument, symbol)
    if len(instrument_rows) != 1:
        raise RuntimeError("exact instrument metadata unavailable")
    tick = (instrument_rows[0].get("priceFilter") or {}).get("tickSize")
    if not tick:
        raise RuntimeError("instrument tickSize unavailable")
    return symbol, {
        "positions": positions,
        "orders": orders,
        "tick": str(tick),
    }


async def direct_account_snapshot(
    symbols: tuple[str, ...],
    *,
    api_key: str,
    api_secret: str,
    expected_user_id: str,
    base_url: str = "https://api.bybit.kz",
) -> dict[str, object]:
    if len(set(symbols)) != len(symbols):
        raise ValueError("repeated Exchange account audit scope")
    reader = LifecycleAccountReadTransport(
        api_key=api_key, api_secret=api_secret, base_url=base_url,
    )
    identity = await reader.get("/v5/user/query-api", {}, authenticated=True)
    if identity.get("retCode") != 0:
        raise RuntimeError("Bybit account identity request failed")
    details = identity.get("result")
    actual_user_id = (
        str(details.get("userID") or "").strip()
        if isinstance(details, Mapping) else ""
    )
    if not expected_user_id or not actual_user_id or actual_user_id != expected_user_id:
        raise RuntimeError("Bybit account identity does not match approved userID")
    results = await asyncio.gather(*(_symbol_snapshot(reader, s) for s in symbols))
    return {
        "source": "BYBIT_AUTHENTICATED_ACCOUNT_GET",
        "account_ref": "BYBIT:UNIFIED",
        "account_user_id": actual_user_id,
        "complete": True,
        "observed_at": datetime.now(UTC).isoformat(),
        "symbols": dict(results),
    }
