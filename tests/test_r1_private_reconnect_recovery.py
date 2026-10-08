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


def test_account_generation_forces_exact_position_mode_probe_every_cycle() -> None:
    text = source()
    assert "force_refresh: bool = False" in text
    assert "if refresh_seconds > 0 and not force_refresh:" in text
    start = text.index("def reconcile(")
    body = text[start:text.index("def upsert_exchange_order_history(", start)]
    assert "force_refresh=True" in body
    assert "position_mode_refs" in body
    assert "runtime.account_state_generations" in body

def test_reconnect_auto_restore_verifies_exact_owner_arm_and_complete_inventory() -> None:
    text = source()
    start = text.index("def restore_r1_gate_after_verified_reconnect(")
    end = text.index("\ndef entry_runtime_readiness(", start)
    body = text[start:end]
    for token in (
        "control.live_arm_sessions",
        "state='ACTIVE'",
        "strategy_entry.execution_permissions",
        "strategy_entry.strategy_activations",
        'str(reconciliation[2]) != "reconnect"',
        "int(reconciliation[4]) != hot_positions",
        "int(reconciliation[5]) != hot_orders",
        "runtime.position_mode_states",
        'str(row[1]) != "ONE_WAY"',
        "PRIVATE_RECONNECT_RECOVERED_REASON",
        "'safety_recovery'",
    ):
        assert token in body


def test_process_restart_preserves_authorized_gate_and_resting_orders() -> None:
    text = source()
    body = text.split('def startup_live_safety(', 1)[1].split('def record_entry_decision(', 1)[0]
    assert 'disarm_new_entries(connection' not in body
    assert 'cancel_bot_owned_pending_entry_orders(' not in body
    assert 'refresh_recent_executions(connection, key, secret)' in body
    assert 'resolve_prestart_entry_commands(connection)' in body