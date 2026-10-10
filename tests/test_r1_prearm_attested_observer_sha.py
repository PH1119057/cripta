from __future__ import annotations

import ast
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "operations/dashboard/app.py"
OBSERVER = ROOT / "operations/monitoring/universal_entry_shadow.py"


def test_attested_observer_sha_matches_current_source() -> None:
    tree = ast.parse(DASHBOARD.read_text(encoding="utf-8"))
    assignment = next(
        n for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "R1_PARITY_ATTESTED_MODULE_SHA256" for t in n.targets)
    )
    hashes = ast.literal_eval(assignment.value)
    assert hashes["operations/monitoring/universal_entry_shadow.py"] == hashlib.sha256(
        OBSERVER.read_bytes()
    ).hexdigest()
    assert len(hashes) == 6


def test_observer_hotfix_changes_only_worker_cleanup_not_decision_conditions() -> None:
    tree = ast.parse(OBSERVER.read_text(encoding="utf-8"))
    f = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_run_observer_epoch"
    )
    source = ast.get_source_segment(OBSERVER.read_text(encoding="utf-8"), f)
    assert source is not None
    assert "if oi_thread.ident is not None:" in source
    assert "if trade_mirror_thread.ident is not None:" in source
    assert "trade_mirror_thread.join(timeout=2.0)" in source
    assert "oi_thread.join(timeout=2.0)" in source
