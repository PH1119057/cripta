BEGIN;

SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

CREATE SCHEMA IF NOT EXISTS identity AUTHORIZATION postgres;
GRANT USAGE ON SCHEMA identity TO cripta;

CREATE TABLE IF NOT EXISTS identity.venues (
    venue_id text PRIMARY KEY,
    venue_kind text NOT NULL,
    display_name text NOT NULL,
    status text NOT NULL DEFAULT 'ACTIVE',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS identity.market_sources (
    market_source_id text PRIMARY KEY,
    venue_id text NOT NULL REFERENCES identity.venues(venue_id),
    source_kind text NOT NULL,
    provider_ref text,
    environment text,
    market_scope text,
    status text NOT NULL DEFAULT 'ACTIVE',
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS identity.instruments (
    instrument_id text PRIMARY KEY,
    venue_id text NOT NULL REFERENCES identity.venues(venue_id),
    product_category text,
    external_symbol text,
    base_asset text,
    quote_asset text,
    network text,
    protocol text,
    pool_ref text,
    status text NOT NULL DEFAULT 'ACTIVE',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE IF NOT EXISTS identity.trading_accounts (
    trading_account_id text PRIMARY KEY,
    venue_id text NOT NULL REFERENCES identity.venues(venue_id),
    external_account_ref text,
    parent_trading_account_id text REFERENCES identity.trading_accounts(trading_account_id),
    account_kind text NOT NULL,
    account_type text,
    environment text NOT NULL,
    status text NOT NULL DEFAULT 'ACTIVE',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    retired_at timestamptz,
    CHECK (parent_trading_account_id IS NULL OR parent_trading_account_id <> trading_account_id)
);

CREATE TABLE IF NOT EXISTS identity.account_connectors (
    account_connector_id text PRIMARY KEY,
    trading_account_id text NOT NULL REFERENCES identity.trading_accounts(trading_account_id),
    connector_kind text NOT NULL,
    adapter_id text NOT NULL,
    credential_ref text,
    status text NOT NULL DEFAULT 'ACTIVE',
    config_fingerprint text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    retired_at timestamptz,
    UNIQUE (account_connector_id, trading_account_id)
);

GRANT SELECT ON
    identity.venues,
    identity.market_sources,
    identity.instruments,
    identity.trading_accounts,
    identity.account_connectors
TO cripta;

ALTER TABLE runtime.account_state_generations
    ADD COLUMN IF NOT EXISTS trading_account_id text,
    ADD COLUMN IF NOT EXISTS account_connector_id text;

ALTER TABLE runtime.position_mode_states
    ADD COLUMN IF NOT EXISTS trading_account_id text,
    ADD COLUMN IF NOT EXISTS account_connector_id text,
    ADD COLUMN IF NOT EXISTS instrument_id text;

ALTER TABLE dispatcher_v2.trading_capacity_snapshots
    ADD COLUMN IF NOT EXISTS trading_account_id text,
    ADD COLUMN IF NOT EXISTS account_connector_id text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='account_state_generations_trading_account_id_fkey'
          AND conrelid='runtime.account_state_generations'::regclass
    ) THEN
        ALTER TABLE runtime.account_state_generations
            ADD CONSTRAINT account_state_generations_trading_account_id_fkey
            FOREIGN KEY (trading_account_id)
            REFERENCES identity.trading_accounts(trading_account_id)
            NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='account_state_generations_account_connector_fkey'
          AND conrelid='runtime.account_state_generations'::regclass
    ) THEN
        ALTER TABLE runtime.account_state_generations
            ADD CONSTRAINT account_state_generations_account_connector_fkey
            FOREIGN KEY (account_connector_id, trading_account_id)
            REFERENCES identity.account_connectors(account_connector_id, trading_account_id)
            NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='position_mode_states_trading_account_id_fkey'
          AND conrelid='runtime.position_mode_states'::regclass
    ) THEN
        ALTER TABLE runtime.position_mode_states
            ADD CONSTRAINT position_mode_states_trading_account_id_fkey
            FOREIGN KEY (trading_account_id)
            REFERENCES identity.trading_accounts(trading_account_id)
            NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='position_mode_states_account_connector_fkey'
          AND conrelid='runtime.position_mode_states'::regclass
    ) THEN
        ALTER TABLE runtime.position_mode_states
            ADD CONSTRAINT position_mode_states_account_connector_fkey
            FOREIGN KEY (account_connector_id, trading_account_id)
            REFERENCES identity.account_connectors(account_connector_id, trading_account_id)
            NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='position_mode_states_instrument_id_fkey'
          AND conrelid='runtime.position_mode_states'::regclass
    ) THEN
        ALTER TABLE runtime.position_mode_states
            ADD CONSTRAINT position_mode_states_instrument_id_fkey
            FOREIGN KEY (instrument_id)
            REFERENCES identity.instruments(instrument_id)
            NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='trading_capacity_snapshots_trading_account_id_fkey'
          AND conrelid='dispatcher_v2.trading_capacity_snapshots'::regclass
    ) THEN
        ALTER TABLE dispatcher_v2.trading_capacity_snapshots
            ADD CONSTRAINT trading_capacity_snapshots_trading_account_id_fkey
            FOREIGN KEY (trading_account_id)
            REFERENCES identity.trading_accounts(trading_account_id)
            NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='trading_capacity_snapshots_account_connector_fkey'
          AND conrelid='dispatcher_v2.trading_capacity_snapshots'::regclass
    ) THEN
        ALTER TABLE dispatcher_v2.trading_capacity_snapshots
            ADD CONSTRAINT trading_capacity_snapshots_account_connector_fkey
            FOREIGN KEY (account_connector_id, trading_account_id)
            REFERENCES identity.account_connectors(account_connector_id, trading_account_id)
            NOT VALID;
    END IF;
END
$$;

COMMIT;
