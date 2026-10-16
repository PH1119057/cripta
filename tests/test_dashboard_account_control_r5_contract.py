from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")
UNIT = (ROOT / "operations/systemd/cripta-dashboard.service").read_text(encoding="utf-8")

def test_dashboard_production_unit_pins_exact_trading_account() -> None:
    assert "Environment=CRIPTA_TRADING_ACCOUNT_ID=ta-" in UNIT
    assert 'TRADING_ACCOUNT_ID = os.environ.get("CRIPTA_TRADING_ACCOUNT_ID"' in APP
    assert "CRIPTA_TRADING_ACCOUNT_ID is required for Dashboard control/live state" in APP

def test_dashboard_readiness_and_prearm_private_facts_are_account_scoped() -> None:
    assert "FROM runtime.reconciliation_runs" in APP
    assert "FROM runtime.wallet_latest" in APP
    assert "FROM runtime.position_mode_states" in APP
    assert APP.count("trading_account_id=%s") >= 8
    assert APP.count("identity_status='PROVEN'") >= 10

def test_dashboard_control_commands_are_proven_for_exact_account() -> None:
    assert "requested_at_epoch_ms,trading_account_id,identity_status" in APP
    assert "VALUES(%s,%s,%s,%s,'queued',%s,%s,'PROVEN')" in APP
    assert "TRADING_ACCOUNT_ID" in APP
