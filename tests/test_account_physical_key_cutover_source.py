"""Source-level fail-closed contracts for five-table private-state key cutover.

Actual database migration and multi-account behavior require disposable PostgreSQL
CI plus separately authorized release/rollback evidence; text tests are not that proof.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "operations/sql/20261026_account_scoped_physical_key_cutover.sql").read_text(
    encoding="utf-8"
)
PREPARATION = (ROOT / "operations/sql/20261016_account_scoped_key_preparation_r5.sql").read_text(
    encoding="utf-8"
)
INSTALLER = (ROOT / "operations/infrastructure/install_verified_release.sh").read_text(
    encoding="utf-8"
)
SCHEMA = (ROOT / "operations/connectivity/runtime_schema.py").read_text(encoding="utf-8")
VERIFIER = (ROOT / "operations/monitoring/r9_migration_verifier.py").read_text(encoding="utf-8")

TABLES = {
    "runtime.wallet_latest": "wallet_latest_pkey",
    "runtime.hot_positions": "hot_positions_pkey",
    "runtime.hot_orders": "hot_orders_pkey",
    "runtime.executions": "executions_pkey",
    "runtime.exchange_order_history": "exchange_order_history_pkey",
}


def test_cutover_uses_one_transaction_with_prepared_keys_and_no_data_rewrite() -> None:
    assert SQL.startswith("-- R10 account-scoped physical-key cutover candidate.")
    assert SQL.count("BEGIN;") == 1
    assert SQL.strip().endswith("COMMIT;")
    assert "SET LOCAL lock_timeout" in SQL
    assert "SET LOCAL statement_timeout" in SQL
    for table, old_pk in TABLES.items():
        assert table in SQL
        assert old_pk in SQL
        assert old_pk in SCHEMA
        assert old_pk not in PREPARATION  # earlier R5 stays additive
    for index in (
        "ux_wallet_latest_trading_account_singleton",
        "ux_hot_positions_trading_account_slot",
        "ux_hot_orders_trading_account_order",
        "ux_executions_trading_account_exec",
        "ux_exchange_order_history_trading_account_order",
    ):
        assert index in SQL
        assert index in PREPARATION
    for invariant in (
        "indisunique", "indisvalid", "indisready", "indnullsnotdistinct",
        "PROVEN", "LEGACY_UNKNOWN", "conindid=pk.conindid",
        "ACCOUNT_SCOPED_UNIQUE_INDEX_UNPROVEN",
        "ACCOUNT_IDENTITY_PROVENANCE_INCONSISTENT",
        "UNEXPECTED_LEGACY_PRIMARY_KEY", "LEGACY_PRIMARY_KEY_STILL_REFERENCED",
        "GLOBAL_PRIMARY_KEY_NOT_RETIRED",
    ):
        assert invariant in SQL
    for forbidden in (
        "DELETE FROM", "UPDATE runtime.", "TRUNCATE",
        "DROP TABLE", "CASCADE", "CREATE USER",
    ):
        assert forbidden not in SQL.upper()


def test_cutover_is_last_sql_migration_and_schema_refuses_legacy_global_pk() -> None:
    assert INSTALLER.index("20261016_account_scoped_key_preparation_r5.sql") < INSTALLER.index(
        "20261026_account_scoped_physical_key_cutover.sql"
    )
    assert INSTALLER.index("20261025_account_attempt_fanout.sql") < INSTALLER.index(
        "20261026_account_scoped_physical_key_cutover.sql"
    )
    assert INSTALLER.index("20261026_account_scoped_physical_key_cutover.sql") < INSTALLER.index(
        "runtime_schema.py\" migrate"
    )
    assert "RETIRED_GLOBAL_PRIMARY_KEYS" in SCHEMA
    assert "_unretired_global_primary_keys" in SCHEMA
    assert "legacy global PRIMARY KEY still present" in SCHEMA


def test_r9_account_parity_reader_does_not_treat_other_account_as_its_own() -> None:
    block = VERIFIER.split("def _private_current_state_gate(", 1)[1].split(
        "def _market_source_gate(", 1
    )[0]
    assert block.count("WHERE trading_account_id=%s") >= 3
    assert "ACCOUNT_SCOPE_PARITY" in block
    assert "EXACT_ACCOUNT_ROW_NOT_UNIQUE_OR_UNPROVEN" in block
    assert "NO_LATEST_EXACT_PROVEN_ROW" in block
    assert "SELECT count(*)," in block
