from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLOT_MIGRATION = ROOT / "operations/sql/20260920_slot_admission_v1.sql"
OBSERVER_MIGRATION = ROOT / "operations/sql/20261006_observer_runtime_fault_v1.sql"


def _fault_code_tokens(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    block = text.split(
        "ADD CONSTRAINT lifecycle_faults_fault_code_check", 1
    )[1].split("));", 1)[0]
    return set(re.findall(r"'([A-Z][A-Z0-9_]+)'", block))


def test_replayed_fault_constraint_migrations_accept_same_current_tokens() -> None:
    slot_tokens = _fault_code_tokens(SLOT_MIGRATION)
    observer_tokens = _fault_code_tokens(OBSERVER_MIGRATION)

    assert slot_tokens == observer_tokens
    assert "UNIVERSAL_ENTRY_OBSERVER_RUNTIME_ERROR" in slot_tokens
