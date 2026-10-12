"""Expected approved protection prices from exact Entry/Exit intent lineage.

These are policy/command inputs, not Exchange truth. They are compared with
independent account GET evidence by the Lifecycle Supervisor.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Mapping


class ProtectionIntentUnavailable(ValueError):
    pass


def _amount(value: object) -> Decimal:
    try:
        amount = Decimal(str(value))
    except Exception as exc:
        raise ProtectionIntentUnavailable("approved numeric protection value unavailable") from exc
    if not amount.is_finite() or amount <= 0:
        raise ProtectionIntentUnavailable("approved numeric protection value invalid")
    return amount


def _round(value: Decimal, tick: Decimal, *, upward: bool) -> Decimal:
    return (value / tick).to_integral_value(
        rounding=ROUND_CEILING if upward else ROUND_FLOOR
    ) * tick


def expected_protection(
    *,
    position_id: str,
    entry_command_id: str,
    side: str,
    entry_price: object,
    tick: object,
    entry_payload: Mapping[str, object],
    completed_commands: list[Mapping[str, object]],
) -> tuple[Decimal, Decimal, str]:
    """Return current approved (SL,TP,TP-kind), or refuse to declare VERIFIED."""
    step = _amount(tick)
    entry = _amount(entry_price)
    if side not in {"Buy", "Sell"}:
        raise ProtectionIntentUnavailable("approved side unavailable")
    initial = entry_payload.get("initial_protection")
    if not isinstance(initial, Mapping):
        raise ProtectionIntentUnavailable("initial protection contract missing")
    stop_pct = _amount(initial.get("stop_loss_pct")) / Decimal("100")
    initial_stop = entry * (Decimal(1) - stop_pct if side == "Buy" else Decimal(1) + stop_pct)
    stop = _round(initial_stop, step, upward=side == "Buy")
    raw_tp = initial.get("take_profit_price")
    if raw_tp is None:
        pct = _amount(initial.get("take_profit_pct")) / Decimal("100")
        raw_tp = entry * (Decimal(1) + pct if side == "Buy" else Decimal(1) - pct)
    target = _round(_amount(raw_tp), step, upward=side == "Buy")
    tp_kind = "POSITION"

    for command in completed_commands:
        kind = str(command.get("command_type") or "")
        payload = command.get("payload")
        result = command.get("result")
        if not isinstance(payload, Mapping):
            raise ProtectionIntentUnavailable("command intent unavailable")
        if not isinstance(result, Mapping):
            raise ProtectionIntentUnavailable("command acknowledgement unavailable")
        if kind == "strategy_exit":
            if payload.get("strategy_position_id") != position_id:
                continue
            action = str(payload.get("action_kind") or "")
            mutation = payload.get("requested_mutation")
            if not isinstance(mutation, Mapping):
                raise ProtectionIntentUnavailable("Exit mutation contract missing")
            if action == "SET_TP":
                requested = _amount(mutation.get("take_profit_price"))
                target = _round(requested, step, upward=side == "Sell")
                order_type = str(mutation.get("order_type") or "").upper()
                if order_type not in {"MARKET", "LIMIT"}:
                    raise ProtectionIntentUnavailable("Exit TP order type unresolved")
                if order_type == "MARKET":
                    raise ProtectionIntentUnavailable(
                        "marketable SET_TP may close position; fresh reconciliation required"
                    )
                if str(mutation.get("marketable_action") or "") == "CLOSE_MARKET":
                    raise ProtectionIntentUnavailable(
                        "maker SET_TP may have executed market close; fresh order lineage required"
                    )
                tp_kind = "MAKER_ORDER"
            elif action == "SET_STOP":
                stop = _amount(mutation.get("stop_price"))
                if stop % step != 0:
                    raise ProtectionIntentUnavailable("approved stop violates tick")
            elif action == "SET_PROTECTION":
                raise ProtectionIntentUnavailable("combined protection update requires exact verification")
        elif kind == "initial_protection":
            if payload.get("entry_command_id") != entry_command_id:
                continue
            actual = result.get("actualProtection")
            if isinstance(actual, Mapping):
                stop = _amount(actual.get("stopLoss"))
                if actual.get("takeProfit") not in (None, ""):
                    target = _amount(actual["takeProfit"])
                    tp_kind = "POSITION"
        elif kind == "break_even":
            if payload.get("strategy_position_id") != position_id:
                raise ProtectionIntentUnavailable(
                    "break-even command cannot be attributed to exact StrategyPosition"
                )
            plan = result.get("protectionPlan")
            if not isinstance(plan, Mapping):
                raise ProtectionIntentUnavailable("break-even effective stop lacks durable price")
            stop = _amount(plan.get("stop"))
        elif kind == "current_stop":
            raise ProtectionIntentUnavailable("current-stop effective price lacks durable approved result")

    return stop, target, tp_kind
