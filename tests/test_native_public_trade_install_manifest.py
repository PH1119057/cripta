"""#470 production cutover must install the same native relation tested in PG16 CI."""
from pathlib import Path


def test_production_installer_applies_native_trade_migration_before_cutover() -> None:
    root = Path(__file__).resolve().parents[1]
    installer = (root / "operations/infrastructure/install_verified_release.sh").read_text(
        encoding="utf-8",
    )
    migration = "operations/sql/20261107_native_public_trade_evidence.sql"
    assert (root / migration).is_file()
    assert installer.count(migration) == 1
    manifest_begin = installer.index("migration_files=(")
    manifest_end = installer.index("\n)", manifest_begin)
    assert manifest_begin < installer.index(migration) < manifest_end
    apply_start = installer.index('for migration in "${migration_files[@]}"; do')
    assert manifest_end < apply_start
    assert 'runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -d cripta' in (
        installer[apply_start:installer.index("done", apply_start)]
    )
    assert apply_start < installer.index('cutover_started=1')
    sql = (root / migration).read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS analytics.native_public_trade_events" in sql
    assert "GRANT SELECT, INSERT" in sql
