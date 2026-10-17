from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")

def test_command_worker_queue_is_exact_proven_account_scoped_after_r10b() -> None:
    assert "WHERE state='queued'" in SOURCE
    assert "LEGACY_UNKNOWN" not in SOURCE
    assert "identity_status='PROVEN'" in SOURCE
    assert "TRADING_ACCOUNT_ID" in SOURCE

def test_restart_non_entry_mutation_is_account_scoped() -> None:
    assert "RESTART_DISARMED: mutation not replayed; exchange truth reconciled" in SOURCE
    assert "PROCESS_STARTED_AT_MS, TRADING_ACCOUNT_ID" in SOURCE

def test_ownership_and_exit_attribution_use_scoped_evidence_and_stored_position_key() -> None:
    assert "entry_execution_request_id,exchange_position_key" in SOURCE
    assert '"exchange_position_key": str(row[10])' in SOURCE
    assert "LEGACY_UNKNOWN" not in SOURCE

def test_orphan_recovery_scopes_reservation_slot_order_and_execution_evidence() -> None:
    assert "c.trading_account_id=%s" in SOURCE
    assert "s.trading_account_id=%s" in SOURCE
    assert "h.trading_account_id=%s" in SOURCE
    assert "runtime.exchange_order_history" in SOURCE
