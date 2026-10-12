import hashlib
from decimal import Decimal

import pytest

from bybit_workbench.exchange.bybit.protection_intent import (
    ProtectionIntentUnavailable,
    accepted_maker_tp_order_id,
    expected_protection,
    verify_approved_exchange_protection,
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
    with pytest.raises(ProtectionIntentUnavailable, match="identity missing"):
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


def test_exact_maker_order_identity_and_market_fallback():
    prefix = "utp" + hashlib.sha256(b"position-A").hexdigest()[:12] + "-"
    payload = {
        "strategy_position_id": "position-A", "action_kind": "SET_TP",
        "requested_mutation": {
            "take_profit_price": "115", "order_type": "LIMIT",
            "marketable_action": "CLOSE_MARKET",
        },
    }
    maker = {"command_type": "strategy_exit", "payload": payload, "result": {
        "retCode": 0, "result": {
            "orderId": "maker-order-123", "orderLinkId": prefix + "abcd",
        },
    }}
    assert accepted_maker_tp_order_id(
        position_id="position-A", completed_commands=[maker],
    ) == "maker-order-123"
    fallback = {**maker, "result": {"retCode": 0, "result": {
        "orderId": "market-close-123", "orderLinkId": "close-request",
    }}}
    with pytest.raises(ProtectionIntentUnavailable, match="not exact maker"):
        accepted_maker_tp_order_id(
            position_id="position-A", completed_commands=[fallback],
        )
    with pytest.raises(ProtectionIntentUnavailable, match="not exact maker"):
        accepted_maker_tp_order_id(
            position_id="position-A", completed_commands=[maker, fallback],
        )


def test_idempotent_maker_tp_response_preserves_exchange_identity():
    prefix = "utp" + hashlib.sha256(b"position-A").hexdigest()[:12] + "-"
    command = {
        "command_type": "strategy_exit",
        "payload": {"strategy_position_id": "position-A",
                    "action_kind": "SET_TP",
                    "requested_mutation": {"order_type": "LIMIT"}},
        "result": {"retCode": 0, "idempotent": True,
                   "result": {"orderId": "original-123",
                              "orderLinkId": prefix + "old"}},
    }
    assert accepted_maker_tp_order_id(
        position_id="position-A", completed_commands=[command],
    ) == "original-123"


def test_full_approved_protection_verdict_requires_live_exact_maker_order():
    from datetime import UTC, datetime
    now = datetime(2026, 10, 12, tzinfo=UTC)
    prefix = "utp" + hashlib.sha256(b"position-A").hexdigest()[:12] + "-"
    command = {
        "command_type": "strategy_exit",
        "payload": {"strategy_position_id": "position-A",
                    "action_kind": "SET_TP",
                    "requested_mutation": {"order_type": "LIMIT",
                                            "marketable_action": "CLOSE_MARKET",
                                            "take_profit_price": "115"}},
        "result": {"retCode": 0, "result": {
            "orderId": "maker-1", "orderLinkId": prefix + "1"}},
    }
    snapshot = {
        "complete": True, "account_ref": "BYBIT:UNIFIED",
        "observed_at": now.isoformat(),
        "symbols": {"APTUSDT": {
            "positions": [{"symbol": "APTUSDT", "side": "Buy",
                           "size": "1", "positionIdx": 0,
                           "stopLoss": "90", "takeProfit": "0"}],
            "orders": [{"symbol": "APTUSDT", "side": "Sell",
                        "positionIdx": 0, "orderLinkId": prefix + "1",
                        "orderId": "maker-1", "reduceOnly": True,
                        "orderStatus": "New", "leavesQty": "1", "price": "115"}],
        }},
    }
    args = dict(
        account_evidence=snapshot, position_id="position-A",
        entry_command_id="entry-A", symbol="APTUSDT", position_idx=0,
        side="Buy", entry_price="100", tick="0.01",
        entry_payload={"initial_protection": {
            "stop_loss_pct": "10", "take_profit_price": "110"}},
        completed_commands=[command], now=now,
    )
    assert verify_approved_exchange_protection(**args)[0] == "VERIFIED"
    wrong_order = {**snapshot, "symbols": {"APTUSDT": {
        **snapshot["symbols"]["APTUSDT"],
        "orders": [{**snapshot["symbols"]["APTUSDT"]["orders"][0],
                    "orderId": "maker-OTHER"}],
    }}}
    assert verify_approved_exchange_protection(
        **{**args, "account_evidence": wrong_order}
    )[0] == "NOT_VERIFIED"
    assert verify_approved_exchange_protection(
        **{**args, "account_evidence": None}
    )[0] == "UNKNOWN"
