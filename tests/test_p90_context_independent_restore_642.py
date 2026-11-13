"""Scoped independent PostgreSQL recovery contract tests, Issue #642."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

FILE = (
    Path(__file__).resolve().parents[1]
    / "operations" / "monitoring" / "p90_context_independent_restore_642.py"
)
spec = importlib.util.spec_from_file_location("context_recovery_642", FILE)
assert spec is not None and spec.loader is not None
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def test_isolated_scope_is_exact_and_fully_disclosed() -> None:
    assert recovery.TABLES == (
        "dispatcher_v2.coin_market_contexts",
        "mayak_v2.coin_market_contexts",
        "research_context.dispatcher_v2_event_links",
    )
    assert recovery.EXPECTED_LINKS == 718
    assert all(count == 450 for count, _ in recovery.EXPECTED.values())
    assert recovery.CLUSTER.parent == recovery.ROOT
    assert recovery.ROOT.parent == Path("/data/cripta/research")
    assert recovery.MIN_DATA_FREE >= 3 * 1024 * 1024 * 1024


def test_reviewed_script_is_nonprivileged_and_production_read_only() -> None:
    s = FILE.read_text(encoding="utf-8")
    assert "sudo " not in s
    assert "--no-owner" in s
    assert "--no-privileges" in s
    assert "--section=pre-data" in s
    assert "--section=data" in s
    assert "listen_addresses = ''" in s
    assert "full_cluster_restore" in s
    assert "SCOPED_INDEPENDENT_POSTGRES_RESTORE_PASS" in s
    assert "PRODUCTION_SOURCE_CHANGED_DURING_BACKUP" in s
    for bad in ("DROP TABLE", "TRUNCATE", "DELETE FROM", "ALTER ROLE"):
        assert bad not in s


def test_guard_refuses_unapproved_fs_actor(monkeypatch) -> None:
    monkeypatch.setattr(recovery.os, "geteuid", lambda: 0)
    with pytest.raises(RuntimeError, match="UNIX_ACTOR_MUST_BE_CRIPTA"):
        recovery.guard_filesystem()


def test_sha_and_digest_proof_path_is_fixed() -> None:
    assert recovery.ARCHIVE.parent == recovery.ROOT
    assert recovery.RESULT.parent == recovery.ROOT
    assert recovery.PG_LOG.parent == recovery.ROOT
    assert recovery.SCRATCH_PORT == "55441"
