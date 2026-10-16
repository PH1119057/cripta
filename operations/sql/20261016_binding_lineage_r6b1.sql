BEGIN;

SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

ALTER TABLE runtime.capital_reservations
    ADD COLUMN IF NOT EXISTS strategy_execution_binding_id text,
    ADD COLUMN IF NOT EXISTS execution_leg text,
    ADD COLUMN IF NOT EXISTS trading_account_id text,
    ADD COLUMN IF NOT EXISTS identity_status text NOT NULL DEFAULT 'LEGACY_UNKNOWN';

ALTER TABLE runtime.exchange_position_slot_claims
    ADD COLUMN IF NOT EXISTS strategy_execution_binding_id text,
    ADD COLUMN IF NOT EXISTS execution_leg text,
    ADD COLUMN IF NOT EXISTS trading_account_id text,
    ADD COLUMN IF NOT EXISTS identity_status text NOT NULL DEFAULT 'LEGACY_UNKNOWN';

ALTER TABLE strategy_entry.entry_decisions
    ADD COLUMN IF NOT EXISTS strategy_execution_binding_id text,
    ADD COLUMN IF NOT EXISTS execution_leg text,
    ADD COLUMN IF NOT EXISTS trading_account_id text,
    ADD COLUMN IF NOT EXISTS identity_status text NOT NULL DEFAULT 'LEGACY_UNKNOWN';

ALTER TABLE strategy_entry.execution_requests
    ADD COLUMN IF NOT EXISTS strategy_execution_binding_id text,
    ADD COLUMN IF NOT EXISTS execution_leg text,
    ADD COLUMN IF NOT EXISTS trading_account_id text,
    ADD COLUMN IF NOT EXISTS identity_status text NOT NULL DEFAULT 'LEGACY_UNKNOWN';

DO $$
DECLARE item record;
BEGIN
    FOR item IN
        SELECT * FROM (VALUES
            ('runtime.capital_reservations'::regclass, 'capital_reservations'),
            ('runtime.exchange_position_slot_claims'::regclass, 'exchange_position_slot_claims'),
            ('strategy_entry.entry_decisions'::regclass, 'entry_decisions'),
            ('strategy_entry.execution_requests'::regclass, 'execution_requests')
        ) AS v(relid,prefix)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid=item.relid
              AND conname=item.prefix || '_strategy_execution_binding_fkey'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %s ADD CONSTRAINT %I FOREIGN KEY (strategy_execution_binding_id) REFERENCES strategy_entry.strategy_execution_bindings(strategy_execution_binding_id) NOT VALID',
                item.relid,
                item.prefix || '_strategy_execution_binding_fkey'
            );
        END IF;
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
              AND conname=item.prefix || '_binding_identity_check'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %s ADD CONSTRAINT %I CHECK (
                    (identity_status=''LEGACY_UNKNOWN''
                     AND strategy_execution_binding_id IS NULL
                     AND execution_leg IS NULL
                     AND trading_account_id IS NULL)
                    OR
                    (identity_status=''PROVEN''
                     AND strategy_execution_binding_id IS NOT NULL
                     AND execution_leg IS NOT NULL
                     AND trading_account_id IS NOT NULL)
                ) NOT VALID',
                item.relid,
                item.prefix || '_binding_identity_check'
            );
        END IF;
    END LOOP;
END
$$;

COMMIT;
