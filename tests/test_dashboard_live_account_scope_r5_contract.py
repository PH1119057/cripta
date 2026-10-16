from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "operations/dashboard/app.py").read_text(encoding="utf-8")


def test_live_wallet_positions_and_orders_require_exact_proven_account() -> None:
    assert "FROM runtime.wallet_latest" in APP
    assert "FROM runtime.hot_positions" in APP
    assert "FROM runtime.hot_orders" in APP
    assert APP.count("trading_account_id=%s") >= 18
    assert APP.count("identity_status='PROVEN'") >= 22


def test_live_historical_private_evidence_preserves_explicit_legacy_transition() -> None:
    assert "runtime.exchange_order_history h" in APP
    assert "runtime.executions x" in APP
    assert "identity_status='LEGACY_UNKNOWN'" in APP
    assert "JOIN runtime.capital_reservations r" in APP


def test_dashboard_lifecycle_and_exit_history_are_scoped_through_position_or_account_identity() -> None:
    assert "JOIN runtime.position_ownership o" in APP
    assert "FROM analyst.trade_lifecycles l" in APP
    assert "o.trading_account_id=%s" in APP
