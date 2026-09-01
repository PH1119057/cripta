from decimal import Decimal

from operations.connectivity.exact_close import (
    classify_exit,
    resolve_exchange_position_close,
    trigger_to_fill_slippage_pct,
)


def execution(exec_id: str, order_id: str, qty: str, price: str, side: str = "Sell") -> dict:
    return {
        "exec_id": exec_id,
        "order_id": order_id,
        "side": side,
        "exec_qty": qty,
        "exec_price": price,
        "exec_fee": "0.01",
        "exec_time_ms": int(exec_id.lstrip("e") or 0),
        "payload_json": {"closedSize": qty, "stopOrderType": "StopLoss"},
    }


def test_complete_exchange_order_closes_position_exactly() -> None:
    result = resolve_exchange_position_close(
        side="Buy",
        actual_avg_fill=Decimal("100"),
        actual_qty=Decimal("2"),
        executions=[execution("e1", "x1", "0.5", "99"), execution("e2", "x1", "1.5", "98")],
    )
    assert result.status == "EXACT"
    assert result.exit_order_id == "x1"
    assert result.exit_order_ids == ("x1",)
    assert result.actual_exit_qty == Decimal("2.0")
    assert result.actual_exit_avg_fill == Decimal("98.25")
    assert result.entry_to_exit_move_pct == Decimal("-1.7500")


def test_ambiguous_or_partial_close_stays_unresolved() -> None:
    result = resolve_exchange_position_close(
        side="Buy",
        actual_avg_fill=Decimal("100"),
        actual_qty=Decimal("2"),
        executions=[execution("e1", "x1", "1", "99")],
    )
    assert result.status == "UNRESOLVED_EXACT_LINK"
    assert result.exit_order_id is None


def test_multiple_partial_exchange_orders_can_exactly_close_one_position() -> None:
    result = resolve_exchange_position_close(
        side="Buy",
        actual_avg_fill=Decimal("100"),
        actual_qty=Decimal("2"),
        executions=[
            execution("e1", "partial-a", "0.5", "99"),
            execution("e2", "partial-b", "1.5", "98"),
        ],
    )
    assert result.status == "EXACT"
    assert result.exit_order_id == "partial-b"
    assert result.exit_order_ids == ("partial-a", "partial-b")
    assert result.exit_execution_ids == ("e1", "e2")


def test_profit_protection_is_not_initial_hard_stop() -> None:
    owner, mechanism, method = classify_exit(
        exit_order_id="stop-profit",
        stop_order_type="StopLoss",
        protection_events=[{
            "exchange_order_ids": ["stop-profit"],
            "protection_kind": "PROFIT_PROTECTION_STOP",
            "initiator": "ALGORITHM",
        }],
        close_commands=[],
    )
    assert (owner, mechanism, method) == (
        "ALGORITHM", "PROFIT_PROTECTION_STOP", "EXACT_PROTECTION_ORDER_ID"
    )


def test_stoploss_without_provenance_remains_unknown() -> None:
    assert classify_exit(
        exit_order_id="x", stop_order_type="StopLoss", protection_events=[], close_commands=[]
    ) == ("EXCHANGE", "UNKNOWN", "UNRESOLVED_PROTECTION_PROVENANCE")


def test_exact_bybit_trailing_create_type_is_trailing_evidence() -> None:
    assert classify_exit(
        exit_order_id="x",
        stop_order_type="TrailingStop",
        create_type="CreateByTrailingStop",
        protection_events=[],
        close_commands=[],
    ) == ("EXCHANGE", "TRAILING_STOP", "EXACT_BYBIT_EXIT_ORDER_CREATE_TYPE")


def test_manual_close_is_attributed_to_owner() -> None:
    owner, mechanism, _ = classify_exit(
        exit_order_id="x",
        stop_order_type="",
        protection_events=[],
        close_commands=[{"command_id": "web-1", "result_json": {"orderId": "x"}}],
    )
    assert owner == "OWNER"
    assert mechanism == "OWNER_MANUAL_CLOSE"


def test_trigger_slippage_math_long_and_short() -> None:
    assert trigger_to_fill_slippage_pct("Buy", Decimal("100"), Decimal("99")) == Decimal("1.00")
    assert trigger_to_fill_slippage_pct("Sell", Decimal("100"), Decimal("101")) == Decimal("1.00")
