from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "operations/sql/20261016_private_current_state_r5a.sql"
PRIVATE = ROOT / "operations/connectivity/private_runtime.py"
SCHEMA = ROOT / "operations/connectivity/runtime_schema.py"


def test_r5a_schema_is_additive_without_key_cutover() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    for table in (
        "runtime.wallet_latest",
        "runtime.hot_positions",
        "runtime.hot_orders",
        "runtime.reconciliation_runs",
        "runtime.private_events",
        "runtime.connection_events",
    ):
        assert f"ALTER TABLE {table}" in sql
    assert sql.count("DEFAULT 'LEGACY_UNKNOWN'") == 6
    assert "CREATE INDEX" not in sql.upper()
    assert "DROP " not in sql.upper()
    assert "DELETE FROM" not in sql.upper()
    assert "UPDATE RUNTIME." not in sql.upper()


def test_r5a_private_runtime_marks_refreshed_current_state_proven() -> None:
    source = PRIVATE.read_text(encoding="utf-8")
    for table in (
        "runtime.wallet_latest",
        "runtime.hot_positions",
        "runtime.hot_orders",
        "runtime.reconciliation_runs",
        "runtime.private_events",
        "runtime.connection_events",
    ):
        assert table in source
    assert source.count("identity_status='PROVEN'") >= 3
    assert source.count("'PROVEN'") >= 8
    assert source.count("TRADING_ACCOUNT_ID") >= 12
    assert source.count("ACCOUNT_CONNECTOR_ID") >= 12


def test_r5a_does_not_claim_multi_account_key_cutover() -> None:
    source = PRIVATE.read_text(encoding="utf-8")
    assert "ON CONFLICT(symbol,position_idx)" in source
    assert "ON CONFLICT(order_id)" in source
    assert "WHERE singleton=1" in source
    schema = SCHEMA.read_text(encoding="utf-8")
    assert "EXPECTED_RUNTIME_SCHEMA_VERSION" in schema
