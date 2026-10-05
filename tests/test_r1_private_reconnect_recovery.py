from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "operations" / "connectivity" / "private_runtime.py"


def source() -> str:
    return PRIVATE.read_text(encoding="utf-8")


def test_private_reconnect_is_temporarily_fail_closed_not_permanent_owner_rearm() -> None:
    text = source()
    assert 'PRIVATE_RECONNECT_GATE_REASON = "private WS reconnect: verification pending"' in text
    assert "private WS reconnect: owner re-arm required" not in text
    assert "def restore_r1_gate_after_verified_reconnect(" in text
    assert "gate_was_open=restore_gate_after_reconnect" in text
    assert "gate_restored=restored" in text


def test_reconnect_forces_fresh_position_mode_probe() -> None:
    text = source()
    assert "force_refresh: bool = False" in text
    assert "if refresh_seconds > 0 and not force_refresh:" in text
    assert 'force_refresh=reason != "periodic"' in text


def test_reconnect_auto_restore_is_scoped_to_exact_flat_owner_armed_r1() -> None:
    text = source()
    start = text.index("def restore_r1_gate_after_verified_reconnect(")
    end = text.index("\ndef entry_runtime_readiness(", start)
    body = text[start:end]
    for token in (
        "if hot_positions or hot_orders:",
        "control.live_arm_sessions",
        "state='ACTIVE'",
        "strategy_entry.execution_permissions",
        "strategy_entry.strategy_activations",
        'str(reconciliation[2]) != "reconnect"',
        "int(reconciliation[4]) != 0",
        "int(reconciliation[5]) != 0",
        "runtime.position_mode_states",
        'str(row[1]) != "ONE_WAY"',
        "PRIVATE_RECONNECT_RECOVERED_REASON",
        "'safety_recovery'",
    ):
        assert token in body


def test_process_restart_still_requires_explicit_owner_rearm() -> None:
    text = source()
    assert 'disarm_new_entries(connection, "restart: owner re-arm required")' in text
