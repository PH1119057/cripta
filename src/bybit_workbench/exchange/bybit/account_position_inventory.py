"""Authenticated, paginated account-wide Bybit LINEAR position observation.

Read-only evidence.  Exhausted pagination does not prove an atomic Exchange
snapshot, Strategy ownership, credential-to-TradingAccount binding or a safe
mutation target. Never use this alone to authorize trading-stop.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol


class PositionReadTransport(Protocol):
    async def get(
        self, endpoint: str, params: Mapping[str, Any], *, authenticated: bool
    ) -> Mapping[str, Any]: ...


async def complete_linear_positions(
    reader: PositionReadTransport,
    *,
    max_pages: int = 100,
) -> list[dict[str, Any]]:
    """Collect all USDT-settled linear position pages; refuse partial input."""
    if max_pages <= 0 or max_pages > 100:
        raise ValueError("position page limit outside approved bounded reader")
    seen_cursors: set[str] = set()
    seen_keys: set[tuple[str, int]] = set()
    cursor = ""
    gathered: list[dict[str, Any]] = []
    for _ in range(max_pages):
        params = {"category": "linear", "settleCoin": "USDT", "limit": "200"}
        if cursor:
            params["cursor"] = cursor
        payload = await reader.get("/v5/position/list", params, authenticated=True)
        if type(payload.get("retCode")) is not int or payload["retCode"] != 0:
            raise RuntimeError("authenticated account-wide position GET rejected")
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("account-wide position result unavailable")
        rows = result.get("list")
        if not isinstance(rows, list):
            raise RuntimeError("account-wide position list unavailable")
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError("account-wide position row is not an object")
            symbol = row.get("symbol")
            index = row.get("positionIdx")
            if not isinstance(symbol, str) or not symbol.strip():
                raise RuntimeError("account-wide position symbol unavailable")
            if type(index) is not int or index not in (0, 1, 2):
                raise RuntimeError("account-wide positionIdx invalid")
            try:
                size = Decimal(str(row.get("size")))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise RuntimeError("account-wide position size invalid") from exc
            if not size.is_finite() or size < 0:
                raise RuntimeError("account-wide position size nonfinite/negative")
            key = (symbol, index)
            if key in seen_keys:
                raise RuntimeError("duplicate account-wide physical position key")
            seen_keys.add(key)
            gathered.append(row)
        next_cursor = result.get("nextPageCursor")
        if next_cursor in (None, ""):
            return gathered
        if not isinstance(next_cursor, str) or next_cursor in seen_cursors or next_cursor == cursor:
            raise RuntimeError("account-wide position pagination cursor invalid/repeated")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise RuntimeError("account-wide position pagination incomplete")
