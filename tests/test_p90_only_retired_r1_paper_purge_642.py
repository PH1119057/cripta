"""No-financial-link R1 PAPER retention gate for Issue #642."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "operations/monitoring/p90_only_retired_r1_paper_purge_642.py"
)
SPEC = importlib.util.spec_from_file_location("r1_paper_642", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_paper_scope_is_fixed_and_reviewable() -> None:
    s = SOURCE.read_text(encoding="utf-8")
    compile(s, str(SOURCE), "exec")
    assert MODULE.COUNTS == {
        "strategy_entry.paper_position_events": 5097,
        "strategy_entry.paper_positions": 102,
        "strategy_entry.paper_orders": 178,
    }
    assert MODULE.RETIRED_OPEN_SYMBOLS == [
        "APTUSDT", "ARBUSDT", "INJUSDT", "LTCUSDT",
    ]
    assert "TRUNCATE TABLE strategy_entry.paper_position_events" in s
    assert "strategy_entry.paper_positions" in s
    assert "strategy_entry.paper_orders" in s
    assert "CASCADE" not in s.split("TRUNCATE TABLE", 1)[1]
    assert "DROP TABLE" not in s
    assert "DISABLE TRIGGER" not in s
    assert "DELETE FROM" not in s
    assert "runtime.position_ownership" in s
    assert "execution_request_id IS NOT NULL" in s
    assert "pg_try_advisory_xact_lock" in s
    assert "ACCESS EXCLUSIVE MODE NOWAIT" in s
    assert "SET TRANSACTION READ ONLY" in s


class _SqlCursor:
    def __init__(
        self,
        *,
        after: bool = False,
        has_real_execution: bool = False,
        external_fk: bool = False,
        extra_open: bool = False,
    ) -> None:
        self.after = after
        self.has_real_execution = has_real_execution
        self.external_fk = external_fk
        self.extra_open = extra_open
        self.query = ""
        self.queries: list[str] = []

    def execute(self, statement: str, _params: Any = None) -> None:
        self.query = statement
        self.queries.append(statement)

    def fetchall(self) -> list[tuple[Any, ...]]:
        if "FROM pg_constraint" in self.query:
            result = sorted(MODULE.INBOUND_FOREIGN_KEYS)
            if self.external_fk:
                result.append(("runtime.position_ownership", MODULE.ORDERS, "x"))
            return result
        if "FROM pg_trigger" in self.query:
            return []
        if "FROM pg_class" in self.query:
            return sorted([
                (MODULE.EVENTS, "postgres", 2908160),
                (MODULE.POSITIONS, "postgres", 3842048),
                (MODULE.ORDERS, "postgres", 794624),
            ])
        if "SELECT external_symbol" in self.query:
            return [(s,) for s in MODULE.RETIRED_OPEN_SYMBOLS]
        raise AssertionError("Unexpected fetchall")

    def fetchone(self) -> tuple[Any, ...]:
        if "current_user,session_user" in self.query:
            return ("cripta", "cripta", "cripta", False)
        if "SELECT count(*) FROM " in self.query:
            for table, count in MODULE.COUNTS.items():
                if table in self.query:
                    return (0 if self.after else count,)
        if "state='OPEN'" in self.query:
            return (
                5 if self.extra_open else 4, 98, 0, 0, 0,
            )
        if "execution_request_id IS NOT NULL" in self.query:
            return (1 if self.has_real_execution else 0, 0, 102, 76, 0)
        if "JOIN runtime.position_ownership" in self.query:
            return (0,)
        if "LEFT JOIN strategy_entry.paper_positions" in self.query:
            return (0,)
        raise AssertionError("Unexpected fetchone")


def test_exact_paper_simulation_is_proven_independent() -> None:
    cursor = _SqlCursor()
    result = MODULE.inspect(cursor, empty=False)
    assert result["rows"] == MODULE.COUNTS
    assert result["abandoned_open_virtual_positions"] == 4
    assert result["real_exchange_execution_links"] == 0
    assert result["inbound_foreign_keys"] == 3
    assert all(x.lstrip().upper().startswith("SELECT") for x in cursor.queries)


@pytest.mark.parametrize(
    "drift",
    ["has_real_execution", "external_fk", "extra_open"],
)
def test_any_financial_or_scope_drift_stops_before_mutation(
    drift: str,
) -> None:
    cursor = _SqlCursor(**{drift: True})
    with pytest.raises(RuntimeError, match="R1_PAPER_642_BLOCKED"):
        MODULE.inspect(cursor, empty=False)
    assert all(x.lstrip().upper().startswith("SELECT") for x in cursor.queries)


def test_post_truncate_rechecks_only_existing_schema() -> None:
    cursor = _SqlCursor(after=True)
    result = MODULE.inspect(cursor, empty=True)
    assert set(result["rows"]) == set(MODULE.COUNTS)
    assert all(n == 0 for n in result["rows"].values())
    assert result["inbound_foreign_keys"] == 3
