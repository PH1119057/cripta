-- Account-scoped Entry denial latch. No owner arm, no autodeploy, no seed.
-- An Exchange/worker failure cannot shut down unrelated TradingAccounts.
-- Only the separately authorized DB control actor can DELETE a denial after
-- exact authenticated recovery and approval. Runtime cripta may only latch.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

CREATE TABLE IF NOT EXISTS runtime.account_entry_safety_blocks (
    trading_account_id text NOT NULL
        REFERENCES identity.trading_accounts(trading_account_id),
    account_connector_id text NOT NULL
        REFERENCES identity.account_connectors(account_connector_id),
    blocked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 300),
    PRIMARY KEY (trading_account_id, account_connector_id)
);

GRANT SELECT ON runtime.account_entry_safety_blocks TO cripta;
GRANT INSERT (trading_account_id, account_connector_id, reason)
    ON runtime.account_entry_safety_blocks TO cripta;
-- NO UPDATE, DELETE or TRUNCATE for the private worker.

COMMIT;
