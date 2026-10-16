from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "operations/sql/20261016_trade_command_account_identity_r6d.sql").read_text(encoding="utf-8")

def test_trade_command_identity_separates_account_proof_from_strategy_binding() -> None:
    assert "trade_commands_account_identity_check" in MIGRATION
    assert "identity_status='PROVEN'" in MIGRATION
    assert "trading_account_id IS NOT NULL" in MIGRATION
    assert "(strategy_execution_binding_id IS NULL AND execution_leg IS NULL)" in MIGRATION
    assert "(strategy_execution_binding_id IS NOT NULL AND execution_leg IS NOT NULL)" in MIGRATION

def test_position_ownership_binding_constraint_is_not_relaxed() -> None:
    assert "runtime.position_ownership" not in MIGRATION

def test_r6d_changes_constraint_only_and_never_rewrites_rows() -> None:
    upper = MIGRATION.upper()
    assert "DELETE FROM" not in upper
    assert "UPDATE RUNTIME.TRADE_COMMANDS" not in upper
    assert "DROP TABLE" not in upper
    assert "DROP COLUMN" not in upper