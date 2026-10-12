"""Pure fail-closed evaluation of fresh, independent Bybit protection evidence."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Mapping


def _decimal(value: object) -> Decimal:
    try:
        amount = Decimal(str(value or "0"))
        return amount if amount.is_finite() else Decimal(0)
    except (InvalidOperation, ValueError):
        return Decimal(0)


def evaluate_protection(
    evidence: Mapping[str, object] | None,
    *,
    position_id: str,
    symbol: str,
    position_idx: int,
    side: str,
    expected_tp: Decimal,
    expected_tp_kind: str,
    expected_stop: Decimal,
    tick: Decimal,
    now: datetime,
    max_age_seconds: float = 15,
) -> tuple[str, str]:
    """Return VERIFIED, NOT_VERIFIED or UNKNOWN; never infer account truth from DB."""
    if not isinstance(evidence, Mapping) or evidence.get("complete") is not True:
        return "UNKNOWN", "fresh authenticated account evidence unavailable"
    if evidence.get("account_ref") != "BYBIT:UNIFIED":
        return "UNKNOWN", "unexpected account identity"
    try:
        observed = datetime.fromisoformat(str(evidence["observed_at"])).astimezone(UTC)
    except (KeyError, TypeError, ValueError):
        return "UNKNOWN", "Exchange observation timestamp invalid"
    if observed.tzinfo is None or now.tzinfo is None:
        return "UNKNOWN", "Exchange observation clock lacks timezone"
    age = (now.astimezone(UTC) - observed).total_seconds()
    if age < -1 or age > max_age_seconds:
        return "UNKNOWN", "Exchange account observation is stale"
    symbol_data = evidence.get("symbols")
    if not isinstance(symbol_data, Mapping) or symbol not in symbol_data:
        return "UNKNOWN", "symbol absent from complete account audit scope"
    scope = symbol_data[symbol]
    if not isinstance(scope, Mapping):
        return "UNKNOWN", "invalid account evidence scope"
    positions = scope.get("positions")
    orders = scope.get("orders")
    if not isinstance(positions, list) or not isinstance(orders, list):
        return "UNKNOWN", "incomplete account position/order inventory"
    matches = [
        p for p in positions
        if isinstance(p, Mapping) and p.get("symbol") == symbol
        and int(str(p.get("positionIdx") if p.get("positionIdx") is not None else "-1")) == position_idx
        and _decimal(p.get("size")) > 0
    ]
    if len(matches) != 1:
        return "NOT_VERIFIED", "exact open Exchange position absent or ambiguous"
    pos = matches[0]
    if pos.get("side") != side:
        return "NOT_VERIFIED", "Exchange position direction mismatch"
    stop = _decimal(pos.get("stopLoss"))
    if tick <= 0 or expected_stop <= 0 or expected_tp <= 0:
        return "UNKNOWN", "approved protection price or tick is unavailable"
    if stop <= 0 or abs(stop - expected_stop) > tick:
        return "NOT_VERIFIED", "Exchange Hard Stop differs from current approved stop"
    if expected_tp_kind == "POSITION":
        tp = _decimal(pos.get("takeProfit"))
        if tp <= 0 or abs(tp - expected_tp) > tick:
            return "NOT_VERIFIED", "Exchange position Take Profit differs from approved TP"
    elif expected_tp_kind == "MAKER_ORDER":
        prefix = "utp" + hashlib.sha256(position_id.encode()).hexdigest()[:12] + "-"
        expected_side = "Sell" if side == "Buy" else "Buy"
        matches = [
            o for o in orders
            if isinstance(o, Mapping) and o.get("symbol") == symbol
            and str(o.get("orderLinkId") or "").startswith(prefix)
            and o.get("orderStatus") in {"New", "PartiallyFilled"}
            and str(o.get("side")) == expected_side
            and o.get("reduceOnly") is True
            and int(str(o.get("positionIdx") if o.get("positionIdx") is not None else "-1")) == position_idx
            and _decimal(o.get("leavesQty") or o.get("qty")) > 0
            and abs(_decimal(o.get("price")) - expected_tp) <= tick
        ]
        if len(matches) != 1:
            return "NOT_VERIFIED", "approved dynamic maker Take Profit order absent"
    else:
        return "UNKNOWN", "approved Take Profit mode unresolved"
    return "VERIFIED", "independent Exchange Hard Stop and Take Profit confirmed"
