from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

ZERO = Decimal("0")


def number(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (ArithmeticError, ValueError):
        return ZERO


def payload(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("payload_json", {})
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


def entry_to_exit_move_pct(side: str, entry: Decimal, exit_price: Decimal) -> Decimal:
    if entry <= 0:
        return ZERO
    direction = Decimal("1") if side == "Buy" else Decimal("-1")
    return (exit_price / entry - Decimal("1")) * Decimal("100") * direction


def trigger_to_fill_slippage_pct(
    side: str, trigger: Decimal | None, fill: Decimal
) -> Decimal | None:
    if trigger is None or trigger <= 0:
        return None
    # Положительное значение означает ухудшение исполнения относительно триггера.
    if side == "Buy":
        return (trigger - fill) / trigger * Decimal("100")
    return (fill - trigger) / trigger * Decimal("100")


@dataclass(frozen=True)
class ExactClose:
    status: str
    link_method: str
    exit_order_id: str | None
    exit_order_ids: tuple[str, ...]
    exit_execution_ids: tuple[str, ...]
    actual_exit_qty: Decimal | None
    actual_exit_avg_fill: Decimal | None
    exit_fee_actual: Decimal | None
    gross_pnl: Decimal | None
    entry_to_exit_move_pct: Decimal | None
    reason: str


def resolve_exchange_position_close(
    *,
    side: str,
    actual_avg_fill: Decimal,
    actual_qty: Decimal,
    executions: Iterable[Mapping[str, Any]],
) -> ExactClose:
    """Resolve a close from exact exchange inventory, never nearest timestamp.

    The caller supplies executions inside one non-overlapping Bybit position
    identity interval. A result is exact only when the closing exchange
    executions sum to the complete recorded quantity. A close may legitimately
    consist of several partial orders. Ambiguous or partial evidence is unresolved.
    """
    expected_side = "Sell" if side == "Buy" else "Buy"
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in executions:
        body = payload(row)
        if str(row.get("side") or body.get("side") or "") != expected_side:
            continue
        if number(body.get("closedSize")) <= 0:
            continue
        order_id = str(row.get("order_id") or body.get("orderId") or "")
        exec_id = str(row.get("exec_id") or body.get("execId") or "")
        if not order_id or not exec_id:
            continue
        groups.setdefault(order_id, []).append(row)

    rows = [row for values in groups.values() for row in values]
    closed_qty = sum((number(row.get("exec_qty")) for row in rows), ZERO)
    if not rows or closed_qty != actual_qty:
        return ExactClose(
            "UNRESOLVED_EXACT_LINK",
            "NONE",
            None,
            (),
            (),
            None,
            None,
            None,
            None,
            None,
            "сумма точных закрывающих исполнений не равна количеству позиции",
        )

    order_ids = tuple(sorted(groups))
    order_id = str(
        max(
            rows,
            key=lambda row: (
                int(row.get("exec_time_ms") or 0),
                str(row.get("exec_id") or ""),
            ),
        ).get("order_id")
    )
    qty = sum((number(row.get("exec_qty")) for row in rows), ZERO)
    weighted = sum(
        (number(row.get("exec_qty")) * number(row.get("exec_price")) for row in rows),
        ZERO,
    )
    avg_exit = weighted / qty
    fee = sum((abs(number(row.get("exec_fee"))) for row in rows), ZERO)
    direction = Decimal("1") if side == "Buy" else Decimal("-1")
    gross = (avg_exit - actual_avg_fill) * qty * direction
    return ExactClose(
        "EXACT",
        "BYBIT_POSITION_INVENTORY_AND_EXCHANGE_ORDER_ID",
        order_id,
        order_ids,
        tuple(sorted(str(row.get("exec_id")) for row in rows)),
        qty,
        avg_exit,
        fee,
        gross,
        entry_to_exit_move_pct(side, actual_avg_fill, avg_exit),
        "полное количество позиции закрыто точными exchange execution/order ID",
    )


def classify_exit(
    *,
    exit_order_id: str,
    stop_order_type: str,
    create_type: str = "",
    protection_events: Iterable[Mapping[str, Any]],
    close_commands: Iterable[Mapping[str, Any]],
) -> tuple[str, str, str]:
    for event in protection_events:
        order_ids = event.get("exchange_order_ids") or []
        if isinstance(order_ids, str):
            order_ids = json.loads(order_ids or "[]")
        if exit_order_id not in {str(value) for value in order_ids}:
            continue
        kind = str(event.get("protection_kind") or "")
        initiator = str(event.get("initiator") or "UNKNOWN")
        owner = initiator if initiator in {"ALGORITHM", "OWNER", "TECHNICAL_SAFETY"} else "UNKNOWN"
        mechanism = {
            "INITIAL_HARD_STOP": "INITIAL_HARD_STOP",
            "PROFIT_PROTECTION_STOP": "PROFIT_PROTECTION_STOP",
            "TRAILING_STOP": "TRAILING_STOP",
            "TAKE_PROFIT": "TAKE_PROFIT",
        }.get(kind, "UNKNOWN")
        return owner, mechanism, "EXACT_PROTECTION_ORDER_ID"

    for command in close_commands:
        result = command.get("result_json") or {}
        if isinstance(result, str):
            result = json.loads(result or "{}")
        result = result.get("result") or result
        if str(result.get("orderId") or "") != exit_order_id:
            continue
        command_id = str(command.get("command_id") or "")
        owner = "OWNER" if command_id.startswith("web-") else "ALGORITHM"
        mechanism = "OWNER_MANUAL_CLOSE" if owner == "OWNER" else "STRATEGY_EXIT"
        return owner, mechanism, "EXACT_CLOSE_COMMAND_ORDER_ID"

    if create_type == "CreateByTrailingStop":
        return "EXCHANGE", "TRAILING_STOP", "EXACT_BYBIT_EXIT_ORDER_CREATE_TYPE"

    # Bybit StopLoss alone does not identify the business meaning of the stop.
    if stop_order_type:
        return "EXCHANGE", "UNKNOWN", "UNRESOLVED_PROTECTION_PROVENANCE"
    return "UNKNOWN", "UNKNOWN", "UNRESOLVED_EXIT_PROVENANCE"
