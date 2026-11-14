"""Issue #642: the public-fact cleanup inventory is strictly read-only."""
from __future__ import annotations

import importlib.util
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
