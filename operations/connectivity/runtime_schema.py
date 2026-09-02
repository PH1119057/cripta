from __future__ import annotations

import argparse
from collections.abc import Sequence

import psycopg

EXPECTED_RUNTIME_SCHEMA_VERSION = "runtime-schema-2026-09-02-v1"
LOCK_TIMEOUT_MS = 2000
STATEMENT_TIMEOUT_MS = 15000

REQUIRED_RELATIONS: tuple[str, ...] = (
    "control.execution_gates",
    "monitoring.entry_geometry_handoffs",
    "monitoring.opportunities",
    "runtime.connection_events",
    "runtime.entry_decision_events",
    "runtime.entry_decisions",
    "runtime.entry_geometry_bindings",
    "runtime.exchange_order_history",
    "runtime.executions",
    "runtime.hot_orders",
    "runtime.hot_positions",
    "runtime.m3_consumed_context",
    "runtime.position_ownership",
    "runtime.private_events",
    "runtime.reconciliation_runs",
    "runtime.trade_commands",
    "runtime.trade_settings",
    "runtime.wallet_latest",
    "strategy_dispatcher.assessments",
)

REQUIRED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("control", "execution_gates", "enabled"),
    ("runtime", "position_ownership", "entry_command_id"),
    ("runtime", "position_ownership", "position_idx"),
    ("runtime", "position_ownership", "close_link_status"),
    ("runtime", "trade_settings", "entry_offset_pct"),
    ("runtime", "trade_settings", "entry_limit_ttl_seconds"),
    ("runtime", "trade_settings", "auto_profit_protection"),
    ("runtime", "trade_settings", "auto_trailing_stop"),
    ("runtime", "trade_settings", "trailing_distance_pct"),
    ("runtime", "trade_settings", "entry_policy"),
)


def _missing_relations(connection: psycopg.Connection) -> list[str]:
    missing: list[str] = []
    for relation in REQUIRED_RELATIONS:
        row = connection.execute("SELECT to_regclass(%s)", (relation,)).fetchone()
        if not row or row[0] is None:
            missing.append(relation)
    return missing


def _missing_columns(connection: psycopg.Connection) -> list[str]:
    missing: list[str] = []
    for schema, table, column in REQUIRED_COLUMNS:
        row = connection.execute(
            """SELECT 1 FROM information_schema.columns
               WHERE table_schema=%s AND table_name=%s AND column_name=%s""",
            (schema, table, column),
        ).fetchone()
        if row is None:
            missing.append(f"{schema}.{table}.{column}")
    return missing


def validate_runtime_schema_shape(connection: psycopg.Connection) -> None:
    missing_relations = _missing_relations(connection)
    missing_columns = _missing_columns(connection)
    enabled_type = connection.execute(
        """SELECT data_type FROM information_schema.columns
           WHERE table_schema='control' AND table_name='execution_gates'
             AND column_name='enabled'"""
    ).fetchone()
    errors: list[str] = []
    if missing_relations:
        errors.append("missing relations: " + ", ".join(missing_relations))
    if missing_columns:
        errors.append("missing columns: " + ", ".join(missing_columns))
    if not enabled_type or str(enabled_type[0]) != "smallint":
        errors.append("control.execution_gates.enabled must be SMALLINT")
    if errors:
        raise RuntimeError("RUNTIME_SCHEMA_SHAPE_MISMATCH: " + "; ".join(errors))


def migrate_runtime_schema_contract(connection: psycopg.Connection) -> None:
    """Register the already-validated production schema under an explicit version.

    This is the only DDL in this module. It is installer-only and atomic. Existing
    runtime tables are never altered here; future schema changes must be separate
    versioned migrations with the same lock-timeout contract.
    """
    validate_runtime_schema_shape(connection)
    connection.commit()
    with connection.transaction():
        connection.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT_MS}ms'")
        connection.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS runtime.schema_contract(
                component TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp())"""
        )
        connection.execute(
            """INSERT INTO runtime.schema_contract(component,version,applied_at)
               VALUES('private_runtime',%s,clock_timestamp())
               ON CONFLICT(component) DO UPDATE SET
                 version=excluded.version,
                 applied_at=excluded.applied_at""",
            (EXPECTED_RUNTIME_SCHEMA_VERSION,),
        )


def validate_runtime_schema_contract(connection: psycopg.Connection) -> None:
    """Read-only startup contract. No CREATE/ALTER/DROP is allowed here."""
    validate_runtime_schema_shape(connection)
    marker = connection.execute(
        "SELECT to_regclass('runtime.schema_contract')"
    ).fetchone()
    if not marker or marker[0] is None:
        raise RuntimeError(
            "RUNTIME_SCHEMA_VERSION_MISMATCH: "
            f"expected={EXPECTED_RUNTIME_SCHEMA_VERSION} actual=MISSING"
        )
    row = connection.execute(
        """SELECT version FROM runtime.schema_contract
           WHERE component='private_runtime'"""
    ).fetchone()
    if row is None or str(row[0]) != EXPECTED_RUNTIME_SCHEMA_VERSION:
        actual = "MISSING" if row is None else str(row[0])
        raise RuntimeError(
            "RUNTIME_SCHEMA_VERSION_MISMATCH: "
            f"expected={EXPECTED_RUNTIME_SCHEMA_VERSION} actual={actual}"
        )


def _connect() -> psycopg.Connection:
    return psycopg.connect("dbname=cripta user=cripta host=/var/run/postgresql")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check-shape", "migrate", "validate"))
    args = parser.parse_args(argv)
    with _connect() as connection:
        if args.command == "check-shape":
            validate_runtime_schema_shape(connection)
            connection.rollback()
            print("RUNTIME_SCHEMA_SHAPE=PASS")
        elif args.command == "migrate":
            migrate_runtime_schema_contract(connection)
            print(f"RUNTIME_SCHEMA_MIGRATION=PASS version={EXPECTED_RUNTIME_SCHEMA_VERSION}")
        else:
            validate_runtime_schema_contract(connection)
            connection.rollback()
            print(f"RUNTIME_SCHEMA_CONTRACT=PASS version={EXPECTED_RUNTIME_SCHEMA_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
