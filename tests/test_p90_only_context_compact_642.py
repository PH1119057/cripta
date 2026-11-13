"""Tests for exact P90 linked-context PostgreSQL physical reclaim #642."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "operations"
    / "monitoring"
    / "p90_only_context_compact_642.py"
)
spec = importlib.util.spec_from_file_location("p90_context_compact_642", SOURCE)
assert spec is not None and spec.loader is not None
compact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compact)


def test_exact_two_relation_contract() -> None:
    assert set(compact.EXPECTED) == {
        "dispatcher_v2.coin_market_contexts",
        "mayak_v2.coin_market_contexts",
    }
    assert compact.REQUIRED_ROWS == 450
    assert compact.REQUIRED_LINK_ROWS == 718
    assert compact.MIN_ROOT_FREE_BYTES >= 700 * 1024 * 1024
    assert compact.MAX_REWRITTEN_BYTES <= 120 * 1024 * 1024


def test_no_unreviewed_physical_or_logical_mutation_api() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "VACUUM (FULL, ANALYZE)" in source
    for forbidden in [
        "DROP TABLE",
        "DROP INDEX",
        "CASCADE;",
        "ALTER TABLE",
        "TRUNCATE TABLE",
        "DELETE FROM",
        "UPDATE ",
        "DISABLE TRIGGER",
    ]:
        assert forbidden not in source
    assert "CREATE TABLESPACE" not in source
    assert "pg_advisory_unlock" in source
    assert "CHECKPOINT" in source


class FakeQuery:
    def __init__(self, value: tuple) -> None:
        self.value = value

    def execute(self, sql: str, params: object = None) -> FakeQuery:
        return self

    def fetchone(self) -> tuple:
        return self.value


def test_table_scope_refuses_any_unexpected_name() -> None:
    with pytest.raises(RuntimeError, match="TABLE_NOT_IN_EXACT_COMPACT_MANIFEST"):
        compact.table_evidence(FakeQuery(()), "analytics.market_minutes")


def test_table_retention_digest_is_mandatory() -> None:
    table = "dispatcher_v2.coin_market_contexts"
    wrong = (450, 450, "wrong", 2293170, 962027520, "postgres")
    with pytest.raises(RuntimeError, match="RETAINED_CONTEXT_EVIDENCE_DRIFT"):
        compact.table_evidence(FakeQuery(wrong), table)


def test_table_good_scope_is_read_only() -> None:
    table = "dispatcher_v2.coin_market_contexts"
    evidence = (
        450, 450, compact.EXPECTED[table], 2293170, 962027520, "postgres",
    )
    result = compact.table_evidence(FakeQuery(evidence), table)
    assert result["rows"] == 450
    assert result["relation_bytes"] == 962027520


def test_unverified_actor_is_refused() -> None:
    fake = FakeQuery(("cripta", "cripta", "cripta", False))
    with pytest.raises(RuntimeError, match="EXACT_POSTGRES_DB_ROLE_REQUIRED"):
        compact.verify_role(fake, applying=True)
