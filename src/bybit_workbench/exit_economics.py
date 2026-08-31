from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class CloseEconomics:
    gross_pnl_if_closed_now: Decimal
    entry_fee_actual: Decimal
    exit_fee_expected: Decimal
    funding_realized: Decimal | None
    slippage_reserve: Decimal
    expected_net_if_closed_now: Decimal
    calculated_net_break_even_price: Decimal
    exchange_break_even_price: Decimal | None
    minimum_profitable_close_price: Decimal
    minimum_net_profit: Decimal
    executable_close_price: Decimal
    data_quality: str
    exactness: str
    actual_net_without_funding: Decimal
    actual_net_pnl: Decimal | None
    net_completeness: str

    def audit_dict(self) -> dict[str, Any]:
        return {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in asdict(self).items()
        }


def calculate_close_economics(
    *,
    side: str,
    entry_price: Decimal,
    qty: Decimal,
    executable_close_price: Decimal,
    entry_fee_actual: Decimal,
    exit_fee_rate: Decimal,
    funding_realized: Decimal | None = None,
    slippage_reserve: Decimal = Decimal("0"),
    minimum_net_profit: Decimal = Decimal("0.01"),
    exchange_break_even_price: Decimal | None = None,
    data_quality: str = "FRESH",
    exactness: str = "EXPECTED_EXIT_FEE",
) -> CloseEconomics:
    values = (
        entry_price,
        qty,
        executable_close_price,
        entry_fee_actual,
        exit_fee_rate,
        slippage_reserve,
        minimum_net_profit,
    )
    invalid_price = entry_price <= 0 or qty <= 0 or executable_close_price <= 0
    if invalid_price or any(value < 0 for value in values[3:]):
        raise ValueError("цены и объём должны быть положительными, расходы — неотрицательными")
    direction = Decimal("1") if side == "Buy" else Decimal("-1") if side == "Sell" else None
    if direction is None:
        raise ValueError("side должен быть Buy или Sell")
    gross = (executable_close_price - entry_price) * qty * direction
    exit_fee = executable_close_price * qty * exit_fee_rate
    net_without_funding = gross - entry_fee_actual - exit_fee - slippage_reserve
    funding_effect = funding_realized if funding_realized is not None else Decimal("0")
    net = net_without_funding + funding_effect
    fixed = entry_fee_actual - funding_effect + slippage_reserve
    if side == "Buy":
        net_be = (entry_price * qty + fixed) / (qty * (Decimal("1") - exit_fee_rate))
        min_profitable = (entry_price * qty + fixed + minimum_net_profit) / (
            qty * (Decimal("1") - exit_fee_rate)
        )
    else:
        net_be = (entry_price * qty - fixed) / (qty * (Decimal("1") + exit_fee_rate))
        min_profitable = (entry_price * qty - fixed - minimum_net_profit) / (
            qty * (Decimal("1") + exit_fee_rate)
        )
    return CloseEconomics(
        gross,
        entry_fee_actual,
        exit_fee,
        funding_realized,
        slippage_reserve,
        net,
        net_be,
        exchange_break_even_price,
        min_profitable,
        minimum_net_profit,
        executable_close_price,
        data_quality,
        exactness,
        net_without_funding,
        net if funding_realized is not None else None,
        "COMPLETE" if funding_realized is not None else "PARTIAL_NO_FUNDING",
    )


def guaranteed_profit_allowed(economics: CloseEconomics) -> bool:
    return (
        economics.data_quality == "FRESH"
        and economics.expected_net_if_closed_now >= economics.minimum_net_profit
    )
