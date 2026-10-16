BEGIN;

SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

ALTER TABLE runtime.executions
    ADD COLUMN IF NOT EXISTS trading_account_id text,
    ADD COLUMN IF NOT EXISTS account_connector_id text,
    ADD COLUMN IF NOT EXISTS identity_status text NOT NULL DEFAULT 'LEGACY_UNKNOWN';

ALTER TABLE runtime.exchange_order_history
    ADD COLUMN IF NOT EXISTS trading_account_id text,
    ADD COLUMN IF NOT EXISTS account_connector_id text,
    ADD COLUMN IF NOT EXISTS identity_status text NOT NULL DEFAULT 'LEGACY_UNKNOWN';

DO $$
DECLARE
    item record;
BEGIN
    FOR item IN
        SELECT * FROM (VALUES
            ('runtime.executions'::regclass, 'executions'),
            ('runtime.exchange_order_history'::regclass, 'exchange_order_history')
        ) AS v(relid,prefix)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid=item.relid
              AND conname=item.prefix || '_trading_account_id_fkey'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %s ADD CONSTRAINT %I FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID',
                item.relid,
                item.prefix || '_trading_account_id_fkey'
            );
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid=item.relid
              AND conname=item.prefix || '_account_connector_fkey'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %s ADD CONSTRAINT %I FOREIGN KEY (account_connector_id,trading_account_id) REFERENCES identity.account_connectors(account_connector_id,trading_account_id) NOT VALID',
                item.relid,
                item.prefix || '_account_connector_fkey'
            );
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid=item.relid
              AND conname=item.prefix || '_identity_status_check'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %s ADD CONSTRAINT %I CHECK (identity_status IN (''PROVEN'',''LEGACY_UNKNOWN'')) NOT VALID',
                item.relid,
                item.prefix || '_identity_status_check'
            );
        END IF;
    END LOOP;
END
$$;

COMMIT;
