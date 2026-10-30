"""Replay-safety contract for live_arm_evidence status migrations."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R1 = ROOT / "operations/sql/20261004_r1_live_arm_waiver.sql"
P90 = ROOT / "operations/sql/20261030_p90_portal_micro_live_waiver.sql"


def _statuses(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r"live_arm_evidence_status_check[\s\S]*?CHECK \(status IN \(([\s\S]*?)\)\)",
        text,
    )
    assert match is not None
    return set(re.findall(r"'([^']+)'", match.group(1)))


def test_historical_r1_status_constraint_is_replay_safe_for_p90_rows():
    old_statuses = _statuses(R1)
    current_statuses = _statuses(P90)
    assert old_statuses >= current_statuses
    assert "OWNER_WAIVED_FOR_P90_PORTAL_MICRO_LIVE" in old_statuses
