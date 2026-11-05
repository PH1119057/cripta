"""F01 recovery selection against real PostgreSQL, without production or Exchange access.

The test extracts the exact SQL literals from the private runtime. No SQLite
translation, synthetic Exchange call, or Strategy/order mutation is used.
Every fixture lives inside an uncommitted transaction and is rolled back.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
SOURCE = Path(__file__).resolve().parents[1] / "operations/connectivity/private_runtime.py"

pytestmark = pytest.mark.skipif(
    not DSN, reason="explicit disposable PostgreSQL required for F01",
)


def production_sql(function_name: str, marker: str) -> str:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    matches = [
        node.value for node in ast.walk(function)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and marker in node.value
    ]
    assert len(matches) == 1, (function_name, marker, len(matches))
    return matches[0]


@pytest.fixture
def isolated_pg():
    psycopg = pytest.importorskip("psycopg")
    assert DSN is not None
    connection = psycopg.connect(DSN)
    schema = "f01_acceptance_" + uuid4().hex
    try:
        assert connection.info.dbname != "cripta", "production database forbidden"
        connection.execute(f"CREATE SCHEMA {schema}")
        connection.execute(
            f"CREATE TABLE {schema}.trade_commands ("
            "command_id text,symbol text,command_type text,state text,"
            "trading_account_id text,identity_status text,"
            "payload_json jsonb,requested_at_epoch_ms bigint,started_at_epoch_ms bigint)"
        )
        connection.execute(
            f"CREATE TABLE {schema}.executions ("
            "exec_id text,order_id text,order_link_id text,"
            "exec_time_ms bigint,trading_account_id text,"
            "identity_status text,payload_json jsonb,side text,"
            "exec_qty numeric,exec_price numeric)"
        )
        connection.execute(
            f"CREATE TABLE {schema}.position_ownership ("
            "position_id text,entry_command_id text,trading_account_id text,"
            "symbol text,state text)"
        )
        connection.execute(
            f"CREATE TABLE {schema}.hot_positions ("
            "symbol text,trading_account_id text,identity_status text,"
            "side text,size numeric,entry_price numeric,"
            "position_idx integer,payload_json jsonb)"
        )
        connection.execute(
            f"CREATE TABLE {schema}.recovered_closed_entry_cycles ("
            "entry_order_id text,entry_execution_ids jsonb)"
        )
        yield connection, schema
    finally:
        # No COMMIT: even successful tests destroy their temporary schema/data.
        connection.rollback()
        connection.close()


def _seed(
    connection, schema: str, command: str, account: str, *,
    symbol: str = "DOTUSDT", command_type: str = "entry",
    command_state: str = "completed", command_identity: str = "PROVEN",
    execution_identity: str = "PROVEN", closed_size: str = "0",
    position_state: str | None = None, executions: bool = True,
    time_ms: int = 1000, side: str = "Buy",
) -> None:
    payload = {"source": "universal_entry", "initial_protection": {"test": True}}
    connection.execute(
        f"INSERT INTO {schema}.trade_commands "
        "(command_id,symbol,command_type,state,trading_account_id,identity_status,"
        "payload_json,requested_at_epoch_ms) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
        (command, symbol, command_type, command_state, account,
         command_identity, json.dumps(payload), time_ms),
    )
    if executions:
        connection.execute(
            f"INSERT INTO {schema}.executions VALUES "
            "(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)",
            (f"{command}-exec", f"{command}-order", command, time_ms,
             account, execution_identity,
             json.dumps({"closedSize": closed_size}), side, "1.25", "125.00"),
        )
    if position_state is not None:
        connection.execute(
            f"INSERT INTO {schema}.position_ownership VALUES (%s,%s,%s,%s,%s)",
            (f"{command}-position", command, account, symbol, position_state),
        )


def _selected(connection, schema: str, function_name: str, account: str) -> list[str]:
    sql = production_sql(function_name, "SELECT c.command_id,c.symbol")
    assert "JOIN runtime.executions" in sql
    return [
        str(row[0]) for row in connection.execute(
            sql.replace("runtime.", schema + "."),
            (account, account),
        ).fetchall()
    ]


RECOVERY_PATHS = ("command_worker_loop", "protect_recovered_bot_positions")
ACCOUNTS = ("MAIN", "SUB")


@pytest.mark.parametrize("account", ACCOUNTS)
@pytest.mark.parametrize("function_name", RECOVERY_PATHS)
@pytest.mark.parametrize("current_ownership", ("OPEN", None))
def test_three_cycles_without_open_time_preserve_closed_history(
    isolated_pg, account, function_name, current_ownership,
):
    connection, schema = isolated_pg
    _seed(connection, schema, "old-closed", account,
          position_state="CLOSED", time_ms=1000)
    _seed(connection, schema, "second-closed", account,
          position_state="CLOSED", time_ms=2000)
    _seed(connection, schema, "current", account,
          position_state=current_ownership, time_ms=3000, side="Sell")
    connection.execute(
        f"INSERT INTO {schema}.hot_positions VALUES "
        "(%s,%s,'PROVEN','Sell',1.25,125.00,0,%s::jsonb)",
        ("DOTUSDT", account, json.dumps({"size": "1.25", "side": "Sell"})),
    )
    assert "openTime" not in connection.execute(
        f"SELECT payload_json::text FROM {schema}.hot_positions"
    ).fetchone()[0]
    # Another account's CLOSED row cannot hide this account's current Entry.
    connection.execute(
        f"INSERT INTO {schema}.position_ownership VALUES "
        "('foreign-pos','current','OTHER','DOTUSDT','CLOSED')"
    )
    assert _selected(connection, schema, function_name, account) == ["current"]
    assert connection.execute(
        f"SELECT entry_command_id,state FROM {schema}.position_ownership "
        "WHERE trading_account_id=%s AND state='CLOSED' "
        "ORDER BY entry_command_id",
        (account,),
    ).fetchall() == [("old-closed", "CLOSED"), ("second-closed", "CLOSED")]


@pytest.mark.parametrize("account", ACCOUNTS)
@pytest.mark.parametrize("function_name", RECOVERY_PATHS)
@pytest.mark.parametrize("bad_proof", ("command", "execution"))
def test_unproven_current_identity_is_not_selected(
    isolated_pg, account, function_name, bad_proof,
):
    connection, schema = isolated_pg
    _seed(
        connection, schema, "unproven", account,
        command_identity="UNKNOWN" if bad_proof == "command" else "PROVEN",
        execution_identity="UNKNOWN" if bad_proof == "execution" else "PROVEN",
    )
    assert _selected(connection, schema, function_name, account) == []


@pytest.mark.parametrize("account", ACCOUNTS)
@pytest.mark.parametrize("function_name", RECOVERY_PATHS)
def test_closed_fill_and_executionless_orphan_cannot_be_current(
    isolated_pg, account, function_name,
):
    connection, schema = isolated_pg
    _seed(connection, schema, "closing-execution", account, closed_size="0.5")
    _seed(connection, schema, "orphan", account, executions=False)
    _seed(connection, schema, "foreign", "OTHER")
    assert _selected(connection, schema, function_name, account) == []


@pytest.mark.parametrize("account", ACCOUNTS)
@pytest.mark.parametrize("function_name", RECOVERY_PATHS)
def test_nonterminal_command_must_not_be_recovered_as_completed_fill(
    isolated_pg, account, function_name,
):
    connection, schema = isolated_pg
    _seed(connection, schema, "unsettled", account, command_state="queued")
    # An execution record alone cannot promote a queued/uncertain intent.
    assert _selected(connection, schema, function_name, account) == []


@pytest.mark.parametrize("account", ACCOUNTS)
@pytest.mark.parametrize("function_name", RECOVERY_PATHS)
def test_competing_unclosed_same_symbol_cannot_choose_a_winner(
    isolated_pg, account, function_name,
):
    connection, schema = isolated_pg
    _seed(connection, schema, "old-unresolved", account,
          position_state="RECONCILIATION_REQUIRED", time_ms=1000)
    _seed(connection, schema, "current", account,
          position_state=None, time_ms=2000)
    # Without openTime or a proven current execution mapping, selecting
    # *either* Entry as the live position's parent would invent provenance.
    assert _selected(connection, schema, function_name, account) == []


@pytest.mark.parametrize("account", ACCOUNTS)
def test_current_execution_ids_quantities_side_and_exit_queue_remain_distinct(
    isolated_pg, account,
):
    connection, schema = isolated_pg
    _seed(connection, schema, "old-closed", account,
          position_state="CLOSED", side="Buy")
    _seed(connection, schema, "current", account,
          position_state="OPEN", time_ms=3000, side="Sell")
    connection.execute(
        f"INSERT INTO {schema}.executions VALUES "
        "(%s,%s,%s,%s,%s,'PROVEN',%s::jsonb,'Sell',0.75,125.00)",
        ("current-exec-2", "current-order", "current", 3001,
         account, json.dumps({"closedSize": "0"})),
    )
    connection.execute(
        f"INSERT INTO {schema}.hot_positions VALUES "
        "('DOTUSDT',%s,'PROVEN','Sell',2.00,125.00,0,%s::jsonb)",
        (account, json.dumps({"size": "2.00", "side": "Sell"})),
    )
    worker_sql = production_sql(
        "command_worker_loop", "SELECT exec_id,order_id,order_link_id,exec_time_ms"
    )
    execution_ids = connection.execute(
        worker_sql.replace("runtime.", schema + "."),
        ("current", account),
    ).fetchall()
    assert [r[0] for r in execution_ids] == ["current-exec", "current-exec-2"]
    assert {r[2] for r in execution_ids} == {"current"}
    assert {r[1] for r in execution_ids} == {"current-order"}
    identity = connection.execute(
        f"SELECT side,size,entry_price,payload_json "
        f"FROM {schema}.hot_positions WHERE symbol='DOTUSDT' "
        "AND trading_account_id=%s AND identity_status='PROVEN'",
        (account,),
    ).fetchone()
    assert identity[:3] == ("Sell", 2, 125)
    assert [r[0] for r in connection.execute(
        f"SELECT exec_id FROM {schema}.executions "
        "WHERE order_link_id='old-closed'",
    ).fetchall()] == ["old-closed-exec"]
    _seed(
        connection, schema, "exit-queued", account,
        command_type="exit", command_state="queued", executions=False,
    )
    dispatch_sql = production_sql(
        "command_worker_loop",
        "SELECT command_id,command_type,symbol,payload_json",
    )
    next_row = connection.execute(
        dispatch_sql.replace("runtime.", schema + "."),
        (account,),
    ).fetchone()
    assert next_row[:2] == ("exit-queued", "exit")


@pytest.mark.parametrize("account", ACCOUNTS)
@pytest.mark.parametrize("scenario", ("competing", "unproven"))
def test_actual_worker_passes_ambiguous_recovery_and_dispatches_queued_exit(
    isolated_pg, account, scenario,
):
    """Run the real command_worker_loop AST on isolated PG; no Exchange sender."""
    import time

    connection, schema = isolated_pg
    if scenario == "competing":
        _seed(
            connection, schema, "prior-unresolved", account,
            position_state="RECONCILIATION_REQUIRED", time_ms=1000,
        )
        _seed(connection, schema, "current", account, time_ms=2000)
    else:
        _seed(
            connection, schema, "current-unproven", account,
            command_identity="UNKNOWN",
        )
    _seed(
        connection, schema, "exit-queued", account,
        command_type="exit", command_state="queued", executions=False,
    )
    assert _selected(connection, schema, "command_worker_loop", account) == []

    class ScopedConnection:
        def execute(self, statement, parameters=()):
            return connection.execute(
                statement.replace("runtime.", schema + "."), parameters
            )

        def commit(self):
            # Keep all test effects in the uncommitted disposable transaction.
            pass

        def rollback(self):
            connection.rollback()

    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    worker = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "command_worker_loop"
    )
    observed = []
    scope = {
        "running": True,
        "TRADING_ACCOUNT_ID": account,
        "db": lambda _label: ScopedConnection(),
        "time": time,
        "atomic_status": lambda *_args, **_kw: None,
        "cancel_expired_entry_limits": lambda *_args: None,
    }

    def no_exchange_command_sender(_connection, _key, _secret, row):
        observed.append((row[0], row[1]))
        scope["running"] = False

    scope["execute_command"] = no_exchange_command_sender
    exec(
        compile(ast.Module(body=[worker], type_ignores=[]), str(SOURCE), "exec"),
        scope,
    )
    scope["command_worker_loop"]("unused", "unused")
    assert observed == [("exit-queued", "exit")]
    assert connection.execute(
        f"SELECT state FROM {schema}.trade_commands WHERE command_id='exit-queued'"
    ).fetchone() == ("running",)
