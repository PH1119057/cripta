"""F01 negative acceptance: exact current-cycle provenance, disposable PostgreSQL only.

RED-first audit. Current-scope logic must not bind a current position to an
unrelated single historical Entry just because account/symbol coincide and
Bybit omits openTime. No real Exchange API and no production DB.
"""
from __future__ import annotations

import ast
import json
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
SOURCE = Path(__file__).resolve().parents[1] / "operations/connectivity/private_runtime.py"
pytestmark = pytest.mark.skipif(not DSN, reason="requires disposable PostgreSQL")


@pytest.fixture
def isolated_pg():
    psycopg = pytest.importorskip("psycopg")
    assert DSN is not None
    connection = psycopg.connect(DSN)
    schema = "f01_lineage_red_" + uuid4().hex
    try:
        assert connection.info.dbname != "cripta", "production DB forbidden"
        connection.execute(f"CREATE SCHEMA {schema}")
        connection.execute(
            f"CREATE TABLE {schema}.trade_commands (command_id text,symbol text,"
            "command_type text,state text,trading_account_id text,"
            "identity_status text,payload_json jsonb)"
        )
        connection.execute(
            f"CREATE TABLE {schema}.executions (exec_id text,order_id text,"
            "order_link_id text,exec_time_ms bigint,trading_account_id text,"
            "identity_status text,payload_json jsonb,side text,exec_qty numeric)"
        )
        connection.execute(
            f"CREATE TABLE {schema}.position_ownership (entry_command_id text,"
            "trading_account_id text,state text)"
        )
        connection.execute(
            f"CREATE TABLE {schema}.hot_positions (symbol text,trading_account_id text,"
            "identity_status text,side text,size numeric,entry_price numeric,"
            "position_idx integer,payload_json jsonb)"
        )
        connection.execute(
            f"CREATE TABLE {schema}.recovered_closed_entry_cycles (entry_order_id text,"
            "symbol text,entry_execution_ids jsonb)"
        )
        yield connection, schema
    finally:
        connection.rollback()
        connection.close()


def seed_counterexample(connection, schema: str, account: str, scenario: str) -> None:
    assert scenario in {"wrong_side", "excess_qty", "already_recovered_closed"}
    command_payload = {
        "source": "universal_entry",
        "initial_protection": {"contract": "historical"},
    }
    connection.execute(
        f"INSERT INTO {schema}.trade_commands VALUES "
        "(%s,%s,'entry','completed',%s,'PROVEN',%s::jsonb)",
        ("old-entry", "DOTUSDT", account, json.dumps(command_payload)),
    )
    connection.execute(
        f"INSERT INTO {schema}.executions VALUES "
        "(%s,%s,%s,1000,%s,'PROVEN',%s::jsonb,'Buy',1.25)",
        ("old-exec", "old-order", "old-entry", account, '{"closedSize":"0"}'),
    )
    side = "Sell" if scenario == "wrong_side" else "Buy"
    qty = Decimal("9.00") if scenario == "excess_qty" else Decimal("1.25")
    position_payload = {
        "positionIdx": 0,
        "symbol": "DOTUSDT",
        "side": side,
        "size": str(qty),
        "stopLoss": "0",
        "trailingStop": "0",
        # No openTime, just like the audited production failure shape.
    }
    connection.execute(
        f"INSERT INTO {schema}.hot_positions VALUES "
        "(%s,%s,'PROVEN',%s,%s,125.00,0,%s::jsonb)",
        ("DOTUSDT", account, side, qty, json.dumps(position_payload)),
    )
    if scenario == "already_recovered_closed":
        connection.execute(
            f"INSERT INTO {schema}.recovered_closed_entry_cycles VALUES "
            "('old-order','DOTUSDT','[\"old-exec\"]'::jsonb)"
        )
    assert "openTime" not in position_payload
    assert connection.execute(
        f"SELECT count(*) FROM {schema}.position_ownership"
    ).fetchone()[0] == 0


def production_function(name: str) -> ast.FunctionDef:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def recovery_sql(name: str) -> str:
    fn = production_function(name)
    sqls = [
        n.value for n in ast.walk(fn)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and "SELECT c.command_id,c.symbol" in n.value
        and "JOIN runtime.executions" in n.value
    ]
    assert len(sqls) == 1
    return sqls[0]


def current_candidates(connection, schema: str, account: str, path: str) -> list[str]:
    sql = recovery_sql(path).replace("runtime.", schema + ".")
    return [str(x[0]) for x in connection.execute(sql, (account, account)).fetchall()]


@pytest.mark.parametrize("account", ["MAIN", "SUB"])
@pytest.mark.parametrize("path", ["command_worker_loop", "protect_recovered_bot_positions"])
@pytest.mark.parametrize("scenario", ["wrong_side", "excess_qty", "already_recovered_closed"])
def test_unrelated_unique_historical_entry_must_not_bind_current_position(
    isolated_pg, account, path, scenario,
):
    connection, schema = isolated_pg
    seed_counterexample(connection, schema, account, scenario)
    # Zero eligible: current open position has no proven link to old Entry.
    # Single account/symbol candidate is not exact Exchange execution proof.
    assert current_candidates(connection, schema, account, path) == []


@pytest.mark.parametrize("account", ["MAIN", "SUB"])
@pytest.mark.parametrize("scenario", ["wrong_side", "already_recovered_closed"])
def test_actual_startup_recovery_must_not_send_old_protection_to_other_position(
    isolated_pg, account, scenario,
):
    connection, schema = isolated_pg
    seed_counterexample(connection, schema, account, scenario)

    class ScopedConnection:
        def execute(self, statement, parameters=()):
            return connection.execute(statement.replace("runtime.", schema + "."), parameters)

    function = production_function("protect_recovered_bot_positions")
    calls = []

    def forbidden_exchange_post(*args, **kwargs):
        calls.append((args[0], args[1] if len(args) > 1 else None))
        raise AssertionError("would send a stale protection mutation to Exchange")

    def fake_api_get(*_a, **_kw):
        return (
            {"retCode": 0, "result": {"list": [{"priceFilter": {"tickSize": "0.01"}}]}},
            None,
        )
    namespace = {
        "psycopg": SimpleNamespace(Connection=object),
        "TRADING_ACCOUNT_ID": account,
        "json": json,
        "Decimal": Decimal,
        "api_get": fake_api_get,
        "api_post": forbidden_exchange_post,
        "initial_protection_contract": lambda _p: {
            "tpsl_mode": "Full", "trigger_by": "LastPrice",
        },
        "resolve_initial_protection_boundaries": lambda **_kw: (
            Decimal("120.00"), None,
        ),
        "reconcile": lambda *_a, **_kw: None,
    }
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"),
        namespace,
    )
    assert namespace["protect_recovered_bot_positions"](
        ScopedConnection(), "unused", "unused"
    ) == 0
    assert calls == [], "no Exchange post is permitted without exact cycle proof"
