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
