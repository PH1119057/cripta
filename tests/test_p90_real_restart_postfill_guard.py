"""Crash-safe P90 REAL Fill recovery: actual production function, synthetic DB.

No Bybit orders. This does NOT replace real PG16 multi-connection acceptance.
"""
from __future__ import annotations

import ast
import builtins
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "operations/connectivity/private_runtime.py"
VERSION = "2.0-p90l90-real-main-sub-20261028"


class Cursor:
    def __init__(self, one=None, all_rows=None):
        self.one, self.all_rows = one, all_rows or []
    def fetchone(self):
        return self.one
    def fetchall(self):
        return self.all_rows


class Db:
    def __init__(self, version=VERSION, postfill=False, error=None, state="running"):
        self.version, self.postfill = version, postfill
        self.error, self.state, self.commits = error, state, 0
        self.sql = []
    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        if "SELECT command_id,state FROM runtime.trade_commands" in sql:
            return Cursor(all_rows=[("synthetic-entry", self.state)])
        if "SELECT order_id FROM runtime.hot_orders" in sql:
            return Cursor(all_rows=[])
        if "SELECT 1 FROM runtime.executions" in sql:
            return Cursor(one=(1,))
        if "SELECT payload_json FROM runtime.trade_commands" in sql:
            return Cursor(one=(json.dumps({"strategy_version": self.version}),))
        if "JOIN strategy_exit.exit_observations o" in sql:
            for phrase in ("P90L90_REAL_POSTFILL", "JOIN strategy_exit.exit_decisions d",
                           "JOIN strategy_exit.execution_requests r",
                           "JOIN strategy_exit.execution_dispatches ed",
                           "child.command_type='strategy_exit'",
                           "p.initial_protection_confirmed_at IS NOT NULL"):
                assert phrase in sql
            return Cursor(one=(1,) if self.postfill else None)
        if "UPDATE runtime.trade_commands" in sql:
            if "SET state='completed'" in sql:
                self.state = "completed"
                self.error = self.error or "recovered after restart from exact execution evidence"
            elif "P90_REAL_RESTART_POSTFILL_UNVERIFIED" in sql:
                self.state = "running"
                self.error = self.error or "P90_REAL_RESTART_POSTFILL_UNVERIFIED"
            else:
                raise AssertionError("Unexpected SQL update")
            return Cursor()
        raise AssertionError("Unrecognized query: " + sql[:100])
    def commit(self):
        self.commits += 1


def recover(db):
    assert SOURCE.is_file(), "Required full checkout missing"
    module = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(n for n in module.body if isinstance(n, ast.FunctionDef)
              and n.name == "resolve_prestart_entry_commands")
    original_import = builtins.__import__
    def imports(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "bybit_workbench.universal_entry.p90l90_real_contract_v3":
            return SimpleNamespace(VERSION=VERSION)
        return original_import(name, globals, locals, fromlist, level)
    isolated_builtins = dict(vars(builtins))
    isolated_builtins["__import__"] = imports
    scope = {
        "__builtins__": isolated_builtins,
        "psycopg": SimpleNamespace(Connection=object),
        "json": json, "time": time, "UTC": UTC, "datetime": datetime,
        "PROCESS_STARTED_AT_MS": 100000, "TRADING_ACCOUNT_ID": "synthetic-account",
        "ExchangeMutationBarrier": RuntimeError,
    }
    wrapper = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        fn], type_ignores=[])
    exec(compile(ast.fix_missing_locations(wrapper), str(SOURCE), "exec"), scope)
    scope["resolve_prestart_entry_commands"](db)
    return db


def test_fill_without_handoff_never_completes_or_reenters():
    db = recover(Db(postfill=False))
    assert db.state == "running"
    assert db.error == "P90_REAL_RESTART_POSTFILL_UNVERIFIED"
    assert db.commits == 1
    assert all("/v5/order/create" not in sql for sql, _ in db.sql)


def test_original_failure_preserved_when_handoff_missing():
    db = recover(Db(postfill=False, error="ORIGINAL_SOURCE_FAILURE"))
    assert db.state == "running" and db.error == "ORIGINAL_SOURCE_FAILURE"


def test_linked_postfill_child_allows_completed_without_duplicate_entry():
    db = recover(Db(postfill=True, error="ORIGINAL_SOURCE_FAILURE"))
    assert db.state == "completed" and db.error == "ORIGINAL_SOURCE_FAILURE"


def test_non_p90_entry_keeps_old_recovery_contract():
    db = recover(Db(version="r1-legacy", postfill=False))
    assert db.state == "completed"
    assert all("JOIN strategy_exit.exit_observations o" not in sql for sql, _ in db.sql)


def test_queued_real_fill_is_no_longer_replayable():
    db = recover(Db(state="queued", postfill=False))
    assert db.state == "running"


def test_queued_legacy_fill_remains_completed():
    db = recover(Db(state="queued", version="r1-legacy"))
    assert db.state == "completed"
