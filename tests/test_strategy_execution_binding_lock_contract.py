from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "operations/sql/20261018_strategy_execution_binding_lock_v1.sql"
ENTRY_ADMISSION = ROOT / "src/bybit_workbench/entry_admission.py"
INSTALLER = ROOT / "operations/infrastructure/install_verified_release.sh"


def test_binding_lock_helper_is_narrow_security_definer() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE ROLE cripta_entry_binding_locker" in sql
    assert "NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE" in sql
    assert "NOINHERIT NOREPLICATION NOBYPASSRLS" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, pg_temp" in sql
    assert "FOR SHARE OF b, ta, a" in sql
    assert (
        "ALTER FUNCTION strategy_entry.lock_strategy_execution_binding(text)\n"
        "    OWNER TO cripta_entry_binding_locker"
    ) in sql
    assert (
        "REVOKE ALL ON FUNCTION strategy_entry.lock_strategy_execution_binding(text) FROM PUBLIC"
        in sql
    )
    assert (
        "GRANT EXECUTE ON FUNCTION strategy_entry.lock_strategy_execution_binding(text) TO cripta"
        in sql
    )
    assert "GRANT UPDATE ON strategy_entry.strategy_execution_bindings TO cripta" not in sql
    body = sql.split("AS $function$", 1)[1].split("$function$;", 1)[0]
    assert "EXECUTE " not in body.upper()
    assert "FORMAT(" not in body.upper()


def test_entry_admission_uses_binding_lock_helper_not_direct_row_lock() -> None:
    source = ENTRY_ADMISSION.read_text(encoding="utf-8")
    assert "FROM strategy_entry.lock_strategy_execution_binding(%s)" in source
    assert "FROM strategy_entry.strategy_execution_bindings b" not in source
    assert "FOR SHARE" not in source


def test_verified_release_installs_binding_lock_migration_after_binding_registry() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    registry = installer.index("operations/sql/20261016_strategy_execution_binding_r6a.sql")
    lock_helper = installer.index("operations/sql/20261018_strategy_execution_binding_lock_v1.sql")
    lineage = installer.index("operations/sql/20261016_binding_lineage_r6b1.sql")
    assert registry < lock_helper < lineage
