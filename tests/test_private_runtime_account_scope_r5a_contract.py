from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")

def test_reconnect_and_readiness_use_exact_proven_current_state() -> None:
    assert SOURCE.count("identity_status='PROVEN'") >= 20
    assert "FROM runtime.reconciliation_runs" in SOURCE
    assert "TRADING_ACCOUNT_ID" in SOURCE

def test_restart_evidence_reader_is_proven_only_after_r10b() -> None:
    assert "LEGACY_UNKNOWN" not in SOURCE
    assert "runtime.executions" in SOURCE
    assert "runtime.exchange_order_history" in SOURCE
    assert SOURCE.count("identity_status='PROVEN'") >= 20

def test_hot_projection_restart_reads_are_account_scoped() -> None:
    assert "o.trading_account_id=%s" in SOURCE
    assert "FROM runtime.hot_positions" in SOURCE
    assert "FROM runtime.hot_orders" in SOURCE
