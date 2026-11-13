"""Fail-closed static P90-only six cold table maintenance contract, Issue #642."""
import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "operations" / "monitoring" / "p90_only_cold_truncate_642.py"
SPEC = importlib.util.spec_from_file_location("cold_purge_642", PATH)
assert SPEC is not None and SPEC.loader is not None
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def test_static_manifest_exact_six_and_all_legacy():
    assert len(MOD.TABLE_ROWS) == 6
    assert sum(MOD.TABLE_ROWS.values()) == 97415
    assert all(name.endswith("_cold_20261107") for name in MOD.TABLE_ROWS)
    assert all(name.split(".")[0] in {"mayak_v2", "dispatcher_v2", "research_context"}
               for name in MOD.TABLE_ROWS)


def test_truncate_statement_no_cascade_or_identity_restart():
    sql = MOD.truncate_sql()
    assert sql.startswith("TRUNCATE TABLE ")
    assert "CASCADE" not in sql
    assert "RESTART IDENTITY" not in sql
    for name in MOD.TABLE_ROWS:
        assert sql.count(name) == 1
    assert "runtime." not in sql
    assert "analytics." not in sql
    assert "strategy_entry." not in sql


class Cursor:
    def __init__(self, *, changed_fk=False, missing=False, drift=False, recovery=False):
        self.rows = []
        self.recovery = recovery
        self.changed_fk = changed_fk
        self.missing = missing
        self.drift = drift
        self.last_query = ""

    def execute(self, query, params=None):
        self.last_query = query
        if "SELECT current_user,session_user" in query:
            self.rows = [("cripta", "cripta", "cripta", self.recovery)]
        elif "FROM pg_class c" in query:
            self.rows = [(t, "r", "postgres", 1024, int(self.changed_fk), 0)
                         for t in MOD.TABLE_ROWS]
            if self.missing:
                self.rows.pop()
        elif "SELECT count(*) FROM" in query:
            t = query.removeprefix("SELECT count(*) FROM ")
            self.rows = [(MOD.TABLE_ROWS[t] + int(self.drift),)]
        elif "pg_database_size" in query:
            self.rows = [(10_000_000,)]
        else:
            raise AssertionError(query)
        return self

    def fetchone(self):
        return self.rows.pop(0)

    def fetchall(self):
        result, self.rows = self.rows, []
        return result


def test_readonly_inventory_checks_all_tables():
    out = MOD._preflight(Cursor(), False)
    assert out["database_bytes"] == 10_000_000
    assert out["rows"] == MOD.TABLE_ROWS
    assert len(out["relation_bytes"]) == 6


@pytest.mark.parametrize("kwargs,pattern", [
    ({"changed_fk": True}, "COLD_TABLE_CONTRACT_CHANGED"),
    ({"missing": True}, "COLD_TABLE_MISSING"),
    ({"drift": True}, "COLD_TABLE_ROW_COUNT_DRIFT"),
    ({"recovery": True}, "WRONG_DB_OR_IN_RECOVERY"),
])
def test_contract_change_is_fail_closed(kwargs, pattern):
    with pytest.raises(RuntimeError, match=pattern):
        MOD._preflight(Cursor(**kwargs), False)
