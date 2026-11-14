"""Issue #642: SQL parity retirement must never touch P90 or finance."""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "operations/monitoring/p90_only_retired_parity_purge_642.py"
)
SPEC = importlib.util.spec_from_file_location("p90_only_retired_parity_642", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_exact_legacy_tables_and_no_wider_migration() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    compile(source, str(SCRIPT), "exec")
    assert MODULE.EXPECTED_EVENTS == 6854
    assert MODULE.EXPECTED_RUNS == 620
    assert MODULE.RETIRED_STRATEGY == "entry_v1_core"
    assert MODULE.RETIRED_VERSION == "1.0-live-first-touch"
    assert MODULE.RETIRED_STATUS == "NOT_COMPARABLE"
    assert "TRUNCATE TABLE strategy_entry.shadow_parity_events, " in source
    assert "strategy_entry.shadow_parity_runs" in source
    assert "CASCADE" not in source.split("TRUNCATE TABLE", 1)[1].split(
        "connection.execute", 1
    )[0]
    assert "DROP TABLE" not in source
    assert "DISABLE TRIGGER" not in source
    assert "DELETE FROM" not in source
    assert "VACUUM FULL" not in source
    assert "sudo" not in source
    assert "pg_try_advisory_xact_lock" in source
    assert "ACCESS EXCLUSIVE MODE NOWAIT" in source
    assert "SET TRANSACTION READ ONLY" in source


class _Cursor:
    def __init__(
        self,
        *,
        bad_inbound: bool = False,
        bad_count: bool = False,
        after: bool = False,
        bad_trigger: bool = False,
    ) -> None:
        self.queries: list[str] = []
        self.step = 0
        self.bad_inbound = bad_inbound
        self.bad_count = bad_count
        self.after = after
        self.bad_trigger = bad_trigger

    def execute(self, statement: str, _params: Any = None) -> None:
        self.queries.append(statement)

    def fetchone(self) -> tuple[Any, ...]:
        if self.step == 0:
            self.step += 1
            return ("cripta", "cripta", "cripta", False)
        if self.step == 1:
            self.step += 1
            if self.after:
                return (0, 0, 0, None, None)
            return (
                6855 if self.bad_count else 6854,
                620,
                0,
                datetime(2026, 9, 8, tzinfo=UTC),
                datetime(2026, 9, 12, tzinfo=UTC),
            )
        self.step += 1
        if self.after:
            return (0, 0, None, None)
        return (
            620,
            620,
            datetime(2026, 9, 8, tzinfo=UTC),
            datetime(2026, 9, 12, tzinfo=UTC),
        )

    def fetchall(self) -> list[tuple[Any, ...]]:
        count = len(self.queries)
        # Exactly one fetchall after FK, immutable-trigger and table-owner query.
        if count == 2:
            if self.bad_inbound:
                return [
                    (MODULE.EVENTS, MODULE.RUNS, MODULE.FK_NAME),
                    ("runtime.position_ownership", MODULE.RUNS, "OTHER"),
                ]
            return [(MODULE.EVENTS, MODULE.RUNS, MODULE.FK_NAME)]
        if count == 3:
            result = sorted(
                (table, trigger, "O") for table, trigger in MODULE.TRIGGERS
            )
            if self.bad_trigger:
                return [(table, trigger, "D") for table, trigger, _ in result]
            return result
        if count == 4:
            return [
                (MODULE.EVENTS, "postgres", 22691840),
                (MODULE.RUNS, "postgres", 655360),
            ]
        raise AssertionError("Unexpected read-only query")


def test_exact_scoped_v1_parity_is_candidate_only() -> None:
    cur = _Cursor()
    evidence = MODULE.evidence(cur, empty=False)
    assert evidence["event_rows"] == 6854
    assert evidence["parity_runs"] == 620
    assert evidence["foreign_keys"] == 1
    assert evidence["immutable_triggers_enabled"] == 3
    assert all(q.lstrip().upper().startswith("SELECT") for q in cur.queries)


@pytest.mark.parametrize(
    "scope",
    ["bad_inbound", "bad_count", "bad_trigger"],
)
def test_drift_fails_closed_without_sql_mutation(scope: str) -> None:
    cur = _Cursor(**{scope: True})
    with pytest.raises(RuntimeError, match="RETIRED_PARITY_642_BLOCKED"):
        MODULE.evidence(cur, empty=False)
    assert all(q.lstrip().upper().startswith("SELECT") for q in cur.queries)


def test_empty_poststate_validates_same_fk_and_immutable_triggers() -> None:
    evidence = MODULE.evidence(_Cursor(after=True), empty=True)
    assert evidence["event_rows"] == 0
    assert evidence["parity_runs"] == 0
    assert evidence["immutable_triggers_enabled"] == 3
