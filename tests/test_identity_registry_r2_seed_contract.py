from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "operations/sql/20261016_identity_registry_r2_seed.sql"
INSTALLER = ROOT / "operations/infrastructure/install_verified_release.sh"


def test_r2_seed_registers_only_proven_current_identities() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "'venue-bybit'" in sql
    assert "'ms-bybit-mainnet-public-v1'" in sql
    assert "'ta-2122c81ecff14f3b85b102db605212f7'" in sql
    assert "'ac-ed73de20410548d6841aeafa5f196f70'" in sql
    assert "'systemd-credential:bybit-mainnet'" in sql
    assert "BYBIT:UNIFIED" in sql
    assert "external_account_ref" in sql
    assert "NULL" in sql
    assert "identity.instruments" not in sql


def test_r2_seed_is_idempotent_and_does_not_backfill_history() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert sql.count("ON CONFLICT") == 4
    upper = sql.upper()
    assert "UPDATE RUNTIME." not in upper
    assert "UPDATE DISPATCHER_V2." not in upper
    assert "DELETE FROM" not in upper
    assert "DROP TABLE" not in upper
    assert "DROP COLUMN" not in upper
    assert "ALTER TABLE" not in upper


def test_r2_seed_is_registered_in_verified_installer() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    r1 = installer.index("operations/sql/20261016_identity_registry_r1.sql")
    r2 = installer.index("operations/sql/20261016_identity_registry_r2_seed.sql")
    assert r1 < r2
