import hashlib
from datetime import UTC, datetime

from bybit_workbench.exchange.bybit.effective_protection import effective_protection_from_account


NOW = datetime(2026, 10, 12, tzinfo=UTC)


def _build(payload):
    return effective_protection_from_account(
        command_id="command-1", symbol="APTUSDT",
        position_idx=0, account_ref="BYBIT:UNIFIED",
        account_evidence=payload, observed_at=NOW,
        strategy_position_id="pos-test-1",
    )


def _snapshot(*, stop="90", tp="110", orders=None):
    return {
        "complete": True,
        "account_ref": "BYBIT:UNIFIED",
        "symbols": {
            "APTUSDT": {
                "positions": [{
                    "symbol": "APTUSDT", "positionIdx": 0,
                    "size": "1", "side": "Buy",
                    "stopLoss": stop, "takeProfit": tp,
                }],
                "orders": orders or [],
            },
        },
    }


def test_exchange_confirmation_requires_full_account_snapshot():
    assert _build(None).state == "UNKNOWN"
    assert _build({"retCode": 0, "result": {"orderId": "accepted"}}).state == "UNKNOWN"
    assert _build({"complete": True, "account_ref": "WRONG"}).state == "UNKNOWN"
    assert _build(_snapshot()).state == "OBSERVED_PRESENT"


def test_missing_sl_or_tp_does_not_mean_approved():
    assert _build(_snapshot(stop="0")).state == "NOT_VERIFIED"
    assert _build(_snapshot(tp="0")).state == "NOT_VERIFIED"


def test_dynamic_maker_tp_is_recognized_as_separate_surface():
    maker = {
        "symbol": "APTUSDT", "positionIdx": 0,
        "orderStatus": "New", "reduceOnly": True,
        "orderLinkId": "utp" + hashlib.sha256(b"pos-test-1").hexdigest()[:12] + "-0123", "orderId": "maker123",
        "price": "115", "leavesQty": "1",
    }
    result = _build(_snapshot(tp="0", orders=[maker]))
    assert result.state == "OBSERVED_PRESENT"
    assert result.maker_take_profit_order_ids == ("maker123",)
    assert _build(_snapshot(tp="0", orders=[{**maker, "reduceOnly": False}])).state == "NOT_VERIFIED"


def test_evidence_contains_exact_command_identity():
    record = _build(_snapshot()).as_record()
    assert record["command_id"] == "command-1"
    assert record["account_ref"] == "BYBIT:UNIFIED"
    assert record["source"] == "BYBIT_AUTHENTICATED_ACCOUNT_GET"
    assert record["stop_loss"] == "90"
