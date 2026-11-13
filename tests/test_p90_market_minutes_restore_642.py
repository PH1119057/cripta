"""Exact read-only PostgreSQL recovery contract for P90 market minutes."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1] / "operations"
    / "monitoring" / "p90_market_minutes_restore_642.py"
)
spec = importlib.util.spec_from_file_location("p90_minutes_r12_642", SCRIPT)
assert spec is not None and spec.loader is not None
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)


def test_exact_scope_and_cardinality() -> None:
    assert proof.TABLE == "analytics.market_minutes"
    assert proof.EXPECTED_ROWS == 483770
    assert proof.ROWS_PER_SYMBOL == 69110
    assert proof.EXPECTED_SOURCE == "ms-bybit-mainnet-public-v1"
    assert set(proof.PROTECTED) == {
        "APTUSDT", "ARBUSDT", "BTCUSDT", "DOTUSDT", "ETHUSDT",
        "INJUSDT", "LTCUSDT",
    }
    assert proof.MIN_DATA_FREE >= 6 * 1024**3
    assert proof.ROOT.parent == Path("/data/cripta/research")
    assert proof.ARCHIVE.parent == proof.ROOT
    assert proof.PORT == "55442"


def test_hash_is_streamed_and_source_read_only() -> None:
    src = SCRIPT.read_text(encoding="utf-8")
    assert "COPY (SELECT row_to_json(t)::text" in src
    assert "ORDER BY t.market_source_id, t.instrument_id, t.observed_minute" in src
    assert "sha.update(data)" in src
    assert "SCOPED_MARKET_MINUTES_RESTORE_PASS" in src
    assert "SOURCE_CHANGED_AFTER_DUMP" in src
    assert "RESTORED_ROWS_DIFFER_AFTER_SCRATCH_RESTART" in src
    assert "unix_socket_permissions = 0700" in src
    assert "listen_addresses = ''" in src
    assert "--no-owner" in src
    assert "--no-privileges" in src
    for forbidden in (
        "sudo ", "DROP TABLE", "DELETE FROM", "TRUNCATE TABLE",
        "VACUUM FULL", "DROP INDEX", "ALTER ROLE",
    ):
        assert forbidden not in src


def test_actor_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proof.os, "geteuid", lambda: 0, raising=False)
    with pytest.raises(RuntimeError, match="EXACT_CRIPTA_UNIX_ACTOR_REQUIRED"):
        proof.filesystem_preflight()


def test_scratch_is_not_prod() -> None:
    assert proof.SOCKET.parent == proof.ROOT
    assert proof.CLUSTER.parent == proof.ROOT
    assert proof.PROOF.parent == proof.ROOT
    assert "p90_market_minutes_r12_proof_642" in str(proof.ROOT)
