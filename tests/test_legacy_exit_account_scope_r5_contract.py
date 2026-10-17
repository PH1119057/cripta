from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "operations/monitoring/exit_runtime.py").read_text(encoding="utf-8")


def test_exit_private_evidence_is_exact_proven_account_scoped_after_r10b() -> None:
    assert "FROM runtime.executions" in SOURCE
    assert "FROM runtime.private_events" in SOURCE
    assert "LEGACY_UNKNOWN" not in SOURCE
    assert SOURCE.count("identity_status='PROVEN'") >= 4
    assert SOURCE.count("trading_account_id=%s") >= 4


def test_new_break_even_and_trailing_commands_have_proven_account_identity() -> None:
    assert SOURCE.count("trading_account_id,identity_status") >= 2
    assert SOURCE.count("'PROVEN'") >= 2
    assert SOURCE.count("TRADING_ACCOUNT_ID") >= 8