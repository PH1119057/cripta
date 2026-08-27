from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parents[1] / "operations" / "connectivity"))
from protection_math import (  # noqa: E402
    EXIT_TAKER_FEE_RATE,
    MIN_PROTECTED_PROFIT_USDT,
    calculate_initial_boundaries,
    calculate_protection_plan,
    trailing_start_preserves_protection,
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


def test_trailing_stop_cannot_start_below_protected_profit_for_long() -> None:
    assert not trailing_start_preserves_protection(
        side="Buy", mark=Decimal("0.09024"), distance=Decimal("0.00019"),
        protected_stop=Decimal("0.09023"),
    )
    assert trailing_start_preserves_protection(
        side="Buy", mark=Decimal("0.09043"), distance=Decimal("0.00020"),
        protected_stop=Decimal("0.09023"),
    )


def test_xrp_initial_boundaries_use_actual_fill_not_limit_price() -> None:
    stop, target = calculate_initial_boundaries(
        entry=Decimal("1.4563"), side="Sell", tick=Decimal("0.0001")
    )
    assert stop == Decimal("1.4708")
    assert target == Decimal("1.4402")
