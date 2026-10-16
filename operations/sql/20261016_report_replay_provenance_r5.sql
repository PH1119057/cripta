BEGIN;

SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

ALTER TABLE research_context.dispatcher_v2_event_links
    ADD COLUMN IF NOT EXISTS trading_account_id text,
    ADD COLUMN IF NOT EXISTS identity_status text NOT NULL DEFAULT 'LEGACY_UNKNOWN';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='research_context.dispatcher_v2_event_links'::regclass
          AND conname='dispatcher_v2_event_links_trading_account_id_fkey'
    ) THEN
        ALTER TABLE research_context.dispatcher_v2_event_links
            ADD CONSTRAINT dispatcher_v2_event_links_trading_account_id_fkey
            FOREIGN KEY (trading_account_id)
            REFERENCES identity.trading_accounts(trading_account_id)
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='research_context.dispatcher_v2_event_links'::regclass
          AND conname='dispatcher_v2_event_links_identity_status_check'
    ) THEN
        ALTER TABLE research_context.dispatcher_v2_event_links
            ADD CONSTRAINT dispatcher_v2_event_links_identity_status_check
            CHECK (
                (identity_status='LEGACY_UNKNOWN' AND trading_account_id IS NULL)
                OR
                (identity_status='PROVEN' AND trading_account_id IS NOT NULL)
            )
            NOT VALID;
    END IF;
END
$$;

COMMIT;
