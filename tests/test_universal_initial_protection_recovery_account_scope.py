"""Contract tests for account-owned recovery of exchange initial protection.

These tests execute the exact SQL embedded in the production worker against
an isolated in-memory relational table. No Bybit or production database calls.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

SOURCE = (Path(__file__).resolve().parents[1] / "operations/connectivity/private_runtime.py").read_text(
    encoding="utf-8"
)


def _recovery_insert_sql() -> str:
    tree = ast.parse(SOURCE)
    matches: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "execute" or not node.args:
            continue
        statement = node.args[0]
        if not isinstance(statement, ast.Constant) or not isinstance(statement.value, str):
            continue
        sql = statement.value
        if "INSERT INTO runtime.trade_commands(" in sql and "'initial_protection'" in sql:
            matches.append(sql)
    assert len(matches) == 1, "Exactly one identity-preserving recovery insert is required"
    return matches[0]


def _database() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute("ATTACH DATABASE ':memory:' AS runtime")
    db.execute(
        """CREATE TABLE runtime.trade_commands (
            command_id TEXT PRIMARY KEY, command_type TEXT NOT NULL,
            symbol TEXT NOT NULL, payload_json TEXT NOT NULL,
            state TEXT NOT NULL, requested_at_epoch_ms INTEGER NOT NULL,
            strategy_execution_binding_id TEXT, execution_leg TEXT,
            trading_account_id TEXT, identity_status TEXT NOT NULL,
            CHECK (
                (identity_status='LEGACY_UNKNOWN' AND trading_account_id IS NULL
                 AND strategy_execution_binding_id IS NULL AND execution_leg IS NULL)
                OR
                (identity_status='PROVEN' AND trading_account_id IS NOT NULL
                 AND ((strategy_execution_binding_id IS NULL AND execution_leg IS NULL)
                   OR (strategy_execution_binding_id IS NOT NULL
                       AND execution_leg IS NOT NULL)))
            )
        )"""
    )
    return db


def _parent(db: sqlite3.Connection, command_id: str, *, account: str | None,
            state: str = "completed") -> None:
    proven = account is not None
    db.execute(
        """INSERT INTO runtime.trade_commands(
            command_id,command_type,symbol,payload_json,state,requested_at_epoch_ms,
            strategy_execution_binding_id,execution_leg,trading_account_id,identity_status)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (command_id, "entry", "DOTUSDT", "{}", state, 1,
         "binding-a" if proven else None, "PRIMARY" if proven else None,
         account, "PROVEN" if proven else "LEGACY_UNKNOWN"),
    )


def _insert_recovery(db: sqlite3.Connection, command_id: str, *, parent: str,
                     account: str) -> None:
    db.execute(
        _recovery_insert_sql().replace("%s", "?"),
        (command_id, "DOTUSDT", '{"initial_protection":{"stop_loss_enabled":true}}',
         2, parent, account),
    )


def test_auto_init_inherits_exact_account_and_binding_from_proven_entry() -> None:
    db = _database()
    _parent(db, "entry-a", account="account-a")
    _insert_recovery(db, "auto-init-a", parent="entry-a", account="account-a")
    assert db.execute(
        """SELECT command_type,state,trading_account_id,identity_status,
                  strategy_execution_binding_id,execution_leg
             FROM runtime.trade_commands WHERE command_id='auto-init-a'"""
    ).fetchone() == (
        "initial_protection", "queued", "account-a", "PROVEN",
        "binding-a", "PRIMARY",
    )
    _insert_recovery(db, "auto-init-a", parent="entry-a", account="account-a")
    assert db.execute(
        "SELECT count(*) FROM runtime.trade_commands WHERE command_id='auto-init-a'"
    ).fetchone()[0] == 1


def test_auto_init_refuses_cross_account_legacy_and_nonterminal_parent() -> None:
    db = _database()
    _parent(db, "entry-a", account="account-a")
    _parent(db, "entry-old", account=None)
    _parent(db, "entry-pending", account="account-a", state="queued")
    _insert_recovery(db, "foreign", parent="entry-a", account="account-b")
    _insert_recovery(db, "legacy", parent="entry-old", account="account-a")
    _insert_recovery(db, "pending", parent="entry-pending", account="account-a")
    assert db.execute(
        "SELECT count(*) FROM runtime.trade_commands WHERE command_type='initial_protection'"
    ).fetchone()[0] == 0


def test_auto_init_key_contains_account_to_prevent_cross_account_collision() -> None:
    assert 'protection_key=f"{TRADING_ACCOUNT_ID}:{entry_id}:' in SOURCE
    assert "c.trading_account_id=%s" in _recovery_insert_sql()
    assert "c.identity_status='PROVEN'" in _recovery_insert_sql()
