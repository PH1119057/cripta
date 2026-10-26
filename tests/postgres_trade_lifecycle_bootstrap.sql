-- Disposable PostgreSQL bootstrap for trade-lifecycle integration tests only.
-- This file is never installed by the production release rail.
CREATE SCHEMA IF NOT EXISTS runtime AUTHORIZATION postgres;
CREATE SCHEMA IF NOT EXISTS control AUTHORIZATION postgres;
CREATE SCHEMA IF NOT EXISTS analytics AUTHORIZATION postgres;
CREATE SCHEMA IF NOT EXISTS dispatcher_v2 AUTHORIZATION postgres;
GRANT USAGE ON SCHEMA runtime, control, analytics, dispatcher_v2 TO cripta;

CREATE TABLE dispatcher_v2.trading_capacity_snapshots (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
);

CREATE OR REPLACE FUNCTION runtime.reject_immutable_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'immutable runtime record % cannot be changed', TG_TABLE_NAME;
END;
$$;

CREATE TABLE runtime.position_ownership (
    position_id text PRIMARY KEY,
    trade_id text UNIQUE NOT NULL,
    bot_instance_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    signal_id text NOT NULL,
    entry_command_id text UNIQUE NOT NULL,
    geometry_handoff_id text,
    symbol text NOT NULL,
    side text NOT NULL,
    actual_avg_fill numeric NOT NULL,
    actual_qty numeric NOT NULL,
    fill_at timestamptz NOT NULL,
    exchange_order_ids jsonb NOT NULL,
    client_order_ids jsonb NOT NULL,
    execution_ids jsonb NOT NULL,
    state text NOT NULL DEFAULT 'OPEN',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (state IN ('OPEN','CLOSED','RECONCILIATION_REQUIRED'))
);

CREATE TABLE runtime.hot_positions (
    symbol text NOT NULL,
    position_idx integer NOT NULL,
    side text NOT NULL,
    size text NOT NULL,
    entry_price text NOT NULL,
    leverage text NOT NULL,
    exchange_updated_ms bigint,
    refreshed_at_epoch_ms bigint NOT NULL,
    payload_json text NOT NULL,
    PRIMARY KEY(symbol,position_idx)
);

CREATE TABLE runtime.hot_orders (
    order_id text PRIMARY KEY,
    order_link_id text NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL,
    order_status text NOT NULL,
    qty text NOT NULL,
    price text NOT NULL,
    leaves_qty text NOT NULL,
    exchange_updated_ms bigint,
    refreshed_at_epoch_ms bigint NOT NULL,
    payload_json text NOT NULL
);

CREATE TABLE runtime.executions (
    exec_id text PRIMARY KEY,
    order_id text NOT NULL,
    order_link_id text NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL,
    exec_qty text NOT NULL,
    exec_price text NOT NULL,
    exec_fee text NOT NULL,
    exec_time_ms bigint,
    received_at_epoch_ms bigint NOT NULL,
    payload_json text NOT NULL
);

CREATE TABLE runtime.wallet_latest (
    singleton smallint PRIMARY KEY CHECK(singleton=1),
    refreshed_at_epoch_ms bigint NOT NULL,
    total_equity text NOT NULL,
    wallet_balance text NOT NULL,
    available_balance text NOT NULL,
    payload_json text NOT NULL
);

CREATE TABLE runtime.private_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    received_at_epoch_ms bigint NOT NULL,
    topic text NOT NULL,
    message_id text NOT NULL,
    creation_time_ms bigint,
    payload_json text NOT NULL
);

CREATE TABLE runtime.connection_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY
);

CREATE TABLE runtime.reconciliation_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at_epoch_ms bigint NOT NULL,
    finished_at_epoch_ms bigint NOT NULL,
    reason text NOT NULL,
    ok smallint NOT NULL,
    positions integer NOT NULL,
    orders integer NOT NULL,
    error text NOT NULL
);

CREATE TABLE runtime.trade_commands (
    command_id text PRIMARY KEY,
    command_type text NOT NULL,
    symbol text NOT NULL,
    payload_json text NOT NULL,
    state text NOT NULL,
    requested_at_epoch_ms bigint NOT NULL,
    started_at_epoch_ms bigint,
    finished_at_epoch_ms bigint,
    result_json text NOT NULL DEFAULT '{}',
    error text NOT NULL DEFAULT ''
);

CREATE TABLE runtime.exchange_order_history (
    order_id text PRIMARY KEY,
    order_link_id text NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL,
    order_status text NOT NULL,
    qty text,
    price text,
    leaves_qty text,
    updated_at_epoch_ms bigint,
    refreshed_at_epoch_ms bigint NOT NULL,
    payload_json jsonb NOT NULL
);

CREATE TABLE control.execution_gates (
    mode text PRIMARY KEY,
    enabled smallint NOT NULL,
    reason text NOT NULL,
    updated_at_epoch_ms bigint NOT NULL
);
