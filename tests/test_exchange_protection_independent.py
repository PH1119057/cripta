from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.exchange.bybit.protection_evidence import evaluate_protection
from bybit_workbench.exchange.bybit.protection_intent import expected_protection


NOW = datetime(2026, 10, 12, tzinfo=UTC)


def _evidence(*, stop="90", tp="110", orders=None):
    return {
        "complete": True, "account_ref": "BYBIT:UNIFIED",
        "observed_at": NOW.isoformat(),
        "symbols": {"APTUSDT": {
            "positions": [{
                "symbol": "APTUSDT", "positionIdx": 0, "side": "Buy",
                "size": "1", "stopLoss": stop, "takeProfit": tp,
            }],
            "orders": orders or [],
        }},
    }


def _check(evidence, *, kind="POSITION", tp=Decimal("110")):
    return evaluate_protection(
        evidence, position_id="pos-test-1", symbol="APTUSDT",
        position_idx=0, side="Buy", expected_stop=Decimal("90"),
        expected_tp=tp, expected_tp_kind=kind,
        tick=Decimal("0.01"), now=NOW,
    )[0]


def test_exchange_protection_confirmed_only_from_account_truth():
    assert _check(_evidence()) == "VERIFIED"
    assert _check(_evidence(stop="0")) == "NOT_VERIFIED"
    assert _check(_evidence(tp="0")) == "NOT_VERIFIED"
    assert _check(_evidence(stop="85")) == "NOT_VERIFIED"
    assert _check(None) == "UNKNOWN"
    assert _check({**_evidence(), "complete": False}) == "UNKNOWN"


def test_stale_exchange_protection_is_unknown():
    observed = _evidence()
    observed["observed_at"] = (NOW - timedelta(seconds=30)).isoformat()
    assert _check(observed) == "UNKNOWN"
    observed["observed_at"] = "2026-10-12T00:00:00"
    assert _check(observed) == "UNKNOWN"


def test_dynamic_maker_tp_requires_exact_order():
    import hashlib
    prefix = "utp" + hashlib.sha256(b"pos-test-1").hexdigest()[:12] + "-"
    order = {
        "symbol": "APTUSDT", "positionIdx": 0, "side": "Sell",
        "orderLinkId": prefix + "abcdefgh", "orderStatus": "New",
        "reduceOnly": True, "leavesQty": "1", "price": "115",
    }
    assert _check(_evidence(tp="0", orders=[order]), kind="MAKER_ORDER", tp=Decimal("115")) == "VERIFIED"
    assert _check(_evidence(tp="0"), kind="MAKER_ORDER", tp=Decimal("115")) == "NOT_VERIFIED"
    assert _check(_evidence(tp="0", orders=[{**order, "reduceOnly": False}]), kind="MAKER_ORDER", tp=Decimal("115")) == "NOT_VERIFIED"
    assert _check(_evidence(tp="0", orders=[{**order, "price": "113"}]), kind="MAKER_ORDER", tp=Decimal("115")) == "NOT_VERIFIED"


def test_approved_dynamic_exit_supersedes_initial_tp_and_stop():
    initial = {"initial_protection": {
        "stop_loss_pct": "10", "take_profit_price": "110",
    }}
    commands = [
        {"command_type": "strategy_exit", "payload": {
            "strategy_position_id": "pos-test-1", "action_kind": "SET_TP",
            "requested_mutation": {"take_profit_price": "115", "order_type": "LIMIT"},
        }, "result": {"retCode": 0}},
        {"command_type": "strategy_exit", "payload": {
            "strategy_position_id": "pos-test-1", "action_kind": "SET_STOP",
            "requested_mutation": {"stop_price": "95"},
        }, "result": {"retCode": 0}},
    ]
    sl, tp, kind = expected_protection(
        position_id="pos-test-1", entry_command_id="entry-1",
        side="Buy", entry_price="100", tick="0.01",
        entry_payload=initial, completed_commands=commands,
    )
    assert (sl, tp, kind) == (Decimal("95"), Decimal("115"), "MAKER_ORDER")


def test_signed_lifecycle_transport_has_no_exchange_mutation_endpoint():
    import asyncio
    from bybit_workbench.exchange.bybit.transport import LifecycleAccountReadTransport
    reader = LifecycleAccountReadTransport(api_key="test", api_secret="test")
    try:
        asyncio.run(reader.get("/v5/order/create", {}, authenticated=True))
    except ValueError:
        pass
    else:
        raise AssertionError("Lifecycle must never expose mutation endpoints")
