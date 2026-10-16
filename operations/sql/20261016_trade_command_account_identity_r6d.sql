BEGIN;

SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

ALTER TABLE runtime.trade_commands
    DROP CONSTRAINT IF EXISTS trade_commands_binding_identity_check;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='runtime.trade_commands'::regclass
          AND conname='trade_commands_account_identity_check'
    ) THEN
        ALTER TABLE runtime.trade_commands
            ADD CONSTRAINT trade_commands_account_identity_check
            CHECK (
                (identity_status='LEGACY_UNKNOWN'
                 AND trading_account_id IS NULL
                 AND strategy_execution_binding_id IS NULL
                 AND execution_leg IS NULL)
                OR
                (identity_status='PROVEN'
                 AND trading_account_id IS NOT NULL
                 AND (
                     (strategy_execution_binding_id IS NULL AND execution_leg IS NULL)
                     OR
                     (strategy_execution_binding_id IS NOT NULL AND execution_leg IS NOT NULL)
                 ))
            ) NOT VALID;
    END IF;
END
$$;

COMMIT;