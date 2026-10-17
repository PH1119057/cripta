from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "operations/sql/20261016_account_scoped_key_preparation_r5.sql"
).read_text(encoding="utf-8")
PRIVATE = (ROOT / "operations/connectivity/private_runtime.py").read_text(encoding="utf-8")
SCHEMA = (ROOT / "operations/connectivity/runtime_schema.py").read_text(encoding="utf-8")
INSTALLER = (
    ROOT / "operations/infrastructure/install_verified_release.sh"
).read_text(encoding="utf-8")


EXPECTED_INDEXES = {
    "ux_wallet_latest_trading_account_singleton": (
        "runtime.wallet_latest",
        "trading_account_id, singleton",
    ),
    "ux_hot_positions_trading_account_slot": (
        "runtime.hot_positions",
        "trading_account_id, symbol, position_idx",
    ),
    "ux_hot_orders_trading_account_order": (
        "runtime.hot_orders",
        "trading_account_id, order_id",
    ),
    "ux_executions_trading_account_exec": (
        "runtime.executions",
        "trading_account_id, exec_id",
    ),
    "ux_exchange_order_history_trading_account_order": (
        "runtime.exchange_order_history",
        "trading_account_id, order_id",
    ),
}


def test_key_preparation_is_additive_and_keeps_legacy_primary_keys() -> None:
    upper = MIGRATION.upper()
    assert MIGRATION.count("CREATE UNIQUE INDEX IF NOT EXISTS") == 5
    assert MIGRATION.count("NULLS NOT DISTINCT") >= 6
    assert "DROP CONSTRAINT" not in upper
    assert "DROP INDEX" not in upper
    assert "ADD PRIMARY KEY" not in upper
    assert "DELETE FROM" not in upper
    assert "UPDATE RUNTIME." not in upper
    assert "ALTER COLUMN" not in upper


def test_account_scoped_unique_indexes_match_current_physical_identity_grain() -> None:
    for index_name, (table, columns) in EXPECTED_INDEXES.items():
        assert f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name}" in MIGRATION
        assert f"ON {table}({columns})" in MIGRATION


def test_proven_and_legacy_identity_consistency_is_fail_closed_for_new_rows() -> None:
    assert MIGRATION.count("_account_identity_consistency_check") >= 2
    assert "identity_status=''PROVEN''" in MIGRATION
    assert "trading_account_id IS NOT NULL" in MIGRATION
    assert "account_connector_id IS NOT NULL" in MIGRATION
    assert "identity_status=''LEGACY_UNKNOWN''" in MIGRATION
    assert "trading_account_id IS NULL" in MIGRATION
    assert "account_connector_id IS NULL" in MIGRATION
    assert ") NOT VALID" in MIGRATION


def test_runtime_schema_requires_exact_unique_nulls_not_distinct_indexes() -> None:
    assert "runtime-schema-2026-10-17-compact-analytics-r8c" in SCHEMA
    assert "REQUIRED_ACCOUNT_KEY_INDEXES" in SCHEMA
    assert "indisunique" in SCHEMA
    assert "indnullsnotdistinct" in SCHEMA
    assert "actual_columns != expected_columns" in SCHEMA
    for index_name in EXPECTED_INDEXES:
        assert f"runtime.{index_name}" in SCHEMA


def test_installer_applies_key_preparation_after_identity_columns_exist() -> None:
    r5a = INSTALLER.index("20261016_private_current_state_r5a.sql")
    r5b = INSTALLER.index("20261016_execution_evidence_r5b.sql")
    keys = INSTALLER.index("20261016_account_scoped_key_preparation_r5.sql")
    assert r5a < keys
    assert r5b < keys


def test_writers_use_prepared_account_scoped_conflict_targets_after_r10a() -> None:
    assert "ON CONFLICT(trading_account_id,singleton) DO UPDATE SET" in PRIVATE
    assert (
        "ON CONFLICT(trading_account_id,symbol,position_idx) DO UPDATE SET"
        in PRIVATE
    )
    assert "ON CONFLICT(trading_account_id,order_id) DO UPDATE SET" in PRIVATE
    assert "ON CONFLICT(trading_account_id,exec_id) DO UPDATE SET" in PRIVATE
    assert "ON CONFLICT(singleton) DO UPDATE SET" not in PRIVATE
    assert "ON CONFLICT(symbol,position_idx) DO UPDATE SET" not in PRIVATE
    assert "ON CONFLICT(order_id) DO UPDATE SET" not in PRIVATE
    assert "ON CONFLICT(exec_id) DO UPDATE SET" not in PRIVATE
