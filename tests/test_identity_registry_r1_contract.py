from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "operations/sql/20261016_identity_registry_r1.sql"
INSTALLER = ROOT / "operations/infrastructure/install_verified_release.sh"
SCHEMA = ROOT / "operations/connectivity/runtime_schema.py"


def test_r1_identity_migration_is_additive_only() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    for relation in (
        "identity.venues",
        "identity.market_sources",
        "identity.instruments",
        "identity.trading_accounts",
        "identity.account_connectors",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {relation}" in sql
    assert "ADD COLUMN IF NOT EXISTS trading_account_id" in sql
    assert "ADD COLUMN IF NOT EXISTS account_connector_id" in sql
    assert "ADD COLUMN IF NOT EXISTS instrument_id" in sql
    assert "NOT VALID" in sql
    upper = sql.upper()
    assert " DROP TABLE " not in f" {upper} "
    assert " DROP COLUMN " not in f" {upper} "
    assert " DELETE FROM " not in f" {upper} "
    assert " UPDATE RUNTIME." not in upper
    assert " UPDATE DISPATCHER_V2." not in upper
    assert "CREATE INDEX" not in upper


def test_r1_identity_registry_is_read_only_for_runtime_role() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "GRANT USAGE ON SCHEMA identity TO cripta" in sql
    assert "GRANT SELECT ON" in sql
    assert "GRANT INSERT" not in sql
    assert "GRANT UPDATE" not in sql
    assert "GRANT DELETE" not in sql


def test_r1_migration_is_in_verified_installer_and_schema_contract() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    assert "operations/sql/20261016_identity_registry_r1.sql" in installer
    assert 'runtime-schema-2026-10-16-identity-r1' in schema
    for relation in (
        "identity.venues",
        "identity.market_sources",
        "identity.instruments",
        "identity.trading_accounts",
        "identity.account_connectors",
    ):
        assert f'"{relation}"' in schema
    for column in (
        '("runtime", "account_state_generations", "trading_account_id")',
        '("runtime", "account_state_generations", "account_connector_id")',
        '("runtime", "position_mode_states", "trading_account_id")',
        '("runtime", "position_mode_states", "account_connector_id")',
        '("runtime", "position_mode_states", "instrument_id")',
        '("dispatcher_v2", "trading_capacity_snapshots", "trading_account_id")',
        '("dispatcher_v2", "trading_capacity_snapshots", "account_connector_id")',
    ):
        assert column in schema
