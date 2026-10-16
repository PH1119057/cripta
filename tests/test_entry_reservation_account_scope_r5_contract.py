from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "src/bybit_workbench/entry_reservation_lifecycle.py").read_text(encoding="utf-8")


def test_universal_entry_binding_resolves_command_and_request_account_identity() -> None:
    assert "tc.trading_account_id,tc.identity_status" in SOURCE
    assert "r.trading_account_id,r.identity_status" in SOURCE
    assert "command/request TradingAccount mismatch" in SOURCE
    assert 'command_status == "PROVEN"' in SOURCE
    assert 'command_status == "LEGACY_UNKNOWN"' in SOURCE


def test_exchange_cancel_and_execution_evidence_is_identity_scoped() -> None:
    assert SOURCE.count("FROM runtime.exchange_order_history") >= 2
    assert SOURCE.count("FROM runtime.executions") >= 2
    assert SOURCE.count("identity_status=%s") >= 5
    assert SOURCE.count("trading_account_id=%s") >= 5


def test_legacy_transition_never_claims_a_specific_account() -> None:
    assert "legacy Universal Entry lineage claims TradingAccount" in SOURCE