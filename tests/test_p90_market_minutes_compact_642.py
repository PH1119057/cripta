"""Fail-closed exact minute-table R12 physical-compaction contract."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

FILE = (
    Path(__file__).resolve().parents[1] / "operations" / "monitoring"
    / "p90_market_minutes_compact_642.py"
)
spec = importlib.util.spec_from_file_location("p90_minutes_compact_642", FILE)
assert spec is not None and spec.loader is not None
compact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compact)


def test_relation_and_disk_scope() -> None:
    assert compact.TABLE == "analytics.market_minutes"
    assert compact.ROWS == 483770
    assert compact.INDEXES == 3
    assert compact.ROWS_PER_SYMBOL == 69110
    assert compact.MIN_FREE_BEFORE_BYTES >= 2.5 * 1024**3
    assert compact.MAX_WAL_BEFORE_BYTES <= 768 * 1024**2
    assert compact.PROOF_FILE == Path(
        "/usr/local/libexec/cripta-p90-market-minutes-proof-642.json"
    )
    assert set(compact.PROTECTED) == {
        "APTUSDT", "ARBUSDT", "BTCUSDT", "DOTUSDT", "ETHUSDT",
        "INJUSDT", "LTCUSDT",
    }


def test_no_deletion_and_exact_rewrite_only() -> None:
    src = FILE.read_text(encoding="utf-8")
    assert src.count("VACUUM (FULL, ANALYZE) analytics.market_minutes") >= 1
    assert src.count("COPY (SELECT row_to_json(t)::text") >= 1
    assert src.count("ORDER BY t.market_source_id, t.instrument_id, t.observed_minute") >= 1
    assert src.count("SCOPED_MARKET_MINUTES_RESTORE_PASS") >= 1
    assert src.count("CURRENT_SOURCE_DIFFERS_FROM_RESTORED_PROOF") >= 1
    assert src.count("PROTECTED_ROWS_CHANGED_AFTER_REWRITE") >= 1
    for bad in (
        "sudo ", "DELETE FROM ", "TRUNCATE TABLE ", "DROP TABLE ",
        "DROP INDEX ", "ALTER TABLE ", "CASCADE;",
    ):
        assert bad not in src


class FakeQuery:
    def __init__(self, actor: tuple) -> None:
        self.actor = actor
        self.executed: list[str] = []

    def execute(self, sql: str, params: object = None) -> FakeQuery:
        self.executed.append(sql)
        return self

    def fetchone(self) -> tuple:
        return self.actor


def test_refuses_unprivileged_db_actor_before_mutation() -> None:
    fake = FakeQuery(("cripta", "cripta", "cripta", False))
    with pytest.raises(RuntimeError, match="POSTGRES_DATABASE_ACTOR_REQUIRED"):
        compact.gate(fake, applying=True)
    assert all(sql.count("VACUUM") == 0 for sql in fake.executed)


def test_hashes_are_streaming_and_no_dynamic_relation() -> None:
    assert compact.ROW_HASH_QUERY.count("analytics.market_minutes") == 1
    assert FILE.read_text(encoding="utf-8").count("sha256") >= 1
    assert compact.LOCK_KEY == "cripta-market-minutes-r12-642"
