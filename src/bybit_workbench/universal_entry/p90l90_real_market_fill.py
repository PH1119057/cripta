"""P90 REAL MARKET position identity readback, no synthetic Exchange fills.

An order ACK is not a fill. Never adopt a foreign/wrong-direction/hedge or
ambiguous Bybit position to rebase the owner's attached opening SL/TP.
A fast already-closed TP may have no open position: that is *unknown* here,
not permission to retry, fabricate PnL or issue a second reduce-only close.
"""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation


class P90RealMarketPositionUnverified(ValueError):
    """Signed inventory does not prove the intended newly opened position."""


def exact_open_market_position(
    rows: object,
    *,
    symbol: str,
    side: str,
) -> Mapping[str, object] | None:
    """Return exactly one current correct one-way position, or None if flat.

    The caller must independently bind the signed GET to the exact account,
    prove the Entry order/fill lineage and eventually read its executionIds.
    Wrong active position is never silently skipped.
    """
    if (
        not symbol or side not in {"Buy", "Sell"}
        or not isinstance(rows, list)
    ):
        raise P90RealMarketPositionUnverified(
            "P90_REAL_MARKET_POSITION_INVENTORY_INVALID"
        )
    active: list[Mapping[str, object]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise P90RealMarketPositionUnverified(
                "P90_REAL_MARKET_POSITION_ROW_INVALID"
            )
        try:
            quantity = Decimal(str(row.get("size") or "0"))
        except (ValueError, TypeError, InvalidOperation):
            raise P90RealMarketPositionUnverified(
                "P90_REAL_MARKET_POSITION_QTY_INVALID"
            ) from None
        if not quantity.is_finite() or quantity < 0:
            raise P90RealMarketPositionUnverified(
                "P90_REAL_MARKET_POSITION_QTY_INVALID"
            )
        if quantity == 0:
            continue
        if (
            row.get("symbol") != symbol
            or row.get("side") != side
            or str(row.get("positionIdx")) != "0"
        ):
            raise P90RealMarketPositionUnverified(
                "P90_REAL_MARKET_POSITION_FOREIGN_OR_WRONG_SIDE"
            )
        try:
            average = Decimal(str(row.get("avgPrice")))
        except (ValueError, TypeError, InvalidOperation):
            raise P90RealMarketPositionUnverified(
                "P90_REAL_MARKET_POSITION_AVG_INVALID"
            ) from None
        if not average.is_finite() or average <= 0:
            raise P90RealMarketPositionUnverified(
                "P90_REAL_MARKET_POSITION_AVG_INVALID"
            )
        active.append(row)
    if len(active) > 1:
        raise P90RealMarketPositionUnverified(
            "P90_REAL_MARKET_POSITION_AMBIGUOUS"
        )
    return active[0] if active else None


def exact_market_order_execution_ids(
    executions: object,
    *,
    exchange_order_id: str,
    entry_order_link_id: str,
    symbol: str,
    side: str,
    position_size: Decimal,
    position_avg: Decimal,
    tick_size: Decimal,
) -> tuple[str, ...]:
    """Prove a signed Bybit MARKET fill belongs to the exact Entry order.

    The signed REST inventory must be paginated to completion by the private
    account worker. A lagging response is UNKNOWN, not permission to fabricate
    a fill or retry an already acknowledged opening order.
    """
    if (
        not isinstance(executions, list) or not executions
        or not exchange_order_id or not entry_order_link_id
        or not symbol or side not in {"Buy", "Sell"}
        or any(
            not value.is_finite() or value <= 0
            for value in (position_size, position_avg, tick_size)
        )
    ):
        raise P90RealMarketPositionUnverified(
            "P90_REAL_MARKET_FILL_ORDER_OR_INVENTORY_INVALID"
        )
    identifiers: set[str] = set()
    quantity = Decimal("0")
    notional = Decimal("0")
    for execution in executions:
        if (
            not isinstance(execution, Mapping)
            or execution.get("orderId") != exchange_order_id
            or execution.get("orderLinkId") != entry_order_link_id
            or execution.get("symbol") != symbol
            or execution.get("side") != side
            or execution.get("orderType") != "Market"
            or execution.get("execType") != "Trade"
        ):
            raise P90RealMarketPositionUnverified(
                "P90_REAL_MARKET_FILL_WRONG_ORDER_OR_SIDE"
            )
        execution_id = execution.get("execId")
        if (
            not isinstance(execution_id, str) or not execution_id.strip()
            or execution_id in identifiers
        ):
            raise P90RealMarketPositionUnverified(
                "P90_REAL_MARKET_FILL_EXECUTION_ID_INVALID"
            )
        identifiers.add(execution_id)
        try:
            filled = Decimal(str(execution.get("execQty")))
            price = Decimal(str(execution.get("execPrice")))
            closed = Decimal(str(execution.get("closedSize") or "0"))
            at_ms = int(str(execution.get("execTime") or "0"))
        except (TypeError, ValueError, InvalidOperation):
            raise P90RealMarketPositionUnverified(
                "P90_REAL_MARKET_FILL_DETAILS_INVALID"
            ) from None
        if (
            not filled.is_finite() or not price.is_finite()
            or not closed.is_finite()
            or filled <= 0 or price <= 0 or closed != 0 or at_ms <= 0
        ):
            raise P90RealMarketPositionUnverified(
                "P90_REAL_MARKET_FILL_DETAILS_INVALID"
            )
        quantity += filled
        notional += filled * price
    if quantity != position_size or abs(notional / quantity - position_avg) > tick_size:
        raise P90RealMarketPositionUnverified(
            "P90_REAL_MARKET_FILL_EXECUTION_POSITION_MISMATCH"
        )
    return tuple(sorted(identifiers))
