from decimal import Decimal

from bybit_workbench.exit_economics import calculate_close_economics, guaranteed_profit_allowed


def test_profit_label_is_blocked_when_expected_net_is_negative() -> None:
    result = calculate_close_economics(
        side="Buy", entry_price=Decimal("100"), qty=Decimal("1"),
        executable_close_price=Decimal("100.05"), entry_fee_actual=Decimal("0.02"),
        exit_fee_rate=Decimal("0.00055"), slippage_reserve=Decimal("0.02"),
    )
    assert result.gross_pnl_if_closed_now == Decimal("0.05")
    assert result.expected_net_if_closed_now < 0
    assert not guaranteed_profit_allowed(result)


def test_fee_aware_profit_is_side_symmetric() -> None:
    common = dict(qty=Decimal("2"), entry_fee_actual=Decimal("0.04"),
                  exit_fee_rate=Decimal("0.00055"), slippage_reserve=Decimal("0.02"))
    long = calculate_close_economics(side="Buy", entry_price=Decimal("100"),
                                     executable_close_price=Decimal("101"), **common)
    short = calculate_close_economics(side="Sell", entry_price=Decimal("100"),
                                      executable_close_price=Decimal("99"), **common)
    assert guaranteed_profit_allowed(long)
    assert guaranteed_profit_allowed(short)
    assert long.expected_net_if_closed_now > 0
    assert short.expected_net_if_closed_now > 0
