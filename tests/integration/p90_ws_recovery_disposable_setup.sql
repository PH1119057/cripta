-- Disposable PostgreSQL 16 schema contract for the exact P90 recovery ACL.
-- CI only: not a production fixture or trading policy.
CREATE ROLE cripta LOGIN PASSWORD 'ci_p90_recovery_disposable_only';
CREATE SCHEMA runtime;
CREATE SCHEMA control;
CREATE SCHEMA strategy_entry;
CREATE SCHEMA identity;

CREATE TABLE runtime.account_entry_safety_blocks (
    trading_account_id text NOT NULL,
    account_connector_id text NOT NULL,
    reason text NOT NULL,
    blocked_at timestamptz NOT NULL,
    PRIMARY KEY(trading_account_id,account_connector_id)
);
CREATE TABLE runtime.account_state_generations (
    generation_id text PRIMARY KEY,
    trading_account_id text NOT NULL,
    account_connector_id text NOT NULL,
    account_ref text NOT NULL,
    identity_status text NOT NULL,
    state text NOT NULL,
    completed_at timestamptz,
    positions_count integer,
    active_orders_count integer
);
CREATE TABLE runtime.trade_commands (
    trading_account_id text NOT NULL,
    state text NOT NULL
);
CREATE TABLE runtime.lifecycle_faults (
    severity text NOT NULL,
    state text NOT NULL,
    exact_ids jsonb NOT NULL
);
CREATE TABLE control.execution_gates (
    mode text NOT NULL,
    enabled integer NOT NULL
);
CREATE TABLE control.live_arm_sessions (
    state text NOT NULL
);
CREATE TABLE control.account_live_arm_sessions (
    state text NOT NULL
);
CREATE TABLE strategy_entry.execution_permissions (
    enabled boolean NOT NULL
);
CREATE TABLE identity.trading_accounts (
    trading_account_id text PRIMARY KEY,
    external_account_ref text NOT NULL,
    environment text NOT NULL,
    status text NOT NULL,
    account_type text NOT NULL,
    venue_id text NOT NULL
);
CREATE TABLE identity.account_connectors (
    trading_account_id text NOT NULL,
    account_connector_id text NOT NULL,
    status text NOT NULL,
    retired_at timestamptz,
    adapter_id text NOT NULL,
    connector_kind text NOT NULL
);
INSERT INTO control.execution_gates VALUES ('mainnet',0),('testnet',0);
INSERT INTO identity.trading_accounts VALUES (
    'ta-853ac5fcd58185697396d33cd57459fa','123456789',
    'MAINNET','ACTIVE','UNIFIED','venue-bybit'
);
INSERT INTO identity.account_connectors VALUES (
    'ta-853ac5fcd58185697396d33cd57459fa',
    'ac-ec57819f875f1bfd929a382d6dc8a125',
    'ACTIVE',NULL,'BYBIT_V5_PRIVATE','CEX_PRIVATE_RUNTIME'
);
INSERT INTO runtime.account_entry_safety_blocks VALUES (
    'ta-853ac5fcd58185697396d33cd57459fa',
    'ac-ec57819f875f1bfd929a382d6dc8a125',
    'PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN',
    clock_timestamp()-interval '2 minutes'
);
INSERT INTO runtime.account_state_generations VALUES (
    'acctgen-ci-sub','ta-853ac5fcd58185697396d33cd57459fa',
    'ac-ec57819f875f1bfd929a382d6dc8a125',
    'BYBIT:UNIFIED','PROVEN','COMPLETE',clock_timestamp(),0,0
);

-- CI assertions by the unprivileged login, not a mutation grant.
GRANT USAGE ON SCHEMA runtime,control TO cripta;
GRANT SELECT ON runtime.account_entry_safety_blocks,
                control.execution_gates TO cripta;
