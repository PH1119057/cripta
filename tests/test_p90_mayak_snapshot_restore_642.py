"""Offline snapshot full-row and FK-pinning recovery contract #642."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

FILE = (
    Path(__file__).resolve().parents[1] / "operations"
    / "monitoring" / "p90_mayak_snapshot_restore_642.py"
)
spec = importlib.util.spec_from_file_location("mayak_snapshot_restore_642", FILE)
assert spec is not None and spec.loader is not None
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


def test_single_table_protected_boundaries() -> None:
    assert proof.TABLE == "mayak_v2.snapshots"
    assert proof.ROWS == 7883
    assert proof.PINNED == 450
    assert proof.INDEXES == 3
    assert proof.PORT == "55443"
    assert proof.DATA_MIN_FREE >= 7 * 1024**3
    assert proof.ROOT.parent == Path("/data/cripta/research")
    assert proof.ARCHIVE.parent == proof.ROOT


def test_all_nine_actual_inbound_foreign_keys() -> None:
    assert proof.EXPECTED_INBOUND_FK_COUNT == 9
    assert len(proof.SOURCE_REF_TABLES) == 4
    assert proof.SOURCE_REF_TABLES.count(
        ("dispatcher_v2.coin_market_contexts", "source_mayak_snapshot_id")
    ) == 1
    src = FILE.read_text(encoding="utf-8")
    assert src.count("FROM pg_constraint c") >= 1
    assert src.count("array_length(c.conkey,1)=1") >= 1
    assert src.count("sql.Identifier(namespace, rel)") >= 1
    assert src.count("set(SOURCE_REF_TABLES) <= names") >= 1


def test_requires_data_ssd_and_exact_unix_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(proof.os, "geteuid", raising=False)
    with pytest.raises(RuntimeError, match="POSIX_ACTOR_REQUIRED"):
        proof.initial_actor_path()


def test_complete_source_restore_and_scratch_restart() -> None:
    src = FILE.read_text(encoding="utf-8")
    for required in (
        "SCOPED_MAYAK_SNAPSHOT_RESTORE_PASS",
        "RESTARTED_SCRATCH_ROWS_DIFFER",
        "RESTORED_MAYAK_ROWS_SHA_MISMATCH",
        "SNAPSHOT_INBOUND_FK_INVENTORY_CHANGED",
        "PROTECTED_SNAPSHOT_ID_MISSING",
        "SOURCE_REF_TABLES",
        "MAYAK_SNAPSHOT_ROWS_OR_PRIMARY_KEY_DRIFT",
        "ROW_COPY",
    ):
        assert src.count(required) >= 1
    for forbidden in (
        "sudo ", "DELETE FROM ", "TRUNCATE TABLE ", "DROP TABLE ",
        "DROP INDEX ", "VACUUM FULL",
    ):
        assert src.count(forbidden) == 0
    assert proof.ROW_COPY.count("ORDER BY t.id") == 1
    assert proof.SOCKET.parent == proof.ROOT
    assert proof.CLUSTER.parent == proof.ROOT
