"""F01 negative acceptance: exact current-cycle provenance, disposable PostgreSQL only.

RED-first audit. Current-scope logic must not bind a current position to an
unrelated single historical Entry just because account/symbol coincide and
Bybit omits openTime. No real Exchange API and no production DB.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bybit_workbench.entry_cycle_recovery import RecoveryInventory

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
SOURCE = Path(__file__).resolve().parents[1] / "operations/connectivity/private_runtime.py"
sys.path.insert(0, str(SOURCE.parent))
sys.path.insert(0, str(SOURCE.parents[2] / "research/server/connectivity"))
import private_runtime as runtime  # noqa: E402

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
            "identity_status text,payload_json jsonb,side text,exec_qty numeric,exec_price numeric)"
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
        connection.execute(
            f"CREATE TABLE {schema}.connection_events (at_epoch_ms bigint,channel text,"
            "event text,details_json text,trading_account_id text,account_connector_id text,"
            "identity_status text)"
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
        "(%s,%s,%s,1000,%s,'PROVEN',%s::jsonb,'Buy',1.25,125)",
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
        "avgPrice": "125", "seq": "20", "updatedTime": "2000",
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


class ScopedConnection:
    def __init__(self, connection, schema):
        self.connection, self.schema = connection, schema

    def execute(self, statement, parameters=()):
        return self.connection.execute(statement.replace("runtime.", self.schema + "."), parameters)

    def commit(self):
        pass  # All disposable effects remain in the rolled-back transaction.


def inventory_for(connection, schema, account):
    position = connection.execute(f"SELECT payload_json FROM {schema}.hot_positions").fetchone()[0]
    fill = {"execId": "old-exec", "orderId": "old-order", "orderLinkId": "old-entry",
            "execTime": "1000", "execType": "Trade", "symbol": "DOTUSDT", "side": "Buy",
            "execQty": "1.25", "execPrice": "125", "closedSize": "0", "seq": "10"}
    order = {"orderId": "old-order", "orderLinkId": "old-entry", "symbol": "DOTUSDT",
             "side": "Buy", "positionIdx": 0, "cumExecQty": "1.25", "avgPrice": "125"}
    return RecoveryInventory(account, "connector-" + account, 999, 2500, 3000,
                             (position,), (fill,), {"old-order": order})


def configure_runtime(monkeypatch, connection, schema, account):
    inventory = inventory_for(connection, schema, account)
    monkeypatch.setattr(runtime, "TRADING_ACCOUNT_ID", account)
    monkeypatch.setattr(runtime, "ACCOUNT_CONNECTOR_ID", "connector-" + account)
    monkeypatch.setattr(runtime, "_f01_recovery_denials", {})
    monkeypatch.setattr(runtime, "_f01_signed_inventory", lambda *_a: inventory)
    monkeypatch.setattr(runtime, "atomic_status", lambda *_a: None)
    monkeypatch.setattr(runtime, "time", SimpleNamespace(time=lambda: 3))
    return inventory


@pytest.mark.parametrize("account", ["MAIN", "SUB"])
@pytest.mark.parametrize("path", ["command_worker_loop", "protect_recovered_bot_positions"])
@pytest.mark.parametrize("scenario", ["wrong_side", "excess_qty", "already_recovered_closed"])
def test_unrelated_unique_historical_entry_must_not_bind_current_position(
    isolated_pg, monkeypatch, account, path, scenario,
):
    connection, schema = isolated_pg
    seed_counterexample(connection, schema, account, scenario)
    configure_runtime(monkeypatch, connection, schema, account)
    # SQL discovers candidates; only the production exact-cycle proof can
    # authorize ownership. Test the final verdict, not discovery alone.
    accepted = [entry for entry in current_candidates(connection, schema, account, path)
                if runtime._f01_current_cycle(ScopedConnection(connection, schema), entry,
                                              "DOTUSDT", "unused", "unused") is not None]
    assert accepted == []


@pytest.mark.parametrize("account", ["MAIN", "SUB"])
@pytest.mark.parametrize("scenario", ["wrong_side", "already_recovered_closed"])
def test_actual_startup_recovery_must_not_send_old_protection_to_other_position(
    isolated_pg, monkeypatch, account, scenario,
):
    connection, schema = isolated_pg
    seed_counterexample(connection, schema, account, scenario)
    configure_runtime(monkeypatch, connection, schema, account)

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
        "_f01_current_cycle": runtime._f01_current_cycle,
    }
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"),
        namespace,
    )
    assert namespace["protect_recovered_bot_positions"](
        ScopedConnection(connection, schema), "unused", "unused"
    ) == 0
    assert calls == [], "no Exchange post is permitted without exact cycle proof"
