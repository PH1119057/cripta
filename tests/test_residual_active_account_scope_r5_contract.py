from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONSUMER = (ROOT / "operations/connectivity/universal_entry_consumer.py").read_text(encoding="utf-8")
PRIVATE = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
DASHBOARD = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")


def test_universal_entry_selection_is_exact_account_proven() -> None:
    assert "WHERE r.trading_account_id=%s" in CONSUMER
    assert "r.identity_status='PROVEN'" in CONSUMER
    assert "(TRADING_ACCOUNT_ID,)" in CONSUMER


def test_expired_request_sweep_never_claims_legacy_unknown_for_current_account() -> None:
    assert "c.trading_account_id=%s" in CONSUMER
    assert "c.identity_status='PROVEN'" in CONSUMER
    assert "(TRADING_ACCOUNT_ID, TRADING_ACCOUNT_ID, now.astimezone(UTC))" in CONSUMER


def test_private_runtime_slippage_uses_only_current_proven_account_events() -> None:
    marker = "def observed_adverse_slippage"
    block = PRIVATE[PRIVATE.index(marker):PRIVATE.index(marker)+1800]
    assert "FROM runtime.private_events" in block
    assert "trading_account_id=%s" in block
    assert "identity_status='PROVEN'" in block
    assert "(TRADING_ACCOUNT_ID,)" in block


def test_dashboard_liquidity_risk_uses_only_current_proven_account_events() -> None:
    marker = "def execution_liquidity_risks"
    block = DASHBOARD[DASHBOARD.index(marker):DASHBOARD.index(marker)+2200]
    assert "FROM runtime.private_events" in block
    assert "trading_account_id=%s" in block
    assert "identity_status='PROVEN'" in block
    assert "(TRADING_ACCOUNT_ID,)" in block
