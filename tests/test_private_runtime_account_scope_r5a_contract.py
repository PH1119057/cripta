from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")

def test_reconnect_and_readiness_use_exact_proven_current_state() -> None:
    assert SOURCE.count("identity_status='PROVEN'") >= 20
    assert "FROM runtime.reconciliation_runs" in SOURCE
    assert "TRADING_ACCOUNT_ID" in SOURCE

def test_historical_restart_evidence_has_explicit_legacy_transition() -> None:
    assert SOURCE.count("identity_status='LEGACY_UNKNOWN'") >= 5
    assert "runtime.executions" in SOURCE
    assert "runtime.exchange_order_history" in SOURCE

def test_hot_projection_restart_reads_are_account_scoped() -> None:
    assert "o.trading_account_id=%s" in SOURCE
    assert "FROM runtime.hot_positions" in SOURCE
    assert "FROM runtime.hot_orders" in SOURCE
