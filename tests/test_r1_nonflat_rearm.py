"""Owner decision: NEW Entry re-arm cannot require a flat Exchange account."""
from pathlib import Path


def test_nonflat_rearm_keeps_reconciliation_and_slot_controls():
    source = Path("operations/dashboard/app.py").read_text()
    start = source.index("def _u6_prepare_r1_prearm_evidence(")
    end = source.index("\ndef ", start + 1)
    prearm = source[start:end]
    assert "R1 PREARM: exchange is not flat" not in prearm
    assert "R1 PREARM: reconciliation missing/failed" in prearm
    assert "R1 PREARM: reconciliation stale" in prearm
    assert "R1 PREARM: wallet snapshot stale" in prearm
    assert "R1 PREARM: position mode cohort incomplete" in prearm
    assert "R1 PREARM: physical slot claim contract missing" in prearm
    assert "R1 PREARM: capital reservation contract missing" in prearm
    assert "R1 PREARM: exact R1 policy mismatch" in prearm


def test_owner_decision_recorded_in_canon():
    contour = Path("docs/TRADING_CONTOUR_RU.md").read_text()
    index = Path("docs/DOCUMENTATION_INDEX_RU.md").read_text()
    assert "## 4.10 Owner decision 2026-10-09" in contour
    assert "Existing Exchange positions" in contour
    assert "TRADING_CONTOUR §4.10" in index
