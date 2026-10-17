from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "src/bybit_workbench/lifecycle_supervisor.py").read_text(encoding="utf-8")

def test_private_lifecycle_scans_are_account_scoped_in_production_mode() -> None:
    assert SOURCE.count("self._trading_account_id") >= 20
    assert SOURCE.count("identity_status='PROVEN'") >= 12
    assert "LEGACY_UNKNOWN" not in SOURCE

def test_position_mode_lookup_has_no_legacy_identity_fallback() -> None:
    marker = "FROM runtime.position_mode_states"
    block = SOURCE[SOURCE.index(marker):SOURCE.index(marker)+900]
    assert "trading_account_id=%s" in block
    assert "identity_status='PROVEN'" in block
    assert "LEGACY_UNKNOWN" not in block

def test_exit_and_entry_fault_scans_are_scoped_through_durable_identity() -> None:
    assert "JOIN strategy_entry.execution_requests r" in SOURCE
    assert "r.trading_account_id=%s" in SOURCE
    assert "p.trading_account_id=%s" in SOURCE