"""Expected approved protection prices from exact Entry/Exit intent lineage.

These are policy/command inputs, not Exchange truth. They are compared with
independent account GET evidence by the Lifecycle Supervisor.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal


class ProtectionIntentUnavailable(ValueError):
    pass


def initial_protection_surfaces_observed(
    *,
    initial_protection: Mapping[str, object] | None,
    position: Mapping[str, object],
) -> bool:
    """Conservative entry-time WS evidence marker, NOT approval verification.

    A position must expose every exchange-native initial protection surface
    required by the exact Entry payload. Final VERIFIED status still requires
    the separate authenticated account/approved-intent comparator.
    """
    if not isinstance(initial_protection, Mapping):
        return False
    if initial_protection.get("stop_loss_enabled") is not True:
        return False
    tp_required = initial_protection.get("take_profit_enabled")
    if not isinstance(tp_required, bool):
        return False

    def present(value: object) -> bool:
        try:
            amount = Decimal(str(value))
            return amount.is_finite() and amount > 0
        except (ValueError, TypeError):
            return False

    return present(position.get("stopLoss")) and (
        not tp_required or present(position.get("takeProfit"))
    )


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
                    # Execution SET_TP/MARKET installs a conditional position
                    # TP via /v5/position/trading-stop (tpOrderType=Market).
                    # This is NOT the market-close fallback of LIMIT SET_TP.
                    tp_kind = "POSITION"
                else:
                    # LIMIT SET_TP may instead issue a market-close fallback.
                    # Only a tagged maker order ACK proves a resting TP.
                    accepted_maker_tp_order_id(
                        position_id=position_id, completed_commands=[command],
                    )
                    tp_kind = "MAKER_ORDER"
            elif action == "SET_STOP":
                stop = _amount(mutation.get("stop_price"))
                if stop % step != 0:
                    raise ProtectionIntentUnavailable("approved stop violates tick")
            elif action == "SET_PROTECTION":
                raise ProtectionIntentUnavailable(
                    "combined protection update requires exact verification"
                )
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
            raise ProtectionIntentUnavailable(
                "current-stop effective price lacks durable approved result"
            )
        elif kind == "trailing_stop":
            raise ProtectionIntentUnavailable(
                "trailing-stop effective price lacks durable approved result"
            )

    return stop, target, tp_kind


def accepted_maker_tp_order_id(
    *,
    position_id: str,
    completed_commands: list[Mapping[str, object]],
) -> str:
    """Prove latest SET_TP ACK identifies a maker order for the exact position.

    Market-close results may also contain an orderId, but not the unique
    position-derived maker orderLinkId. Those are never treated as resting TP.
    """
    import hashlib

    prefix = "utp" + hashlib.sha256(position_id.encode()).hexdigest()[:12] + "-"
    latest: str | None = None
    for command in completed_commands:
        if command.get("command_type") != "strategy_exit":
            continue
        payload = command.get("payload")
        result = command.get("result")
        if not isinstance(payload, Mapping) or payload.get("strategy_position_id") != position_id:
            continue
        if payload.get("action_kind") != "SET_TP":
            continue
        if not isinstance(result, Mapping) or result.get("retCode") != 0:
            raise ProtectionIntentUnavailable("latest TP command ACK not confirmed")
        mutation = payload.get("requested_mutation")
        if not isinstance(mutation, Mapping):
            raise ProtectionIntentUnavailable("TP mutation unavailable")
        if str(mutation.get("order_type") or "").upper() != "LIMIT":
            raise ProtectionIntentUnavailable("latest TP is not a maker limit order")
        detail = result.get("result")
        if not isinstance(detail, Mapping):
            raise ProtectionIntentUnavailable("latest TP Exchange order identity missing")
        order_id = str(detail.get("orderId") or "").strip()
        link_id = str(detail.get("orderLinkId") or "").strip()
        if not order_id or not link_id.startswith(prefix):
            raise ProtectionIntentUnavailable("latest TP ACK not exact maker position order")
        latest = order_id
    if latest is None:
        raise ProtectionIntentUnavailable("no approved completed maker TP order")
    return latest


def verify_approved_exchange_protection(
    *,
    account_evidence: Mapping[str, object] | None,
    position_id: str,
    entry_command_id: str,
    symbol: str,
    position_idx: int,
    side: str,
    entry_price: object,
    tick: object,
    entry_payload: Mapping[str, object],
    completed_commands: list[Mapping[str, object]],
    now: datetime,
) -> tuple[str, str]:
    """One fail-closed comparison of approved intent and fresh Exchange truth.

    Never claims VERIFIED from command ACK alone: live exact Bybit account
    positions and orders are mandatory. All unsupported or ambiguous lineage
    produces UNKNOWN, not an assumed protection price.
    """
    from .protection_evidence import evaluate_protection

    try:
        stop, target, kind = expected_protection(
            position_id=position_id,
            entry_command_id=entry_command_id,
            side=side,
            entry_price=entry_price,
            tick=tick,
            entry_payload=entry_payload,
            completed_commands=completed_commands,
        )
        maker_id = (
            accepted_maker_tp_order_id(
                position_id=position_id,
                completed_commands=completed_commands,
            )
            if kind == "MAKER_ORDER" else None
        )
        return evaluate_protection(
            account_evidence,
            position_id=position_id,
            symbol=symbol,
            position_idx=position_idx,
            side=side,
            expected_stop=stop,
            expected_tp=target,
            expected_tp_kind=kind,
            expected_tp_order_id=maker_id,
            tick=_amount(tick),
            now=now,
        )
    except (ProtectionIntentUnavailable, TypeError, ValueError, KeyError) as exc:
        return "UNKNOWN", "approved Exchange protection lineage incomplete: " + type(exc).__name__
