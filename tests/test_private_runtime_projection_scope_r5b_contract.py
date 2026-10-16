from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")

def test_reconcile_does_not_globally_delete_hot_projection() -> None:
    assert 'connection.execute("DELETE FROM runtime.hot_positions")' not in SOURCE
    assert 'connection.execute("DELETE FROM runtime.hot_orders")' not in SOURCE
    assert "DELETE FROM runtime.hot_positions" in SOURCE
    assert "DELETE FROM runtime.hot_orders" in SOURCE
    assert "trading_account_id=%s" in SOURCE

def test_current_position_mode_cache_is_exact_account_proven() -> None:
    assert "FROM runtime.position_mode_states" in SOURCE
    assert "identity_status='PROVEN'" in SOURCE
    assert "(TRADING_ACCOUNT_ID, cutoff, symbols)" in SOURCE

def test_ws_terminal_deletes_preserve_other_accounts() -> None:
    assert SOURCE.count("OR identity_status='LEGACY_UNKNOWN'") >= 4
    assert "TRADING_ACCOUNT_ID" in SOURCE
