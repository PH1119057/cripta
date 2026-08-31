from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from bybit_workbench.exit_economics import calculate_close_economics

EXIT_TAKER_FEE_RATE = Decimal("0.00055")
MIN_PROTECTED_PROFIT_USDT = Decimal("0.01")
PROTECTION_SLIPPAGE_PCT = Decimal("0.0002")


def quantize(value: Decimal, step: Decimal, *, upward: bool) -> Decimal:
    rounding = ROUND_CEILING if upward else ROUND_FLOOR
    return (value / step).to_integral_value(rounding=rounding) * step


def calculate_protection_plan(
    *,
    entry: Decimal,
    qty: Decimal,
    entry_fee: Decimal,
    side: str,
    tick: Decimal,
    slippage_pct: Decimal = PROTECTION_SLIPPAGE_PCT,
) -> dict[str, Decimal]:
    if entry <= 0 or qty <= 0 or tick <= 0:
        raise ValueError("entry, quantity and tick must be positive")
    slippage = max(tick, entry * slippage_pct)
    economics = calculate_close_economics(
        side=side,
        entry_price=entry,
        qty=qty,
        executable_close_price=entry,
        entry_fee_actual=entry_fee,
        exit_fee_rate=EXIT_TAKER_FEE_RATE,
        slippage_reserve=slippage * qty,
        minimum_net_profit=MIN_PROTECTED_PROFIT_USDT,
    )
    minimum_fill = economics.minimum_profitable_close_price
    raw_stop = minimum_fill
    stop = quantize(raw_stop, tick, upward=side == "Buy")
    activation = stop + tick if side == "Buy" else stop - tick
    return {
        "stop": stop,
        "activation": activation,
        "entry_fee": entry_fee,
        "minimum_fill": minimum_fill,
        "slippage": slippage,
    }


def trailing_start_preserves_protection(
    *, side: str, mark: Decimal, distance: Decimal, protected_stop: Decimal
) -> bool:
    """Return whether an immediately active trailing stop starts beyond the protected stop."""
    if mark <= 0 or distance <= 0:
        return False
    if side == "Buy":
        return mark - distance >= protected_stop
    if side == "Sell":
        return mark + distance <= protected_stop
    raise ValueError("side must be Buy or Sell")


def calculate_initial_boundaries(
    *, entry: Decimal, side: str, tick: Decimal, take_profit_pct: Decimal = Decimal("3.00")
) -> tuple[Decimal, Decimal]:
    if entry <= 0 or tick <= 0:
        raise ValueError("entry and tick must be positive")
    target_move = take_profit_pct / Decimal("100")
    if side == "Buy":
        return (
            quantize(entry * Decimal("0.99"), tick, upward=True),
            quantize(entry * (Decimal("1") + target_move), tick, upward=True),
        )
    if side == "Sell":
        return (
            quantize(entry * Decimal("1.01"), tick, upward=False),
            quantize(entry * (Decimal("1") - target_move), tick, upward=False),
        )
    raise ValueError("side must be Buy or Sell")
