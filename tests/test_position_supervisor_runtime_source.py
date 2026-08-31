from pathlib import Path


def test_post_fill_supervisor_is_independent_from_entry_runtime() -> None:
    source = Path("operations/monitoring/position_supervisor.py").read_text(encoding="utf-8")
    assert "entry_shadow" not in source
    assert "ENTRY_STATUS" not in source
    assert "mayak_v2/status.json" in source


def test_supervisor_runtime_uses_joint_hold_and_supervisor_exit_only() -> None:
    source = Path("operations/monitoring/position_supervisor.py").read_text(encoding="utf-8")
    assert "dispatcher_hold_context" in source
    assert "early_loss_eligible" in source
    assert "protective_clean_break_against" in source
    assert '"internal_exit_reason": "EARLY_LOSS_PREVENTION"' in source
    assert "SupervisorState.WARNING" not in source
    assert "reduce_only" in source
    assert "place_order" not in source
