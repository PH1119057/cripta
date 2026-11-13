"""Fail-closed identity FK recovery for an interrupted scoped scratch restore."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

FILE = (
    Path(__file__).resolve().parents[1] / "operations" / "monitoring"
    / "p90_market_minutes_finish_restore_642.py"
)
spec = importlib.util.spec_from_file_location("minutes_fk_restore", FILE)
assert spec is not None and spec.loader is not None
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def test_exact_failed_job_continuation_only() -> None:
    assert recovery.MARKET_ARCHIVE_BYTES == 248268366
    assert len(recovery.MARKET_SHA256) == 64
    assert len(recovery.EXPECTED_MARKET_HASH) == 64
    assert Path(
        "/data/cripta/research/p90_market_minutes_r12_proof_642"
    ) == recovery.ROOT
    assert recovery.REF_ARCHIVE.parent == recovery.ROOT
    assert recovery.SCRATCH_RESULT == recovery.ROOT / "proof_result.json"


def test_both_market_fk_dependencies_are_explicit() -> None:
    assert set(recovery.FKS) == {
        (
            "market_minutes_instrument_id_venue_id_product_category_ext_fkey",
            "FOREIGN KEY (instrument_id, venue_id, product_category, "
            "external_symbol) REFERENCES identity.instruments(instrument_id, "
            "venue_id, product_category, external_symbol)",
        ),
        (
            "market_minutes_market_source_id_venue_id_fkey",
            "FOREIGN KEY (market_source_id, venue_id) "
            "REFERENCES identity.market_sources(market_source_id, venue_id)",
        ),
    }
    assert set(recovery.REFERENCE_TABLES) == {
        ("identity.venues", "venue_id", 1),
        ("identity.market_sources", "market_source_id", 1),
        ("identity.instruments", "instrument_id", 40),
    }


def test_no_unapproved_production_sql_write_api() -> None:
    code = FILE.read_text(encoding="utf-8")
    assert "ALTER TABLE ONLY analytics.market_minutes" in code
    assert "scratch=True" in code
    assert "PROTECTED_CONTEXT" not in code
    assert "RESTORED_MARKET_MINUTE_ROW_SHA_DIFFERS" in code
    assert "SCOPED_MARKET_MINUTES_RESTORE_PASS" in code
    for unsafe in ("DELETE FROM", "TRUNCATE TABLE", "DROP TABLE"):
        assert unsafe not in code


def test_actor_check_precedes_scratch_filesystem(monkeypatch) -> None:
    monkeypatch.setattr(recovery.os, "geteuid", lambda: 0, raising=False)
    with pytest.raises(RuntimeError, match="EXACT_CRIPTA_UNIX_ACTOR_REQUIRED"):
        recovery.filesystem_guard()
