"""Regression for repeated P90L90 v2 passive function installation.

The exact production function was independently verified to match the
original GitHub body before the one-time migration replay repair.
These tests guarantee that replay preserves its security-definer body and
the temporary privileges are still revoked before COMMIT.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

SQL = (
    Path(__file__).resolve().parents[1]
    / "operations/sql/20261028_p90l90_strategy_v2_passive_binding_writer.sql"
)
EXPECTED_EXISTING_PROSRC_MD5 = "0cc5b305c9dc372dcef0c288c09630f7"


def test_replay_guard_matches_exact_approved_function_body() -> None:
    sql = SQL.read_text(encoding="utf-8")
    assert sql.count("CREATE OR REPLACE FUNCTION strategy_entry.register_p90l90_v2_inactive_binding(") == 1
    assert sql.count("AS $function$\n") == 1
    assert sql.count("\n$function$;") == 1
    body = sql.split("AS $function$\n", 1)[1].split("\n$function$;", 1)[0]
    # PostgreSQL stores the original leading/trailing delimiter newlines
    # as part of pg_proc.prosrc. No function body change is approved here.
    actual_md5 = hashlib.md5(("\n" + body + "\n").encode("utf-8")).hexdigest()
    assert actual_md5 == EXPECTED_EXISTING_PROSRC_MD5
    assert sql.count(EXPECTED_EXISTING_PROSRC_MD5) == 1
    assert "DO $existing_v2$" in sql
    assert "P90L90_V2_EXISTING_FUNCTION_DRIFT" in sql


def test_idempotency_does_not_change_privileged_writer_contract() -> None:
    sql = SQL.read_text(encoding="utf-8")
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, pg_temp" in sql
    assert "OWNER TO cripta_p90l90_passive_binding" in sql
    assert "REVOKE CREATE ON SCHEMA strategy_entry FROM cripta_p90l90_passive_binding;" in sql
    assert "REVOKE ALL ON FUNCTION" in sql
    assert "FROM PUBLIC;" in sql
    assert "GRANT EXECUTE ON FUNCTION" in sql
    assert "TO cripta;" in sql
    assert "P90L90_V2_POST_MIGRATION_PRIVILEGE_INVARIANT" in sql
    assert "DROP FUNCTION" not in sql
    assert "DROP OWNED" not in sql
    assert "TRUNCATE" not in sql
    assert "ALTER ROLE cripta" not in sql


def test_sql_guard_precedes_any_temporary_create_privilege() -> None:
    sql = SQL.read_text(encoding="utf-8")
    guard = sql.index("DO $existing_v2$")
    create_grant = sql.index("GRANT CREATE ON SCHEMA strategy_entry")
    replace_function = sql.index(
        "CREATE OR REPLACE FUNCTION strategy_entry.register_p90l90_v2_inactive_binding("
    )
    revoke = sql.index(
        "REVOKE CREATE ON SCHEMA strategy_entry FROM cripta_p90l90_passive_binding;"
    )
    assert guard < create_grant < replace_function < revoke < sql.rfind("COMMIT;")
