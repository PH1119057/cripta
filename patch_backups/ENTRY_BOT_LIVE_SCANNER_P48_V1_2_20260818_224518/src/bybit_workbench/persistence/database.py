import sqlite3
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

SCHEMA_VERSION = 7


_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    severity TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(profile, checksum)
);

CREATE TABLE IF NOT EXISTS strategy_runs (
    run_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    code_version TEXT,
    mode TEXT NOT NULL,
    symbol TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS strategy_decisions (
    decision_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES strategy_runs(run_id),
    candle_at TEXT,
    inputs_json TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trade_intents (
    intent_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES strategy_runs(run_id),
    decision_id TEXT REFERENCES strategy_decisions(decision_id),
    intent_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_decisions (
    risk_decision_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL REFERENCES trade_intents(intent_id),
    approved INTEGER NOT NULL CHECK(approved IN (0, 1)),
    checks_json TEXT NOT NULL,
    normalized_order_json TEXT,
    normalized_stop TEXT,
    risk_budget TEXT,
    estimated_loss_at_stop TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    client_order_id TEXT NOT NULL UNIQUE,
    intent_id TEXT REFERENCES trade_intents(intent_id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    role TEXT NOT NULL,
    reduce_only INTEGER NOT NULL CHECK(reduce_only IN (0, 1)),
    quantity TEXT NOT NULL,
    price TEXT,
    status TEXT NOT NULL,
    filled_quantity TEXT NOT NULL,
    average_price TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_state_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    event_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    cumulative_filled_quantity TEXT NOT NULL,
    average_price TEXT,
    occurred_at TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    client_order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity TEXT NOT NULL,
    price TEXT NOT NULL,
    fee TEXT NOT NULL,
    reason TEXT NOT NULL,
    executed_at TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS position_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity TEXT NOT NULL,
    average_price TEXT,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stop_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT REFERENCES strategy_runs(run_id),
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    price TEXT NOT NULL,
    protected_quantity TEXT NOT NULL,
    reason TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reconciliation_runs (
    reconciliation_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    discrepancies_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS engine_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES strategy_runs(run_id),
    snapshot_type TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    is_current INTEGER NOT NULL CHECK(is_current IN (0, 1)),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_strategy_decisions_run ON strategy_decisions(run_id);
CREATE INDEX IF NOT EXISTS ix_trade_intents_run ON trade_intents(run_id);
CREATE INDEX IF NOT EXISTS ix_orders_intent ON orders(intent_id);
CREATE INDEX IF NOT EXISTS ix_order_history_order ON order_state_history(order_id, id);
CREATE INDEX IF NOT EXISTS ix_executions_order ON executions(order_id);
CREATE INDEX IF NOT EXISTS ix_positions_symbol_time ON position_snapshots(symbol, id);
CREATE INDEX IF NOT EXISTS ix_snapshots_current ON engine_snapshots(run_id, is_current, id);
"""


_MIGRATION_2 = """
ALTER TABLE stop_updates ADD COLUMN intent_id TEXT REFERENCES trade_intents(intent_id);
ALTER TABLE stop_updates ADD COLUMN order_id TEXT REFERENCES orders(order_id);
ALTER TABLE position_snapshots ADD COLUMN run_id TEXT REFERENCES strategy_runs(run_id);
CREATE INDEX IF NOT EXISTS ix_stop_updates_intent ON stop_updates(intent_id, id);
CREATE INDEX IF NOT EXISTS ix_position_snapshots_run ON position_snapshots(run_id, id);
"""


_MIGRATION_3 = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_engine_snapshots_one_current
ON engine_snapshots(run_id, snapshot_type) WHERE is_current=1;
"""


_MIGRATION_4 = """
CREATE TABLE IF NOT EXISTS execution_commands (
    command_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    intent_id TEXT REFERENCES trade_intents(intent_id),
    request_json TEXT NOT NULL,
    request_checksum TEXT NOT NULL,
    status TEXT NOT NULL,
    exchange_order_id TEXT,
    response_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_execution_commands_intent
ON execution_commands(intent_id, created_at);
CREATE INDEX IF NOT EXISTS ix_execution_commands_status
ON execution_commands(status, updated_at);
"""


_MIGRATION_5 = """
CREATE TABLE IF NOT EXISTS historical_validation_reports (
    report_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    parameters_fingerprint TEXT NOT NULL,
    dataset_fingerprint TEXT NOT NULL,
    eligible_for_testnet INTEGER NOT NULL CHECK(eligible_for_testnet IN (0, 1)),
    report_json TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_historical_validation_strategy
ON historical_validation_reports(
    strategy_id, strategy_version, parameters_fingerprint, generated_at
);
"""


_MIGRATION_6 = """
CREATE TABLE IF NOT EXISTS mainnet_idempotency_claims (
    idempotency_key TEXT PRIMARY KEY,
    claimed_at TEXT NOT NULL
);
"""


_MIGRATION_7 = """
ALTER TABLE historical_validation_reports ADD COLUMN symbol TEXT;
ALTER TABLE historical_validation_reports ADD COLUMN timeframe TEXT;
ALTER TABLE historical_validation_reports ADD COLUMN code_version TEXT;
ALTER TABLE historical_validation_reports ADD COLUMN maker_fee_rate TEXT;
ALTER TABLE historical_validation_reports ADD COLUMN taker_fee_rate TEXT;
ALTER TABLE historical_validation_reports ADD COLUMN slippage_percent TEXT;
ALTER TABLE historical_validation_reports ADD COLUMN execution_mode TEXT;
ALTER TABLE historical_validation_reports ADD COLUMN price_trigger TEXT;
ALTER TABLE historical_validation_reports ADD COLUMN instrument_rules_fingerprint TEXT;
ALTER TABLE historical_validation_reports ADD COLUMN production_equivalent INTEGER
    CHECK(production_equivalent IN (0, 1));
ALTER TABLE historical_validation_reports ADD COLUMN binding_fingerprint TEXT;
CREATE INDEX IF NOT EXISTS ix_historical_validation_exact_binding
ON historical_validation_reports(
    strategy_id, strategy_version, parameters_fingerprint, symbol, timeframe,
    code_version, maker_fee_rate, taker_fee_rate, slippage_percent,
    execution_mode, price_trigger, instrument_rules_fingerprint, generated_at
);
"""


class DatabaseConnection:
    """SQLAlchemy-owned SQLite connection with the small DB-API surface repositories use."""

    def __init__(
        self,
        engine: Engine,
        proxy: Any,
        driver_connection: sqlite3.Connection,
    ) -> None:
        self._engine = engine
        self._proxy = proxy
        self._driver_connection = driver_connection

    def execute(
        self,
        statement: str,
        parameters: Sequence[Any] = (),
    ) -> sqlite3.Cursor:
        return self._driver_connection.execute(statement, parameters)

    def executescript(self, script: str) -> sqlite3.Cursor:
        return self._driver_connection.executescript(script)

    def commit(self) -> None:
        self._driver_connection.commit()

    @property
    def in_transaction(self) -> bool:
        return self._driver_connection.in_transaction

    def rollback(self) -> None:
        self._driver_connection.rollback()

    def close(self) -> None:
        self._proxy.close()
        self._engine.dispose()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        if exc_type is None:
            self.commit()
        else:
            self.rollback()


def open_database(path: Path | str) -> DatabaseConnection:
    database_path = Path(path)
    if database_path != Path(":memory:"):
        database_path.parent.mkdir(parents=True, exist_ok=True)
    url = (
        "sqlite+pysqlite:///:memory:"
        if database_path == Path(":memory:")
        else f"sqlite+pysqlite:///{database_path.resolve().as_posix()}"
    )
    engine = create_engine(
        url,
        poolclass=NullPool,
        connect_args={"timeout": 30.0},
    )
    proxy = engine.raw_connection()
    driver_connection = proxy.driver_connection
    if not isinstance(driver_connection, sqlite3.Connection):
        proxy.close()
        engine.dispose()
        raise TypeError("SQLAlchemy did not provide a SQLite driver connection")
    driver_connection.row_factory = sqlite3.Row
    connection = DatabaseConnection(engine, proxy, driver_connection)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    try:
        apply_migrations(connection)
    except Exception:
        connection.close()
        raise
    return connection


def apply_migrations(connection: DatabaseConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    version = int(
        connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
    )
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {version} is newer than supported {SCHEMA_VERSION}"
        )
    migrations = (
        (1, _MIGRATION_1),
        (2, _MIGRATION_2),
        (3, _MIGRATION_3),
        (4, _MIGRATION_4),
        (5, _MIGRATION_5),
        (6, _MIGRATION_6),
        (7, _MIGRATION_7),
    )
    for migration_version, script in migrations:
        if migration_version <= version:
            continue
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + script
                + "\nINSERT OR IGNORE INTO schema_migrations(version, applied_at) "
                + f"VALUES ({migration_version}, datetime('now'));\nCOMMIT;"
            )
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        version = migration_version
