"""F01 exact cycle proof: real current state is not a same-symbol heuristic."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from bybit_workbench.entry_cycle_recovery import (
    RecoveryCycleUnproven,
    RecoveryInventory,
    SignedRecoveryInventoryReader,
    prove_current_entry_cycle,
)


def case(*, remaining="1.25", symbol="DOTUSDT", account="MAIN", idx=0):
    position = {"symbol": symbol, "positionIdx": idx, "side": "Buy", "size": remaining,
                "avgPrice": "125", "seq": "20", "updatedTime": "2000",
                "stopLoss": "0", "takeProfit": "130", "trailingStop": "0"}
    order = {"orderId": "entry-order", "orderLinkId": "entry", "symbol": symbol,
             "side": "Buy", "positionIdx": idx, "cumExecQty": "1.25", "avgPrice": "125",
             "orderStatus": "Filled"}
    fill = {"execId": "fill", "orderId": "entry-order", "orderLinkId": "entry",
            "symbol": symbol, "side": "Buy", "execQty": "1.25", "execPrice": "125",
            "closedSize": "0", "execTime": "1000", "seq": "10", "execType": "Trade"}
    local = [{"exec_id": "fill", "order_id": "entry-order", "order_link_id": "entry",
              "exec_time_ms": 1000, "side": "Buy", "exec_qty": "1.25", "exec_price": "125"}]
    inventory = RecoveryInventory(account, "connector-" + account, 999, 2500, 3000,
                                  (position,), (fill,), {"entry-order": order})
    return dict(inventory=inventory, account=account, connector="connector-" + account,
                entry_command_id="entry", symbol=symbol, local_entry_executions=local,
                position=position, now_ms=3000)


def add_close(args, *, qty=".5", at="1500", seq="15"):
    inv = args["inventory"]
    order = {"orderId": "close-order", "orderLinkId": "exit", "symbol": args["symbol"],
             "side": "Sell", "positionIdx": args["position"]["positionIdx"]}
    fill = {"execId": "close-fill", "orderId": "close-order", "orderLinkId": "exit",
            "symbol": args["symbol"], "side": "Sell", "execQty": qty, "execPrice": "130",
            "closedSize": qty, "execTime": at, "seq": seq, "execType": "Trade"}
    args["inventory"] = replace(inv, executions=(*inv.executions, fill),
                                orders={**inv.orders, "close-order": order})


@pytest.mark.parametrize("account", ["MAIN", "SUB"])
@pytest.mark.parametrize("idx", [0, 1])
def test_exact_full_and_partially_closed_cycle(account, idx):
    args = case(account=account, idx=idx)
    assert prove_current_entry_cycle(**args).remaining_qty == Decimal("1.25")
    args = case(account=account, idx=idx, remaining=".75")
    add_close(args)
    proof = prove_current_entry_cycle(**args)
    assert proof.filled_qty == Decimal("1.25")
    assert proof.remaining_qty == Decimal(".75")
    assert proof.entry_execution_ids == ("fill",)
    assert proof.close_execution_ids == ("close-fill",)
    assert args["local_entry_executions"][0]["exec_qty"] == "1.25"


def test_partial_entry_order_uses_cumulative_fills_not_requested_order_qty():
    args = case()
    args["inventory"].orders["entry-order"].update(
        orderStatus="PartiallyFilled", qty="5", leavesQty="3.75",
    )
    assert prove_current_entry_cycle(**args).filled_qty == Decimal("1.25")


def test_identical_price_side_quantity_cannot_hide_closed_and_foreign_reopen():
    args = case()
    add_close(args, qty="1.25")
    inv = args["inventory"]
    foreign = {**inv.executions[0], "execId": "manual-open", "orderId": "manual-order",
               "orderLinkId": "manual", "execTime": "1800", "seq": "18"}
    args["inventory"] = replace(inv, executions=(*inv.executions, foreign), orders={
        **inv.orders, "manual-order": {**inv.orders["entry-order"], "orderId": "manual-order",
                                       "orderLinkId": "manual"},
    })
    with pytest.raises(RecoveryCycleUnproven, match="ALREADY_CLOSED"):
        prove_current_entry_cycle(**args)


@pytest.mark.parametrize("bad", ["side", "excess_qty", "account", "connector", "stale",
                                   "missing_id", "foreign_id", "price", "gap", "version"])
def test_contradictory_or_missing_evidence_is_not_a_position_owner(bad):
    args = case()
    if bad == "side":
        args["position"]["side"] = "Sell"
    elif bad == "excess_qty":
        args["position"]["size"] = "9"
    elif bad in {"account", "connector"}:
        args[bad] = "FOREIGN"
    elif bad == "stale":
        args["now_ms"] = 19000
    elif bad == "missing_id":
        args["inventory"] = replace(args["inventory"], executions=())
    elif bad == "foreign_id":
        args["local_entry_executions"][0]["order_id"] = "foreign"
    elif bad == "price":
        args["local_entry_executions"][0]["exec_price"] = "9"
    elif bad == "gap":
        args["inventory"] = replace(args["inventory"], start_ms=1001)
    else:
        args["position"].pop("seq")
    with pytest.raises(RecoveryCycleUnproven):
        prove_current_entry_cycle(**args)


def test_split_entry_fills_and_duplicate_history_are_idempotent():
    args = case()
    inv = args["inventory"]
    a = {**inv.executions[0], "execQty": ".5"}
    b = {**a, "execId": "fill-2", "execQty": ".75", "execTime": "1100", "seq": "11"}
    local = args["local_entry_executions"][0]
    args["local_entry_executions"] = [{**local, "exec_qty": ".5"},
        {**local, "exec_id": "fill-2", "exec_qty": ".75", "exec_time_ms": 1100}]
    args["inventory"] = replace(inv, executions=(a, b, dict(b)))
    proof = prove_current_entry_cycle(**args)
    assert proof.filled_qty == Decimal("1.25")
    assert set(proof.entry_execution_ids) == {"fill", "fill-2"}


def test_sixty_positions_on_independent_accounts_do_not_share_provenance():
    for number in range(60):
        args = case(symbol=f"COIN{number}USDT", account="MAIN" if number % 2 else "SUB",
                    remaining=".75")
        add_close(args)
        proof = prove_current_entry_cycle(**args)
        assert proof.certificate()["account"] == args["account"]
        assert proof.remaining_qty == Decimal(".75")


@pytest.mark.parametrize("bad", [None, "cursor", "drift", "partial_page", "reject"])
def test_signed_reader_requires_all_pages_and_stable_position_versions(bad):
    args = case()
    inv = args["inventory"]
    calls = []
    positions = [deepcopy(args["position"])]
    def monotonic():
        return 0

    def get(endpoint, params):
        calls.append((endpoint, params))
        if bad == "reject":
            return {"retCode": 10001}
        if endpoint == "/v5/position/list":
            if bad == "drift" and sum(e == endpoint for e, _ in calls) == 2:
                positions[0]["seq"] = "21"
            return {"retCode": 0, "result": {"list": deepcopy(positions)}}
        rows = (list(inv.executions) if endpoint == "/v5/execution/list"
                else list(inv.orders.values()))
        if bad == "partial_page":
            return {"retCode": 0, "result": {}}
        if bad == "cursor":
            return {"retCode": 0, "result": {"list": rows, "nextPageCursor": "again"}}
        if endpoint == "/v5/execution/list" and "cursor" not in params:
            return {"retCode": 0, "result": {"list": [], "nextPageCursor": "page2"}}
        return {"retCode": 0, "result": {"list": rows}}

    reader = SignedRecoveryInventoryReader(get, account="MAIN", connector="connector-MAIN",
                                            clock_ms=lambda: 3000, monotonic=monotonic)
    if bad is not None:
        with pytest.raises(RecoveryCycleUnproven):
            reader.read(999)
    else:
        read = reader.read(999)
        assert len(read.executions) == 1
        assert any(p.get("cursor") == "page2" for _, p in calls)
        assert {p["startTime"] for e, p in calls if e == "/v5/execution/list"} == {"999"}


def test_partially_filled_live_order_is_read_by_exact_id_when_history_has_not_arrived():
    args = case()
    inv = args["inventory"]
    calls = []

    def get(endpoint, params):
        calls.append((endpoint, params))
        rows = []
        if endpoint == "/v5/position/list":
            rows = list(inv.positions)
        elif endpoint == "/v5/execution/list":
            rows = list(inv.executions)
        elif endpoint == "/v5/order/realtime":
            assert params["orderId"] == "entry-order"
            rows = [{**inv.orders["entry-order"], "orderStatus": "PartiallyFilled", "qty": "5"}]
        return {"retCode": 0, "result": {"list": rows}}

    reader = SignedRecoveryInventoryReader(get, account="MAIN", connector="connector-MAIN",
                                            clock_ms=lambda: 3000)
    args["inventory"] = reader.read(999)
    assert prove_current_entry_cycle(**args).filled_qty == Decimal("1.25")
    assert any(e == "/v5/order/realtime" for e, _ in calls)
