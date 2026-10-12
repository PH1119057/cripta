from decimal import Decimal
import pytest

from bybit_workbench.exchange.bybit.protection_intent import (
    ProtectionIntentUnavailable,
    expected_protection,
)


def _base(commands=None):
    return expected_protection(
        position_id="position-A",
        entry_command_id="entry-A",
        side="Buy",
        entry_price=Decimal("100"),
        tick=Decimal("0.01"),
        entry_payload={"initial_protection": {
            "stop_loss_pct": "10", "take_profit_price": "110",
        }},
        completed_commands=commands or [],
    )


def test_initial_protection_contract():
    assert _base() == (Decimal("90"), Decimal("110"), "POSITION")


def test_direct_stop_uses_exact_current_price():
    assert _base([{
        "command_type": "strategy_exit",
        "payload": {"strategy_position_id": "position-A",
                    "action_kind": "SET_STOP",
                    "requested_mutation": {"stop_price": "95"}},
        "result": {"retCode": 0},
    }])[0] == Decimal("95")


def test_market_set_tp_is_not_resting_tp():
    with pytest.raises(ProtectionIntentUnavailable, match="marketable"):
        _base([{
            "command_type": "strategy_exit",
            "payload": {"strategy_position_id": "position-A",
                        "action_kind": "SET_TP",
                        "requested_mutation": {
                            "take_profit_price": "105", "order_type": "MARKET"}},
            "result": {"retCode": 0},
        }])


def test_market_fallback_makes_limit_tp_unknown():
    with pytest.raises(ProtectionIntentUnavailable, match="market"):
        _base([{
            "command_type": "strategy_exit",
            "payload": {"strategy_position_id": "position-A",
                        "action_kind": "SET_TP",
                        "requested_mutation": {
                            "take_profit_price": "105", "order_type": "LIMIT",
                            "marketable_action": "CLOSE_MARKET"}},
            "result": {"retCode": 0},
        }])


def test_unattributed_break_even_does_not_update_other_position():
    with pytest.raises(ProtectionIntentUnavailable, match="exact StrategyPosition"):
        _base([{
            "command_type": "break_even",
            "payload": {"symbol": "APTUSDT"},
            "result": {"protectionPlan": {"stop": "100.1"}},
        }])
