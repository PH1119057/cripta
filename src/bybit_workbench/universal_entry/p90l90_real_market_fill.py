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
