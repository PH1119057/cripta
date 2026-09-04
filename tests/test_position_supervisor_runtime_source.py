from pathlib import Path


def test_post_fill_supervisor_is_independent_from_entry_runtime() -> None:
    source = Path("operations/monitoring/position_supervisor.py").read_text(encoding="utf-8")
    assert "entry_shadow" not in source
    assert "ENTRY_STATUS" not in source
    assert "mayak_v2/status.json" in source


def test_supervisor_is_information_only_and_exit_runtime_owns_automation() -> None:
    supervisor = Path("operations/monitoring/position_supervisor.py").read_text(
        encoding="utf-8"
    )
    exit_runtime = Path("operations/monitoring/exit_runtime.py").read_text(encoding="utf-8")
    assert "dispatcher_hold_context" in supervisor
    assert "POSITION_SUPERVISOR_INFORMATION_ONLY_V36" in supervisor
    assert "early_loss_eligible" not in supervisor
    assert "INSERT INTO runtime.trade_commands" not in supervisor
    assert "auto-be-" in exit_runtime
    assert "auto-trail-" in exit_runtime
    assert 'STRUCTURAL_BREAK_RULE = "NOT_PROVEN"' in exit_runtime
    assert "STRUCTURAL_EARLY_EXIT_ENABLED = False" in exit_runtime
    assert "EARLY_LOSS_PREVENTION" not in exit_runtime
