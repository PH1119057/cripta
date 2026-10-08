"""Pure Exchange evidence proof for an orphan historical Entry cycle.

Does not alter exchange state, account gates or durable position ownership.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ClosedCycleProof:
    entry_order_id: str
    symbol: str
    side: str
    quantity: Decimal
    entry_vwap: Decimal
    exit_vwap: Decimal
    entry_fee: Decimal
    exit_fee: Decimal
    gross_pnl: Decimal
    net_without_funding: Decimal
    entry_execution_ids: tuple[str, ...]
    exit_execution_ids: tuple[str, ...]
    exit_order_ids: tuple[str, ...]
    closed_ms: int


def _decimal(value: object) -> Decimal:
    result = Decimal(str(value or "0"))
    if not result.is_finite():
        raise InvalidOperation("non-finite Exchange amount")
    return result


def prove_orphan_closed_cycle(
    *,
    entry_order: Mapping[str, object],
    entry_executions: Sequence[Mapping[str, object]],
    subsequent_executions: Sequence[Mapping[str, object]],
    terminal_orders: Mapping[str, Mapping[str, object]],
) -> ClosedCycleProof | None:
    """Require one fully filled Entry and exact, uninterrupted opposite fills.

    A later reopen is permitted once the original quantity has reached zero.
    Same-direction scale-ins, overshoots, missing executions and missing
    terminal order evidence remain unresolved instead of being guessed.
    """
    try:
        order_id = str(entry_order.get("orderId") or "")
        symbol = str(entry_order.get("symbol") or "")
        side = str(entry_order.get("side") or "")
        filled_qty = _decimal(entry_order.get("cumExecQty"))
        if (
            not order_id or not symbol or side not in {"Buy", "Sell"}
            or entry_order.get("orderStatus") != "Filled" or filled_qty <= 0
            or not entry_executions
        ):
            return None
        seen: set[str] = set()
        entry_qty = Decimal(0)
        entry_value = Decimal(0)
        entry_fee = Decimal(0)
        entry_ids: list[str] = []
        last_entry_ms = 0
        for row in entry_executions:
            exec_id = str(row.get("execId") or "")
            if (
                not exec_id or exec_id in seen
                or str(row.get("orderId") or "") != order_id
                or str(row.get("symbol") or "") != symbol
                or str(row.get("side") or "") != side
                or str(row.get("execType") or "Trade") != "Trade"
            ):
                return None
            seen.add(exec_id)
            qty = _decimal(row.get("execQty"))
            price = _decimal(row.get("execPrice"))
            if qty <= 0 or price <= 0:
                return None
            entry_qty += qty
            entry_value += qty * price
            entry_fee += abs(_decimal(row.get("execFee")))
            entry_ids.append(exec_id)
            last_entry_ms = max(last_entry_ms, int(row.get("execTime") or 0))
        if entry_qty != filled_qty or last_entry_ms <= 0:
            return None
        remaining = entry_qty
        exit_value = Decimal(0)
        exit_fee = Decimal(0)
        exit_ids: list[str] = []
        exit_orders: list[str] = []
        closed_ms = 0
        opposite = "Sell" if side == "Buy" else "Buy"
        for row in sorted(subsequent_executions, key=lambda x: (int(x.get("execTime") or 0), str(x.get("execId") or ""))):
            execution_time = int(row.get("execTime") or 0)
            if execution_time <= last_entry_ms:
                continue
            if str(row.get("symbol") or "") != symbol:
                continue
            if str(row.get("execType") or "Trade") != "Trade":
                continue
            exec_id = str(row.get("execId") or "")
            order = str(row.get("orderId") or "")
            if not exec_id or exec_id in seen or not order:
                return None
            seen.add(exec_id)
            if str(row.get("side") or "") != opposite:
                return None
            qty = _decimal(row.get("execQty"))
            price = _decimal(row.get("execPrice"))
            if qty <= 0 or price <= 0 or qty > remaining:
                return None
            history = terminal_orders.get(order)
            if (
                not history or str(history.get("orderStatus") or "") != "Filled"
                or str(history.get("symbol") or "") != symbol
                or str(history.get("side") or "") != opposite
            ):
                return None
            remaining -= qty
            exit_value += qty * price
            exit_fee += abs(_decimal(row.get("execFee")))
            exit_ids.append(exec_id)
            if order not in exit_orders:
                exit_orders.append(order)
            closed_ms = execution_time
            if remaining == 0:
                break
        if remaining != 0 or not exit_ids:
            return None
        gross = (
            exit_value - entry_value if side == "Buy"
            else entry_value - exit_value
        )
        return ClosedCycleProof(
            entry_order_id=order_id, symbol=symbol, side=side,
            quantity=entry_qty, entry_vwap=entry_value / entry_qty,
            exit_vwap=exit_value / entry_qty, entry_fee=entry_fee,
            exit_fee=exit_fee, gross_pnl=gross,
            net_without_funding=gross - entry_fee - exit_fee,
            entry_execution_ids=tuple(entry_ids),
            exit_execution_ids=tuple(exit_ids),
            exit_order_ids=tuple(exit_orders),
            closed_ms=closed_ms,
        )
    except (InvalidOperation, ValueError, TypeError, ArithmeticError):
        return None
