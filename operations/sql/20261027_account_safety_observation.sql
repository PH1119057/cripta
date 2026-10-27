-- Account-isolated independent signed GET safety snapshots; additive only.
-- Old MAIN safety.exchange_snapshots remain untouched and non-attributed.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

CREATE SCHEMA IF NOT EXISTS safety;
CREATE TABLE IF NOT EXISTS safety.account_exchange_snapshots (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trading_account_id TEXT NOT NULL
        REFERENCES identity.trading_accounts(trading_account_id),
    account_connector_id TEXT NOT NULL
        REFERENCES identity.account_connectors(account_connector_id),
    observed_at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL CHECK (source='SIGNED_BYBIT_ACCOUNT_GET'),
    evidence_status TEXT NOT NULL CHECK (evidence_status='VERIFIED'),
    total_equity NUMERIC NOT NULL CHECK (total_equity>=0),
    available_balance NUMERIC NOT NULL CHECK (available_balance>=0),
    wallet_balance NUMERIC NOT NULL CHECK (wallet_balance>=0),
    open_positions INTEGER NOT NULL CHECK (open_positions>=0),
    open_orders INTEGER NOT NULL CHECK (open_orders>=0),
    protection_checked_count INTEGER NOT NULL CHECK (protection_checked_count>=0),
    protection_verified_count INTEGER NOT NULL CHECK (protection_verified_count>=0),
    CONSTRAINT uq_safety_account_observation
        UNIQUE(trading_account_id,account_connector_id,observed_at)
);
CREATE INDEX IF NOT EXISTS ix_safety_account_exchange_snapshots_recent
    ON safety.account_exchange_snapshots(trading_account_id, observed_at DESC);
GRANT USAGE ON SCHEMA safety TO cripta;
GRANT SELECT, INSERT ON safety.account_exchange_snapshots TO cripta;
GRANT USAGE, SELECT ON SEQUENCE safety.account_exchange_snapshots_id_seq TO cripta;
COMMIT;
