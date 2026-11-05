"""Execute production recovery SQL against sequential same-symbol cycles."""

import ast
import sqlite3
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "operations/connectivity/private_runtime.py"


def recovery_selection(function_name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    function = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function_name
    )
    for node in ast.walk(function):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            sql = node.value
            if "SELECT c.command_id,c.symbol" in sql and "JOIN runtime.executions" in sql:
                return (
                    sql.replace("%s", "?")
                    .replace(
                        "e.payload_json::jsonb->>'closedSize'",
                        "CAST(json_extract(e.payload_json,'$.closedSize') AS REAL)",
                    )
                    .replace("::numeric", "")
                )
    raise AssertionError("production recovery selection not found")


@pytest.mark.parametrize("account", ["MAIN", "SUB"])
@pytest.mark.parametrize(
    "function_name",
    [
        "command_worker_loop",
        "protect_recovered_bot_positions",
    ],
)
def test_closed_entry_is_not_a_candidate_for_next_same_symbol_position(account, function_name):
    with sqlite3.connect(":memory:") as db:
        db.execute("ATTACH DATABASE ':memory:' AS runtime")
        db.execute(
            "CREATE TABLE runtime.trade_commands(command_id,symbol,command_type,"
            "state,trading_account_id,identity_status,payload_json)"
        )
        db.execute(
            "CREATE TABLE runtime.executions(order_link_id,exec_time_ms,"
            "trading_account_id,identity_status,payload_json)"
        )
        db.execute(
            "CREATE TABLE runtime.position_ownership(position_id,entry_command_id,"
            "trading_account_id,symbol,state)"
        )
        for entry, state, instant in [("old-closed", "CLOSED", 1000), ("new-open", "OPEN", 2000)]:
            db.execute(
                "INSERT INTO runtime.trade_commands VALUES(?, 'DOTUSDT', 'entry',"
                "'completed', ?, 'PROVEN', '{}')",
                (entry, account),
            )
            db.execute(
                "INSERT INTO runtime.executions VALUES(?, ?, ?, 'PROVEN','{\"closedSize\":\"0\"}')",
                (entry, instant, account),
            )
            db.execute(
                "INSERT INTO runtime.position_ownership VALUES(?, ?, ?, 'DOTUSDT', ?)",
                ("position-" + entry, entry, account, state),
            )
        selected = db.execute(recovery_selection(function_name), (account, account)).fetchall()
        assert [row[0] for row in selected] == ["new-open"]
        assert db.execute(
            "SELECT state FROM runtime.position_ownership WHERE entry_command_id='old-closed'"
        ).fetchone() == ("CLOSED",)
