from __future__ import annotations

import hashlib
from collections.abc import Iterable
from decimal import Decimal
from typing import NamedTuple


class FillSummary(NamedTuple):
    first_execution_id: str
    execution_ids: tuple[str, ...]
    actual_qty: Decimal
    actual_avg_fill: Decimal


def stable_cycle_ids(
    *,
    entry_command_id: str,
    first_execution_id: str,
    symbol: str,
    side: str,
    position_idx: int,
) -> tuple[str, str]:
    """Return immutable IDs for one exchange position cycle.

    Mutable fill quantity, VWAP and later execution IDs are deliberately excluded.
    """
    parts = (
        entry_command_id.strip(),
        first_execution_id.strip(),
        symbol.strip().upper(),
        side.strip(),
        str(int(position_idx)),
    )
    if not all(parts):
        raise ValueError(
            "stable position-cycle identity requires exact non-empty identifiers"
        )
    identity = "|".join(parts)
    position_id = "POS-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    trade_id = "TRD-" + hashlib.sha256(
        ("trade|" + identity).encode("utf-8")
    ).hexdigest()[:32]
    return position_id, trade_id


def summarize_entry_fills(
    fills: Iterable[tuple[str, Decimal, Decimal]],
) -> FillSummary:
    """Return deterministic cumulative quantity/VWAP for ordered entry fills."""
    rows = tuple(fills)
    if not rows:
        raise ValueError("at least one exact entry execution is required")
    execution_ids: list[str] = []
    qty = Decimal("0")
    notional = Decimal("0")
    for exec_id, fill_qty, fill_price in rows:
        exact_id = exec_id.strip()
        if not exact_id or fill_qty <= 0 or fill_price <= 0:
            raise ValueError("entry fills require exact ID and positive qty/price")
        execution_ids.append(exact_id)
        qty += fill_qty
        notional += fill_qty * fill_price
    return FillSummary(
        first_execution_id=execution_ids[0],
        execution_ids=tuple(execution_ids),
        actual_qty=qty,
        actual_avg_fill=notional / qty,
    )
