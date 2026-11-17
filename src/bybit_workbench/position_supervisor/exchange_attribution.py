"""Strict display-only attribution of current Bybit exposure.

This predicate does not create Strategy ownership. It is applied only to a
unique, currently reconciled OPEN/OPEN ownership row. Actual current position
data comes from the complete signed Bybit account generation; all other
positions remain account-visible but not Strategy-owned.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def _positive_decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result > 0 else None


def matches_reconciled_strategy_cycle(
    *,
    owners: list[tuple[object, ...]],
    expected_strategy: str,
    current_quantity: object,
    current_avg_fill: object,
) -> bool:
    """Require a unique exact current-cycle owner, never symbol-only ownership."""
    if len(owners) != 1:
        return False
    owner = owners[0]
    # Positional schema is the exact dashboard SELECT. Owner row was filtered
    # by account, symbol, position_idx, side, StrategyVersion, identity PROVEN,
    # state OPEN and close_link_status OPEN.
    if len(owner) < 7 or str(owner[4]) != expected_strategy:
        return False
    quantity = _positive_decimal(current_quantity)
    avg_fill = _positive_decimal(current_avg_fill)
    expected_quantity = _positive_decimal(owner[6])
    expected_avg_fill = _positive_decimal(owner[5])
    return (
        quantity is not None
        and avg_fill is not None
        and quantity == expected_quantity
        and avg_fill == expected_avg_fill
    )
