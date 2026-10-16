from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "operations/monitoring/exit_runtime.py").read_text(encoding="utf-8")


def test_legacy_exit_private_evidence_is_account_scoped_with_transition() -> None:
    assert "FROM runtime.executions" in SOURCE
    assert "FROM runtime.private_events" in SOURCE
    assert SOURCE.count("identity_status='LEGACY_UNKNOWN'") >= 4
    assert SOURCE.count("trading_account_id=%s") >= 4


def test_new_break_even_and_trailing_commands_have_proven_account_identity() -> None:
    assert SOURCE.count("trading_account_id,identity_status") >= 2
    assert SOURCE.count("'PROVEN'") >= 2
    assert SOURCE.count("TRADING_ACCOUNT_ID") >= 8