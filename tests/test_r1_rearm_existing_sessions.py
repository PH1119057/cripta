from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/bybit_workbench/r1_micro_live_control.py"


def test_owner_rearm_retires_prior_active_sessions_before_new_sessions():
    source = SOURCE.read_text(encoding="utf-8")
    arm = source[source.index("def arm("):source.index("\ndef disarm(")]
    retire = arm.index("UPDATE control.live_arm_sessions")
    new_session = arm.index("INSERT INTO control.live_arm_sessions")
    assert retire < new_session
    assert "state='ACTIVE'" in arm[retire:new_session]
    assert "state='CLOSED'" in arm[retire:new_session]
    assert "deactivated_at=%s" in arm[retire:new_session]
    assert 'gate is None or bool(gate[0])' in arm[:retire]
    assert "_assert_no_other_execution_permission(connection)" in arm[:retire]
    assert "evaluate_live_arm(" in arm[:retire]


def test_rearm_does_not_disarm_execution_permission_or_cancel_exchange_orders():
    source = SOURCE.read_text(encoding="utf-8")
    arm = source[source.index("def arm("):source.index("\ndef disarm(")]
    assert "cancel_bot_owned_pending_entry_orders" not in arm
    assert "SET enabled=false" not in arm
    assert "DELETE FROM control.live_arm_sessions" not in arm
