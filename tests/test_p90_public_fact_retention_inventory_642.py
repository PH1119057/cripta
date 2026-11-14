"""Issue #642: the public-fact cleanup inventory is strictly read-only."""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "operations/monitoring/p90_public_fact_retention_inventory_642.py"
)
SPEC = importlib.util.spec_from_file_location("p90_fact_inventory_642", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_target_scope_is_fixed_and_public_only() -> None:
    assert MODULE.TARGETS == (
        ("analytics.public_market_source_seconds", "observed_second", 24),
        ("analytics.market_publications", "as_of_second", 24),
        ("analytics.public_forming_minute_observations", "received_at", 24),
        ("dispatcher_v2.trading_capacity_snapshots", "observed_at", 168),
    )
    assert MODULE.MAX_PINNED_PUBLICATIONS == 10_000
    assert "runtime.account_state_generations" not in str(MODULE.TARGETS)
    assert "analytics.native_public_trade_events" not in str(MODULE.TARGETS)


def test_script_has_no_apply_mode_or_destructive_sql() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    compile(source, str(SCRIPT), "exec")
    assert "DELETE FROM" not in source
    assert "TRUNCATE TABLE" not in source
    assert "VACUUM (" not in source
    assert "DROP TABLE" not in source
    assert "--apply" not in source
    assert 'required=True' in source
    assert "connection.read_only = True" in source
    assert "default_transaction_read_only=on" in source
    assert "P90_PUBLICATION_JSON_REF_MISSING" in source
    assert "forming_minute_refs" in source
    assert "rest_observation_refs" in source


class _IdentityCursor:
    def __init__(self, identity: tuple[Any, ...]) -> None:
        self.identity = identity
        self.queries: list[str] = []
        self.rows_read = 0

    def __enter__(self) -> _IdentityCursor:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def execute(self, query: str) -> None:
        self.queries.append(query)

    def fetchone(self) -> tuple[Any, ...]:
        self.rows_read += 1
        if self.rows_read == 1:
            return self.identity
        return (252113,)


class _Connection:
    def __init__(self, identity: tuple[Any, ...]) -> None:
        self.cursor_instance = _IdentityCursor(identity)

    def cursor(self) -> _IdentityCursor:
        return self.cursor_instance


@pytest.mark.parametrize(
    "identity",
    [
        ("postgres", "cripta", False, "on"),
        ("cripta", "another_database", False, "on"),
        ("cripta", "cripta", True, "on"),
        ("cripta", "cripta", False, "off"),
    ],
)
def test_wrong_actor_database_or_rw_connection_fails_closed(
    identity: tuple[Any, ...],
) -> None:
    with pytest.raises(RuntimeError, match="READONLY_IDENTITY_GATE_FAILED"):
        MODULE.inspect(_Connection(identity))


def test_inspect_is_explicitly_not_deletion_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        MODULE,
        "_one_table",
        lambda _cursor, table, col, hours: {
            "table": table,
            "time_column": col,
            "cutoff_hours": hours,
            "deletable_rows": None,
        },
    )
    monkeypatch.setattr(
        MODULE,
        "_publication_pins",
        lambda _cursor: {"pinned_publication_ids": [45018, 94424]},
    )
    monkeypatch.setattr(
        MODULE,
        "_possible_retention_plan",
        lambda _cursor, _pins, _tables: {
            "candidate_status": "PRELIMINARY_ONLY_NO_DELETE_APPROVAL",
        },
    )
    con = _Connection(("cripta", "cripta", False, "on"))
    result = MODULE.inspect(con)

    assert result["status"] == "READ_ONLY_EVIDENCE_ONLY"
    assert result["sql_mutation"] == "NOT_IMPLEMENTED"
    assert result["backup_created"] is False
    assert result["prepared_for_delete"] is False
    assert result["capacity_old_unreferenced_by_direct_fk"] == 252113
    assert len(result["tables"]) == 4
    assert all("deletable_rows" in x for x in result["tables"])
    assert "full_p90_and_json_reference_closure" in result[
        "required_before_apply"
    ]
    assert all(
        query.lstrip().upper().startswith("SELECT")
        for query in con.cursor_instance.queries
    )

class _PlanCursor:
    def __init__(self, replies: list[tuple[object, ...]]) -> None:
        self.replies = iter(replies)
        self.queries: list[str] = []

    def execute(self, query: str, _params: object = None) -> None:
        self.queries.append(query)

    def fetchone(self) -> tuple[object, ...]:
        return next(self.replies)


def _plan_table_rows() -> list[dict[str, object]]:
    return [
        {
            "table": "analytics.public_market_source_seconds",
            "older_than_cutoff_rows": 1_199_020,
        },
        {
            "table": "analytics.public_forming_minute_observations",
            "older_than_cutoff_rows": 188_683,
        },
    ]


def test_provisional_retention_uses_all_retained_publication_json_pins() -> None:
    cursor = _PlanCursor([
        (datetime(2026, 11, 14, 4, 50, tzinfo=UTC),),
        (102_501,),
        (459_682, 0, 134),
        (77_622, 0, 20),
    ])
    out = MODULE._possible_retention_plan(
        cursor,
        {"pinned_publication_ids": [45018, 94424]},
        _plan_table_rows(),
    )
    assert out["old_unpinned_publication_candidates"] == 102_501
    assert out["old_unreferenced_second_candidates"] == 1_198_886
    assert out["old_unreferenced_forming_candidates"] == 188_663
    assert out["actual_reclaim_bytes"] is None
    assert out["capacity_financial_retention_gate"] == "NOT_PROVEN"
    assert out["candidate_status"] == "PRELIMINARY_ONLY_NO_DELETE_APPROVAL"
    assert all("DELETE FROM" not in q.upper() for q in cursor.queries)
    assert any("jsonb_each(p.compact_second_refs)" in q for q in cursor.queries)
    assert any("jsonb_each(p.forming_minute_refs)" in q for q in cursor.queries)


@pytest.mark.parametrize(
    "compact_missing,forming_missing",
    [(1, 0), (0, 1)],
)
def test_no_retention_candidate_when_kept_publication_has_dangling_refs(
    compact_missing: int,
    forming_missing: int,
) -> None:
    cursor = _PlanCursor([
        (datetime(2026, 11, 14, 4, 50, tzinfo=UTC),),
        (102_501,),
        (459_682, compact_missing, 134),
        (77_622, forming_missing, 20),
    ])
    with pytest.raises(RuntimeError, match="DANGLING_REF"):
        MODULE._possible_retention_plan(
            cursor,
            {"pinned_publication_ids": [45018]},
            _plan_table_rows(),
        )
