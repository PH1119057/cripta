"""Guard against obsolete R1 Exit decisions before any Bybit mutation."""
import ast
from pathlib import Path


def test_universal_exit_rejects_superseded_decision_before_exchange_mutation():
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_universal_exit_position"
    )
    body = ast.get_source_segment(source, func)
    assert "STRATEGY_EXIT_DECISION_ID_MISSING" in body
    assert "STRATEGY_EXIT_DECISION_NOT_FOUND" in body
    assert "STRATEGY_EXIT_DECISION_SUPERSEDED" in body
    assert "FROM strategy_exit.exit_decisions" in body
    assert "WHERE strategy_position_id=%s" in body
    assert "ORDER BY decided_at DESC, exit_decision_id DESC" in body
    assert body.index("STRATEGY_EXIT_DECISION_SUPERSEDED") < body.index(
        "exchange_position = next("
    )


def test_exit_order_replacement_remains_reduce_only_post_only():
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    start = source.index("if action_kind is ExitActionKind.SET_TP:")
    end = source.index("if action_kind is ExitActionKind.SET_PROTECTION:", start)
    block = source[start:end]
    assert '"reduceOnly": True' in block
    assert '"timeInForce": "PostOnly"' in block
    assert '"dynamic TP already resting"' in block


def test_universal_exit_rejects_superseded_geometry_observation():
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_universal_exit_position"
    )
    body = ast.get_source_segment(source, func)
    assert "FROM strategy_exit.exit_observations" in body
    assert "STRATEGY_EXIT_OBSERVATION_SUPERSEDED" in body
    assert body.index("STRATEGY_EXIT_OBSERVATION_SUPERSEDED") < body.index(
        "exchange_position = next("
    )


def test_partial_tp_replacement_failure_forces_exchange_reconciliation():
    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    start = source.index("if action_kind is ExitActionKind.SET_TP:")
    end = source.index("if action_kind is ExitActionKind.SET_PROTECTION:", start)
    block = source[start:end]
    assert "dynamic TP cancellation outcome needs reconciliation" in block
    assert "dynamic TP replacement failed after existing-order cancellation" in block
    assert "cancelled_count += 1" in block
    assert "raise ExchangeMutationBarrier(" in block
    assert "except Exception as exc:" in block


def test_latest_exit_decision_guard_with_recorded_observation():
    from datetime import UTC, datetime
    from decimal import Decimal

    import pytest

    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    func = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_universal_exit_position"
    )
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")],
                level=0,
            ),
            func,
        ],
        type_ignores=[],
    )
    ns = {"datetime": datetime, "UTC": UTC, "Decimal": Decimal}
    exec(compile(ast.fix_missing_locations(module), "<exit-guard>", "exec"), ns)
    guard = ns["_universal_exit_position"]

    class Result:
        def __init__(self, value):
            self.value = value

        def fetchone(self):
            return self.value

    class Connection:
        def __init__(self, *, decision="D1", observation="O1"):
            self.decision = decision
            self.observation = observation

        def execute(self, sql, params):
            assert params == ("POS1", "ACCOUNT") or params == ("POS1",)
            if "FROM runtime.position_ownership" in sql:
                return Result(
                    (
                        "POS1", "BYBIT:UNIFIED",
                        "BYBIT:UNIFIED:LINEAR:USDT:APTUSDT:0",
                        0, "r1_aptusdt", "v1", "CONFIG", "PLAN",
                        "APTUSDT", "Buy", "OPEN",
                    )
                )
            if "FROM strategy_exit.exit_decisions" in sql:
                return Result((self.decision, "O1"))
            if "FROM strategy_exit.exit_observations" in sql:
                return Result((self.observation,))
            raise AssertionError("Unexpected DB query")

    payload = {
        "source": "universal_exit",
        "strategy_position_id": "POS1",
        "position_idx": 0,
        "direction": "LONG",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "account_ref": "BYBIT:UNIFIED",
        "exchange_position_key": "BYBIT:UNIFIED:LINEAR:USDT:APTUSDT:0",
        "strategy_id": "r1_aptusdt",
        "strategy_version": "v1",
        "strategy_config_fingerprint": "CONFIG",
        "exit_plan_fingerprint": "PLAN",
        "exit_decision_id": "D1",
    }
    position = {"positionIdx": 0, "side": "Buy", "size": "3"}
    kwargs = {
        "payload": payload,
        "symbol": "APTUSDT",
        "positions_payload": {"result": {"list": [position]}},
    }
    # The production function uses exact account identity from its module global.
    ns["TRADING_ACCOUNT_ID"] = "ACCOUNT"
    assert guard(Connection(), **kwargs) == position
    with pytest.raises(RuntimeError, match="STRATEGY_EXIT_DECISION_SUPERSEDED"):
        guard(Connection(decision="D2"), **kwargs)
    with pytest.raises(RuntimeError, match="STRATEGY_EXIT_OBSERVATION_SUPERSEDED"):
        guard(Connection(observation="O2"), **kwargs)


def test_cancelled_resting_tp_and_failed_create_raise_reconciliation_barrier():
    import hashlib
    from decimal import Decimal
    from enum import Enum

    import pytest

    source = Path("operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
    func = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_execute_universal_exit_command"
    )
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            func,
        ],
        type_ignores=[],
    )

    class Action(Enum):
        SET_TP = "SET_TP"
        SET_STOP = "SET_STOP"
        SET_PROTECTION = "SET_PROTECTION"
        SET_TRAILING = "SET_TRAILING"

    class Barrier(Exception):
        pass

    calls = []
    symbol = "APTUSDT"
    position_id = "POS1"
    tag = "utp" + hashlib.sha256(position_id.encode()).hexdigest()[:12] + "-"
    existing = {
        "orderId": "OLD",
        "orderStatus": "New",
        "reduceOnly": True,
        "orderLinkId": tag + "existing",
        "price": "10.5",
        "leavesQty": "1",
        "qty": "1",
    }

    def post(path, data, key, secret, **kwargs):
        calls.append(path)
        if path == "/v5/order/create":
            raise RuntimeError("Bybit create failed")
        return {"retCode": 0}

    namespace = {
        "Decimal": Decimal,
        "hashlib": hashlib,
        "ExitActionKind": Action,
        "ExchangeMutationBarrier": Barrier,
        "_universal_exit_position": lambda *args, **kwargs: {
            "positionIdx": 0, "side": "Buy", "size": "1"
        },
        "validate_exit_mutation": lambda action, mutation: mutation,
        "_normalize_dynamic_tp": lambda price, tick, **kwargs: price,
        "_step_aligned": lambda price, step, label: price,
        "executable_close_price": lambda symbol, side: Decimal("10"),
        "api_get": lambda *args, **kwargs: (
            {"result": {"list": [existing]}}, None
        ),
        "api_post": post,
    }
    exec(compile(ast.fix_missing_locations(module), "<exit-replace>", "exec"), namespace)
    command = namespace["_execute_universal_exit_command"]
    kwargs = {
        "command_id": "CMD1",
        "symbol": symbol,
        "payload": {
            "source": "universal_exit",
            "strategy_position_id": position_id,
            "action_kind": "SET_TP",
            "requested_mutation": {
                "take_profit_price": "11",
                "order_type": "LIMIT",
                "tpsl_mode": "Full",
            },
        },
        "positions_payload": {},
        "tick": Decimal("0.01"),
        "qty_step": Decimal("1"),
    }
    with pytest.raises(Barrier, match="replacement failed"):
        command(None, "key", "secret", **kwargs)
    assert calls == ["/v5/order/cancel", "/v5/order/create"]
