-- New account-scoped monitoring only. No UPDATE/DELETE of historical M3 rows.
-- Versioned additive migration, applied only by approved production DB actor.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

CREATE SCHEMA IF NOT EXISTS supervisor;
CREATE TABLE IF NOT EXISTS supervisor.account_position_snapshots (
    sample_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trading_account_id TEXT NOT NULL REFERENCES identity.trading_accounts(trading_account_id),
    position_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    position_idx SMALLINT NOT NULL CHECK (position_idx IN (0,1,2)),
    side TEXT NOT NULL CHECK (side IN ('Buy','Sell')),
    observed_at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL CHECK (source='ACCOUNT_SCOPED_HOT_MARK'),
    completeness TEXT NOT NULL CHECK (completeness='SAMPLED_PARTIAL'),
    entry_price NUMERIC NOT NULL CHECK (entry_price>0),
    mark_price NUMERIC NOT NULL CHECK (mark_price>0),
    return_pct NUMERIC NOT NULL,
    sampled_mfe_pct NUMERIC NOT NULL CHECK (sampled_mfe_pct>=0),
    sampled_mae_pct NUMERIC NOT NULL CHECK (sampled_mae_pct<=0),
    protection_state TEXT NOT NULL DEFAULT 'UNKNOWN'
        CHECK (protection_state IN ('UNKNOWN','NOT_VERIFIED','VERIFIED')),
    geometry_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_account_position_sample
        UNIQUE(trading_account_id,position_id,observed_at)
);
CREATE INDEX IF NOT EXISTS ix_account_position_snapshots_latest
    ON supervisor.account_position_snapshots(trading_account_id,position_id,observed_at DESC);
GRANT USAGE ON SCHEMA supervisor TO cripta;
GRANT SELECT, INSERT ON supervisor.account_position_snapshots TO cripta;
GRANT USAGE, SELECT ON SEQUENCE supervisor.account_position_snapshots_sample_id_seq TO cripta;

COMMIT;
