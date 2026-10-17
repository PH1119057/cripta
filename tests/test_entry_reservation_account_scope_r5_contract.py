from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "src/bybit_workbench/entry_reservation_lifecycle.py").read_text(
    encoding="utf-8"
)


def test_universal_entry_binding_requires_exact_proven_account_identity() -> None:
    assert "tc.trading_account_id,tc.identity_status" in SOURCE
    assert "r.trading_account_id,r.identity_status" in SOURCE
    assert "command/request TradingAccount mismatch" in SOURCE
    assert 'command_status != "PROVEN"' in SOURCE
    assert "R10 Universal Entry command/request must be PROVEN" in SOURCE
    assert "LEGACY_UNKNOWN" not in SOURCE


def test_exchange_cancel_and_execution_evidence_is_exact_proven_scope() -> None:
    assert SOURCE.count("FROM runtime.exchange_order_history") >= 2
    assert SOURCE.count("FROM runtime.executions") >= 2
    assert SOURCE.count("identity_status='PROVEN'") >= 5
    assert SOURCE.count("trading_account_id=%s") >= 5


def test_legacy_transition_reader_is_retired() -> None:
    assert "LEGACY_UNKNOWN" not in SOURCE
