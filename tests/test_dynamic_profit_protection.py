from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "operations" / "connectivity"))
from protection_math import (  # noqa: E402
    EXIT_TAKER_FEE_RATE,
    MIN_PROTECTED_PROFIT_USDT,
    calculate_protection_plan,
)


def test_near_protection_covers_fees_profit_and_one_tick_slippage() -> None:
    plan = calculate_protection_plan(
        entry=Decimal("1.893"), qty=Decimal("52.8"),
        entry_fee=Decimal("0.01999008"), side="Buy", tick=Decimal("0.001"),
    )

    assert plan["stop"] == Decimal("1.896")
    assert plan["activation"] == Decimal("1.897")
    assumed_fill = plan["stop"] - Decimal("0.001")
    gross = (assumed_fill - Decimal("1.893")) * Decimal("52.8")
    exit_fee = assumed_fill * Decimal("52.8") * EXIT_TAKER_FEE_RATE
    net = gross - plan["entry_fee"] - exit_fee
    assert net >= MIN_PROTECTED_PROFIT_USDT


def test_short_protection_is_mirrored() -> None:
    plan = calculate_protection_plan(
        entry=Decimal("10"), qty=Decimal("10"), entry_fee=Decimal("0.02"),
        side="Sell", tick=Decimal("0.001"),
    )
    assert plan["activation"] < plan["stop"] < Decimal("10")
