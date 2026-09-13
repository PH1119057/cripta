from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from bybit_workbench.universal_entry.paper_runtime import (
    PaperTradeRuntime,
    _crossed_limit,
    _directional_move,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 12, 3, 0, tzinfo=UTC)


class Cursor:
    def __init__(self, *, rows=None, row=None):
        self._rows = [] if rows is None else rows
        self._row = row
        self.rowcount = 1

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class FakeConnection:
    def __init__(self, *, pending_rows=None):
        self.pending_rows = [] if pending_rows is None else pending_rows
        self.statements: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, statement: str, parameters=()):
        params = tuple(parameters)
        self.statements.append((statement, params))
        if "FROM strategy_entry.paper_orders" in statement and "state='PENDING'" in statement:
            return Cursor(rows=self.pending_rows)
        return Cursor()


def test_directional_paper_economics_are_symmetric() -> None:
    assert _directional_move(Decimal("100"), Decimal("101"), "LONG") == Decimal("1.00")
    assert _directional_move(Decimal("100"), Decimal("99"), "SHORT") == Decimal("1.00")
    assert _crossed_limit("LONG", Decimal("99.8"), Decimal("99.8")) is True
    assert _crossed_limit("LONG", Decimal("100"), Decimal("99.8")) is False
    assert _crossed_limit("SHORT", Decimal("100.2"), Decimal("100.2")) is True
    assert _crossed_limit("SHORT", Decimal("100"), Decimal("100.2")) is False


def test_limit_paper_order_opens_only_after_real_trade_cross_and_before_ttl() -> None:
    payload = {
        "stake_usdt": "10",
        "leverage": 2,
        "exit_policy": {},
        "lifecycle_policy": {},
        "initial_protection": {"stop_loss_pct": "1", "take_profit_pct": "1.1"},
    }
    pending = [
        (
            "paper-order-1",
            "request-1",
            "activation-1",
            "strategy-a",
            "1.0",
            "strategy-fp",
            "entry-fp",
            "exit-fp",
            "signal-1",
            "UNIUSDT",
            "LONG",
            "LIMIT_OFFSET",
            NOW,
            Decimal("99.80"),
            NOW + timedelta(seconds=30),
            payload,
        )
    ]
    connection = FakeConnection(pending_rows=pending)
    runtime = PaperTradeRuntime(connection)
    runtime._fill_orders("UNIUSDT", Decimal("100"), NOW + timedelta(seconds=1))
    sql = "\n".join(statement for statement, _ in connection.statements)
    assert "INSERT INTO strategy_entry.paper_positions" not in sql

    connection.statements.clear()
    runtime._fill_orders("UNIUSDT", Decimal("99.70"), NOW + timedelta(seconds=2))
    sql = "\n".join(statement for statement, _ in connection.statements)
    assert "SET state='FILLED'" in sql
    assert "INSERT INTO strategy_entry.paper_positions" in sql
    insert = next(
        params
        for statement, params in connection.statements
        if "INSERT INTO strategy_entry.paper_positions" in statement
    )
    # Paper limit fill is the Strategy limit price, not a better invented price.
    assert Decimal(str(insert[12])) == Decimal("99.80")


def test_primary_paper_take_profit_closes_with_real_market_price_and_pnl() -> None:
    connection = FakeConnection()
    runtime = PaperTradeRuntime(connection)
    row = (
        "paper-pos-1",
        None,
        "PRIMARY",
        "LONG",
        NOW,
        Decimal("100"),
        Decimal("10"),
        2,
        Decimal("20"),
        Decimal("0.2"),
        Decimal("100"),
        Decimal("0"),
        Decimal("0"),
        None,
        False,
        False,
        {
            "initial_protection": {"stop_loss_pct": "1", "take_profit_pct": "1.1"},
            "exit_policy": {
                "break_even": {"enabled": False},
                "trailing": {"enabled": False},
                "time_exit": {"enabled": False},
                "context_feature_policy": [],
            },
            "lifecycle_policy": {"hedge_policy": {"enabled": False}},
        },
    )
    runtime._update_one(row, Decimal("101.20"), NOW + timedelta(minutes=1), {})
    close = next(
        params for statement, params in connection.statements if "SET state='CLOSED'" in statement
    )
    assert close[2] == "TAKE_PROFIT"
    assert Decimal(str(close[9])) == Decimal("0.240")
    assert Decimal(str(close[10])) == Decimal("1.200")


def test_hedge_with_disabled_sl_tp_does_not_invent_hidden_protection() -> None:
    connection = FakeConnection()
    runtime = PaperTradeRuntime(connection)
    row = (
        "paper-hedge-1",
        "paper-primary-1",
        "HEDGE",
        "SHORT",
        NOW,
        Decimal("100"),
        Decimal("10"),
        1,
        Decimal("10"),
        Decimal("0.1"),
        Decimal("100"),
        Decimal("0"),
        Decimal("0"),
        None,
        False,
        False,
        {
            "hedge_leg_policy": {
                "stop_loss": {"enabled": False},
                "take_profit": {"enabled": False},
            },
            "exit_policy": {
                "break_even": {"enabled": False},
                "trailing": {"enabled": False},
                "time_exit": {"enabled": False},
                "context_feature_policy": [],
            },
            "lifecycle_policy": {},
        },
    )
    runtime._update_one(row, Decimal("99.50"), NOW + timedelta(minutes=1), {})
    sql = "\n".join(statement for statement, _ in connection.statements)
    assert "SET state='CLOSED'" not in sql
    assert "SET best_price=" in sql


def test_execution_permission_is_per_strategy_and_never_replays_old_requests() -> None:
    source = (ROOT / "operations/connectivity/universal_entry_consumer.py").read_text(
        encoding="utf-8"
    )
    assert "JOIN strategy_entry.execution_permissions p" in source
    assert "p.enabled=true" in source
    assert "a.enabled=true" in source
    assert "r.requested_at >= p.enabled_at" in source


def test_monitoring_schema_separates_activation_execution_permission_and_paper_positions() -> None:
    sql = (ROOT / "operations/sql/20260912_strategy_monitoring_execution_permission.sql").read_text(
        encoding="utf-8"
    )
    for token in (
        "strategy_entry.execution_permissions",
        "strategy_entry.execution_permission_events",
        "strategy_entry.paper_orders",
        "strategy_entry.paper_positions",
        "strategy_entry.paper_position_events",
        "ExecutionPermission identity is immutable",
    ):
        assert token in sql


def test_multi_strategy_observer_creates_paper_trades_but_has_no_exchange_mutation() -> None:
    source = (ROOT / "operations/monitoring/universal_entry_shadow.py").read_text(encoding="utf-8")
    assert 'RUNTIME_MODE == "MULTI_STRATEGY_OBSERVER"' in source
    assert "load_active_strategy_bundles" in source
    assert "PaperTradeRuntime" in source
    assert "paper.create_order" in source
    assert '"trading_effect": "NONE"' in source
    scope = source[source.index("def _run_multi_strategy_observer") :]
    assert "runtime.trade_commands" not in scope
    assert "api_post(" not in scope
