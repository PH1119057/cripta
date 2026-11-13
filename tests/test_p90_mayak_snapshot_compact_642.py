"""Safe exact 7883-row MAYAK snapshot R12 physical-only contract."""
from __future__ import annotations

import importlib.util
from pathlib import Path

FILE = (
    Path(__file__).resolve().parents[1] / "operations"
    / "monitoring" / "p90_mayak_snapshot_compact_642.py"
)
spec = importlib.util.spec_from_file_location("mayak_snapshot_compact_642", FILE)
assert spec is not None and spec.loader is not None
compact = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compact)


def test_exact_physical_scope_and_reserves() -> None:
    assert compact.TABLE == "mayak_v2.snapshots"
    assert compact.ROWS == 7883
    assert compact.PINNED == 450
    assert compact.INDEXES == 3
    assert compact.FK_COUNT == 9
    assert compact.MIN_ROOT_FREE >= 2 * 1024**3
    assert compact.MAX_WAL_BEFORE <= 768 * 1024**2
    assert compact.MIN_RELATION_BEFORE >= 900 * 1024**2
    assert compact.MIN_POST_FREE >= 512 * 1024**2
    assert Path(
        "/usr/local/libexec/cripta-p90-mayak-snapshot-proof-642.json"
    ) == compact.PROOF_FILE


def test_all_nine_protected_referencing_tables() -> None:
    assert len(compact.REFERENCES) == 9
    assert sum(row[2] for row in compact.REFERENCES) == 1800
    assert len([r for r in compact.REFERENCES if r[2] == 450]) == 4
    assert len([r for r in compact.REFERENCES if r[2] == 0]) == 5
    assert compact.LOCK_KEY == "cripta-mayak-snapshot-physical-r12-642"


def test_only_supported_physical_rewrite_no_logical_deletions() -> None:
    src = FILE.read_text(encoding="utf-8")
    for required in (
        "VACUUM (FULL, ANALYZE) mayak_v2.snapshots",
        "COPY (SELECT row_to_json(t)::text",
        "ORDER BY t.id",
        "SCOPED_MAYAK_SNAPSHOT_RESTORE_PASS",
        "POST_REWRITE_ROWS_OR_FK_LINKS_DIFFER",
        "ACTUAL_ROWS_OR_450_FK_LINKS_DIFFER_FROM_RESTORE",
    ):
        assert src.count(required) >= 1
    for unsafe in (
        "sudo ", "DELETE FROM ", "TRUNCATE TABLE ",
        "DROP TABLE ", "DROP INDEX ", "ALTER TABLE ",
        "CASCADE;",
    ):
        assert src.count(unsafe) == 0


def test_strict_canonical_scoped_manifest() -> None:
    assert Path("/usr/local/libexec") == compact.PROOF_FILE.parent
    assert compact.ROW_COPY.count("mayak_v2.snapshots") == 1
