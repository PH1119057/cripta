from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Any

import psycopg

EXPECTED_RUNTIME_SCHEMA_VERSION = "runtime-schema-2026-10-17-compact-analytics-r8c"
LOCK_TIMEOUT_MS = 2000
STATEMENT_TIMEOUT_MS = 15000

REQUIRED_RELATIONS: tuple[str, ...] = (
    "analytics.market_minutes",
    "control.execution_gates",
    "control.live_arm_evidence",
    "control.live_arm_sessions",
    "identity.account_connectors",
    "identity.instruments",
    "identity.market_sources",
    "identity.trading_accounts",
    "identity.venues",
    "monitoring.entry_geometry_handoffs",
    "monitoring.opportunities",
    "runtime.account_state_generations",
    "runtime.connection_events",
    "runtime.entry_decision_events",
    "runtime.entry_decisions",
    "runtime.entry_geometry_bindings",
    "runtime.exchange_order_history",
    "runtime.executions",
    "runtime.exchange_position_slot_claims",
    "runtime.position_mode_states",
    "runtime.lifecycle_fault_deliveries",
    "strategy_entry.execution_request_state_events",
    "strategy_entry.strategy_execution_bindings",
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

REQUIRED_ACCOUNT_KEY_INDEXES: tuple[
    tuple[str, str, tuple[str, ...]], ...
] = (
    (
        "runtime.ux_wallet_latest_trading_account_singleton",
        "runtime.wallet_latest",
        ("trading_account_id", "singleton"),
    ),
    (
        "runtime.ux_hot_positions_trading_account_slot",
        "runtime.hot_positions",
        ("trading_account_id", "symbol", "position_idx"),
    ),
    (
        "runtime.ux_hot_orders_trading_account_order",
        "runtime.hot_orders",
        ("trading_account_id", "order_id"),
    ),
    (
        "runtime.ux_executions_trading_account_exec",
        "runtime.executions",
        ("trading_account_id", "exec_id"),
    ),
    (
        "runtime.ux_exchange_order_history_trading_account_order",
        "runtime.exchange_order_history",
        ("trading_account_id", "order_id"),
    ),
)

# R10: old provider-global PRIMARY KEY constraints must no longer reject
# two independently authenticated accounts with the same physical provider ID.
RETIRED_GLOBAL_PRIMARY_KEYS: tuple[tuple[str, str], ...] = (
    ("runtime.wallet_latest", "wallet_latest_pkey"),
    ("runtime.hot_positions", "hot_positions_pkey"),
    ("runtime.hot_orders", "hot_orders_pkey"),
    ("runtime.executions", "executions_pkey"),
    ("runtime.exchange_order_history", "exchange_order_history_pkey"),
)


REQUIRED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("analytics", "market_minutes", "observed_minute"),
    ("analytics", "market_minutes", "market_source_id"),
    ("analytics", "market_minutes", "instrument_id"),
    ("analytics", "market_minutes", "product_category"),
    ("analytics", "market_minutes", "source_snapshot_id"),
    ("analytics", "market_minutes", "config_fingerprint"),
    ("analytics", "market_minutes", "schema_version"),
    ("analytics", "market_minutes", "flow_60m_acceleration_usd_per_min2"),
    ("analytics", "market_minutes", "bid_depth_50bps_usd"),
    ("analytics", "market_minutes", "imbalance_change_15m"),
    ("analytics", "market_minutes", "open_interest_change_60m_pct"),
    ("analytics", "market_minutes", "funding_rate_change_from_previous"),
    ("control", "execution_gates", "enabled"),
    ("runtime", "position_ownership", "entry_command_id"),
    ("runtime", "position_ownership", "position_idx"),
    ("runtime", "position_ownership", "close_link_status"),
    ("runtime", "position_ownership", "exchange_position_slot_claim_id"),
    ("runtime", "position_ownership", "position_mode_state_ref"),
    ("runtime", "position_ownership", "initial_protection_confirmed_at"),
    ("runtime", "position_ownership", "emergency_policy"),
    ("runtime", "account_state_generations", "trading_account_id"),
    ("runtime", "account_state_generations", "account_connector_id"),
    ("runtime", "position_mode_states", "trading_account_id"),
    ("runtime", "position_mode_states", "account_connector_id"),
    ("runtime", "position_mode_states", "instrument_id"),
    ("mayak_v2", "coin_minutes", "market_source_id"),
    ("mayak_v2", "coin_minutes", "spot_instrument_id"),
    ("mayak_v2", "coin_minutes", "linear_instrument_id"),
    ("mayak_v2", "liquidations", "market_source_id"),
    ("mayak_v2", "liquidations", "instrument_id"),
    ("mayak_v2", "coin_market_contexts", "market_source_id"),
    ("mayak_v2", "coin_market_contexts", "spot_instrument_id"),
    ("mayak_v2", "coin_market_contexts", "linear_instrument_id"),
    ("mayak_v2", "shared_market_contexts", "market_source_id"),
    ("dispatcher_v2", "global_market_contexts", "market_source_id"),
    ("dispatcher_v2", "coin_market_contexts", "market_source_id"),
    ("dispatcher_v2", "coin_market_contexts", "spot_instrument_id"),
    ("dispatcher_v2", "coin_market_contexts", "linear_instrument_id"),
    ("dispatcher_v2", "trading_capacity_snapshots", "trading_account_id"),
    ("dispatcher_v2", "trading_capacity_snapshots", "account_connector_id"),
    ("runtime", "account_state_generations", "identity_status"),
    ("runtime", "position_mode_states", "identity_status"),
    ("dispatcher_v2", "trading_capacity_snapshots", "identity_status"),
    ("runtime", "wallet_latest", "trading_account_id"),
    ("runtime", "wallet_latest", "account_connector_id"),
    ("runtime", "wallet_latest", "identity_status"),
    ("runtime", "hot_positions", "trading_account_id"),
    ("runtime", "hot_positions", "account_connector_id"),
    ("runtime", "hot_positions", "identity_status"),
    ("runtime", "hot_orders", "trading_account_id"),
    ("runtime", "hot_orders", "account_connector_id"),
    ("runtime", "hot_orders", "identity_status"),
    ("runtime", "reconciliation_runs", "trading_account_id"),
    ("runtime", "reconciliation_runs", "account_connector_id"),
    ("runtime", "reconciliation_runs", "identity_status"),
    ("runtime", "private_events", "trading_account_id"),
    ("runtime", "private_events", "account_connector_id"),
    ("runtime", "private_events", "identity_status"),
    ("runtime", "connection_events", "trading_account_id"),
    ("runtime", "connection_events", "account_connector_id"),
    ("runtime", "connection_events", "identity_status"),
    ("runtime", "executions", "trading_account_id"),
    ("runtime", "executions", "account_connector_id"),
    ("runtime", "executions", "identity_status"),
    ("runtime", "exchange_order_history", "trading_account_id"),
    ("runtime", "exchange_order_history", "account_connector_id"),
    ("runtime", "exchange_order_history", "identity_status"),
    ("runtime", "capital_reservations", "strategy_execution_binding_id"),
    ("runtime", "capital_reservations", "execution_leg"),
    ("runtime", "capital_reservations", "trading_account_id"),
    ("runtime", "capital_reservations", "identity_status"),
    ("runtime", "exchange_position_slot_claims", "strategy_execution_binding_id"),
    ("runtime", "exchange_position_slot_claims", "execution_leg"),
    ("runtime", "exchange_position_slot_claims", "trading_account_id"),
    ("runtime", "exchange_position_slot_claims", "identity_status"),
    ("strategy_entry", "entry_decisions", "strategy_execution_binding_id"),
    ("strategy_entry", "entry_decisions", "execution_leg"),
    ("strategy_entry", "entry_decisions", "trading_account_id"),
    ("strategy_entry", "entry_decisions", "identity_status"),
    ("strategy_entry", "execution_requests", "strategy_execution_binding_id"),
    ("strategy_entry", "execution_requests", "execution_leg"),
    ("strategy_entry", "execution_requests", "trading_account_id"),
    ("strategy_entry", "execution_requests", "identity_status"),
    ("runtime", "trade_commands", "strategy_execution_binding_id"),
    ("runtime", "trade_commands", "execution_leg"),
    ("runtime", "trade_commands", "trading_account_id"),
    ("runtime", "trade_commands", "identity_status"),
    ("runtime", "position_ownership", "strategy_execution_binding_id"),
    ("runtime", "position_ownership", "execution_leg"),
    ("runtime", "position_ownership", "trading_account_id"),
    ("runtime", "position_ownership", "identity_status"),
    ("strategy_exit", "execution_requests", "strategy_execution_binding_id"),
    ("strategy_exit", "execution_requests", "execution_leg"),
    ("strategy_exit", "execution_requests", "trading_account_id"),
    ("strategy_exit", "execution_requests", "identity_status"),
    ("strategy_entry", "entry_decisions", "exchange_position_slot_claim_id"),
    ("strategy_entry", "entry_decisions", "position_mode_state_ref"),
    ("strategy_entry", "execution_requests", "exchange_position_slot_claim_id"),
    ("strategy_entry", "execution_requests", "position_mode_state_ref"),
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


def _invalid_account_key_indexes(connection: psycopg.Connection[Any]) -> list[str]:
    invalid: list[str] = []
    for index_name, table_name, expected_columns in REQUIRED_ACCOUNT_KEY_INDEXES:
        row = connection.execute(
            """SELECT tbl.oid::regclass::text,
                      ix.indisunique,
                      ix.indnullsnotdistinct,
                      array_agg(att.attname ORDER BY keycol.ordinality)
                 FROM pg_class idx
                 JOIN pg_index ix ON ix.indexrelid=idx.oid
                 JOIN pg_class tbl ON tbl.oid=ix.indrelid
                 JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY
                      AS keycol(attnum,ordinality)
                   ON keycol.ordinality <= ix.indnkeyatts
                 JOIN pg_attribute att
                   ON att.attrelid=tbl.oid
                  AND att.attnum=keycol.attnum
                WHERE idx.oid=to_regclass(%s)
                GROUP BY tbl.oid,ix.indisunique,ix.indnullsnotdistinct""",
            (index_name,),
        ).fetchone()
        if row is None:
            invalid.append(f"{index_name}:MISSING")
            continue
        actual_table = str(row[0])
        actual_columns = tuple(str(value) for value in row[3])
        if (
            actual_table != table_name
            or not bool(row[1])
            or not bool(row[2])
            or actual_columns != expected_columns
        ):
            invalid.append(
                f"{index_name}:"
                f"table={actual_table},unique={bool(row[1])},"
                f"nulls_not_distinct={bool(row[2])},columns={actual_columns}"
            )
    return invalid


def _unretired_global_primary_keys(connection: psycopg.Connection[Any]) -> list[str]:
    remaining: list[str] = []
    for relation, obsolete_name in RETIRED_GLOBAL_PRIMARY_KEYS:
        row = connection.execute(
            """SELECT conname
                 FROM pg_constraint
                WHERE conrelid=to_regclass(%s) AND contype='p'""",
            (relation,),
        ).fetchone()
        if row is not None:
            remaining.append(f"{relation}:{row[0]}:expected_retired={obsolete_name}")
    return remaining


def validate_runtime_schema_shape(connection: psycopg.Connection) -> None:
    missing_relations = _missing_relations(connection)
    missing_columns = _missing_columns(connection)
    invalid_account_key_indexes = _invalid_account_key_indexes(connection)
    unretired_global_primary_keys = _unretired_global_primary_keys(connection)
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
    if invalid_account_key_indexes:
        errors.append(
            "invalid account key indexes: " + ", ".join(invalid_account_key_indexes)
        )
    if unretired_global_primary_keys:
        errors.append("legacy global PRIMARY KEY still present: " + ", ".join(unretired_global_primary_keys))
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