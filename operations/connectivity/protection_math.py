from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR


EXIT_TAKER_FEE_RATE = Decimal("0.00055")
MIN_PROTECTED_PROFIT_USDT = Decimal("0.01")
PROTECTION_SLIPPAGE_PCT = Decimal("0.0002")


def quantize(value: Decimal, step: Decimal, *, upward: bool) -> Decimal:
    rounding = ROUND_CEILING if upward else ROUND_FLOOR
    return (value / step).to_integral_value(rounding=rounding) * step


def calculate_protection_plan(
    *, entry: Decimal, qty: Decimal, entry_fee: Decimal, side: str, tick: Decimal
) -> dict[str, Decimal]:
    if entry <= 0 or qty <= 0 or tick <= 0:
        raise ValueError("entry, quantity and tick must be positive")
    per_unit_cost = (entry_fee + MIN_PROTECTED_PROFIT_USDT) / qty
    if side == "Buy":
        minimum_fill = (entry + per_unit_cost) / (Decimal("1") - EXIT_TAKER_FEE_RATE)
    elif side == "Sell":
        minimum_fill = (entry - per_unit_cost) / (Decimal("1") + EXIT_TAKER_FEE_RATE)
    else:
        raise ValueError("side must be Buy or Sell")
    slippage = max(tick, entry * PROTECTION_SLIPPAGE_PCT)
    raw_stop = minimum_fill + slippage if side == "Buy" else minimum_fill - slippage
    stop = quantize(raw_stop, tick, upward=side == "Buy")
    activation = stop + tick if side == "Buy" else stop - tick
    return {"stop": stop, "activation": activation, "entry_fee": entry_fee, "minimum_fill": minimum_fill, "slippage": slippage}
