from __future__ import annotations

import re
from pathlib import Path

from bybit_workbench.live_arm_readiness import REQUIRED_LIVE_ARM_CHECKS

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "operations/sql/20260920_slot_admission_v1.sql"
FORWARD = ROOT / "operations/sql/20261007_live_arm_parity_gate.sql"
INSTALLER = ROOT / "operations/infrastructure/install_verified_release.sh"


def _check_code_tokens(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"CHECK\s*\(check_code\s+IN\s*\((.*?)\)\)",
        text,
        flags=re.DOTALL,
    )
    assert match is not None
    return set(re.findall(r"'([A-Z][A-Z0-9_]+)'", match.group(1)))


def test_live_arm_sql_vocabulary_matches_python_required_checks() -> None:
    expected = set(REQUIRED_LIVE_ARM_CHECKS)
    assert _check_code_tokens(BASE) == expected
    assert _check_code_tokens(FORWARD) == expected
    assert "PAPER_REAL_DECISION_PARITY" in expected


def test_live_arm_parity_forward_migration_is_transactional_and_replayed() -> None:
    migration = FORWARD.read_text(encoding="utf-8")
    installer = INSTALLER.read_text(encoding="utf-8")
    assert migration.lstrip().startswith("BEGIN;")
    assert migration.rstrip().endswith("COMMIT;")
    assert "DROP CONSTRAINT IF EXISTS live_arm_evidence_check_code_check" in migration
    assert "ADD CONSTRAINT live_arm_evidence_check_code_check" in migration
    assert "operations/sql/20261007_live_arm_parity_gate.sql" in installer
