"""Immutable interpretation of post-mutation Bybit protection evidence.

Acknowledging an order command is not proof of the resulting TP/SL state.
This module is pure and cannot send Exchange mutations or alter a trading gate.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class EffectiveProtectionEvidence:
    command_id: str
    symbol: str
    position_idx: int
    account_ref: str
    observed_at: datetime
    state: str
    stop_loss: Decimal | None
    position_take_profit: Decimal | None
    maker_take_profit_order_ids: tuple[str, ...]
    reason: str

    def as_record(self) -> dict[str, object]:
        return {
            "command_id": self.command_id,
            "symbol": self.symbol,
            "position_idx": self.position_idx,
            "account_ref": self.account_ref,
            "observed_at": self.observed_at.astimezone(UTC).isoformat(),
            "state": self.state,
            "stop_loss": str(self.stop_loss) if self.stop_loss is not None else None,
            "position_take_profit": (
                str(self.position_take_profit)
                if self.position_take_profit is not None else None
            ),
            "maker_take_profit_order_ids": list(self.maker_take_profit_order_ids),
            "reason": self.reason,
            "source": "BYBIT_AUTHENTICATED_ACCOUNT_GET",
        }


def _positive(value: object) -> Decimal | None:
    try:
        v = Decimal(str(value or "0"))
        return v if v.is_finite() and v > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def effective_protection_from_account(
    *,
    command_id: str,
    symbol: str,
    position_idx: int,
    account_ref: str,
    account_evidence: Mapping[str, object] | None,
    observed_at: datetime,
    strategy_position_id: str | None = None,
) -> EffectiveProtectionEvidence:
    """Interpret complete signed-read inventory, never a mutation ACK.

    OBSERVED_PRESENT means SL and a TP surface appear in fresh account data.
    It never proves equivalence with the current approved ExitPlan.
    The separate approval comparator must establish exact economic equivalence.
    """
    expected_maker_prefix = (
        "utp" + hashlib.sha256(strategy_position_id.encode()).hexdigest()[:12] + "-"
        if strategy_position_id else None
    )
    reason = "independent Exchange account read incomplete"
    state = "UNKNOWN"
    stop = None
    tp = None
    maker: tuple[str, ...] = ()
    if (
        observed_at.tzinfo is not None
        and isinstance(account_evidence, Mapping)
        and account_evidence.get("complete") is True
        and account_evidence.get("account_ref") == account_ref
    ):
        scopes = account_evidence.get("symbols")
        scope = scopes.get(symbol) if isinstance(scopes, Mapping) else None
        if isinstance(scope, Mapping):
            rows = scope.get("positions")
            orders = scope.get("orders")
            if isinstance(rows, list) and isinstance(orders, list):
                matches = [
                    p for p in rows
                    if isinstance(p, Mapping)
                    and p.get("symbol") == symbol
                    and str(p.get("positionIdx")) == str(position_idx)
                    and (_positive(p.get("size")) is not None)
                ]
                if len(matches) == 1:
                    pos = matches[0]
                    stop = _positive(pos.get("stopLoss"))
                    tp = _positive(pos.get("takeProfit"))
                    maker = tuple(sorted(str(order.get("orderId")) for order in orders
                        if isinstance(order, Mapping)
                        and order.get("symbol") == symbol
                        and str(order.get("positionIdx")) == str(position_idx)
                        and order.get("reduceOnly") is True
                        and order.get("orderStatus") in {"New", "PartiallyFilled"}
                        and expected_maker_prefix is not None
                        and str(order.get("orderLinkId") or "").startswith(expected_maker_prefix)
                        and _positive(order.get("price")) is not None
                        and _positive(order.get("leavesQty") or order.get("qty")) is not None
                        and order.get("orderId")
                    ))
                    state = (
                        "OBSERVED_PRESENT"
                        if stop is not None and (tp is not None or maker)
                        else "NOT_VERIFIED"
                    )
                    reason = (
                        "Exchange protection surfaces present; approval comparison pending"
                        if state == "OBSERVED_PRESENT" else
                        "Exchange position lacks effective Hard Stop or Take Profit"
                    )
                elif not matches:
                    state, reason = "UNKNOWN", "exact open position not in account snapshot"
                else:
                    state, reason = "NOT_VERIFIED", "ambiguous duplicate exact Exchange positions"
    return EffectiveProtectionEvidence(
        command_id=command_id, symbol=symbol, position_idx=position_idx,
        account_ref=account_ref, observed_at=observed_at,
        state=state, stop_loss=stop, position_take_profit=tp,
        maker_take_profit_order_ids=maker, reason=reason,
    )
