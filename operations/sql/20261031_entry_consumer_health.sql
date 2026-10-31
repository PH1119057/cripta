-- Durable per-account Universal Entry consumer runtime health.
-- No trading policy, gate, arm, order or Exchange mutation is performed here.
CREATE TABLE IF NOT EXISTS runtime.entry_consumer_health (
    trading_account_id text PRIMARY KEY
        REFERENCES identity.trading_accounts(trading_account_id),
    account_connector_id text NOT NULL,
    release_commit text NOT NULL CHECK (release_commit ~ '^[0-9a-f]{40}$'),
    state text NOT NULL CHECK (state IN ('READY','BLOCKED')),
    detail text NOT NULL,
    observed_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS entry_consumer_health_observed_idx
    ON runtime.entry_consumer_health(observed_at);

GRANT SELECT, INSERT, UPDATE ON runtime.entry_consumer_health TO cripta;
REVOKE DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON runtime.entry_consumer_health FROM cripta;
