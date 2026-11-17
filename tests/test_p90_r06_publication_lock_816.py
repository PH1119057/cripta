from __future__ import annotations

import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "operations/sql/20261117_p90_r06_market_publication_lock.sql"


def test_lock_migration_preserves_runtime_market_publication_immutability() -> None:
    source = SQL.read_text(encoding="utf-8")
    assert "CREATE ROLE cripta_p90_exit_publication_locker" in source
    assert "NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE" in source
    assert "NOINHERIT NOREPLICATION NOBYPASSRLS" in source
    assert "REVOKE cripta_p90_exit_publication_locker FROM cripta" in source
    assert "SECURITY DEFINER" in source
    assert "SET search_path = pg_catalog, pg_temp" in source
    assert "FOR SHARE OF p" in source
    assert (
        "REVOKE ALL ON FUNCTION "
        "analytics.lock_p90_exit_market_publication(bigint) FROM PUBLIC"
    ) in source
    assert (
        "GRANT EXECUTE ON FUNCTION "
        "analytics.lock_p90_exit_market_publication(bigint) TO cripta"
    ) in source
    assert "runtime role cripta must not receive publication UPDATE" in source


def test_r06_runtime_uses_definer_lock_not_direct_publication_for_share() -> None:
    from operations.connectivity import p90_r06_exit_owner as owner

    body = inspect.getsource(owner._lock_source)
    assert "analytics.lock_p90_exit_market_publication(%s)" in body
    publication_part = body.split(
        "analytics.lock_p90_exit_market_publication(%s)", 1
    )[0].rsplit("publication =", 1)[-1]
    assert "FOR SHARE" not in publication_part
    assert "publication_id" in body


def test_verified_release_installs_publication_lock_before_code_cutover() -> None:
    installer = (
        ROOT / "operations/infrastructure/install_verified_release.sh"
    ).read_text(encoding="utf-8")
    migration = "operations/sql/20261117_p90_r06_market_publication_lock.sql"
    assert migration in installer
    assert installer.index(migration) < installer.index(
        'for migration in "${migration_files[@]}"; do'
    )


def test_runtime_role_never_gets_market_publication_update_from_new_migration() -> None:
    source = SQL.read_text(encoding="utf-8")
    forbidden = (
        "GRANT UPDATE ON analytics.market_publications TO cripta",
        "GRANT SELECT,UPDATE ON analytics.market_publications TO cripta",
        "GRANT SELECT, UPDATE ON analytics.market_publications TO cripta",
        "GRANT ALL ON analytics.market_publications TO cripta",
    )
    assert not any(token in source for token in forbidden)
