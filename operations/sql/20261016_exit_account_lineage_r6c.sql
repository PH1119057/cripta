BEGIN;

SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

ALTER TABLE strategy_exit.execution_requests
    ADD COLUMN IF NOT EXISTS strategy_execution_binding_id text,
    ADD COLUMN IF NOT EXISTS execution_leg text,
    ADD COLUMN IF NOT EXISTS trading_account_id text,
    ADD COLUMN IF NOT EXISTS identity_status text NOT NULL DEFAULT 'LEGACY_UNKNOWN';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='strategy_exit.execution_requests'::regclass
          AND conname='exit_execution_requests_strategy_execution_binding_fkey'
    ) THEN
        ALTER TABLE strategy_exit.execution_requests
            ADD CONSTRAINT exit_execution_requests_strategy_execution_binding_fkey
            FOREIGN KEY (strategy_execution_binding_id)
            REFERENCES strategy_entry.strategy_execution_bindings(strategy_execution_binding_id)
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='strategy_exit.execution_requests'::regclass
          AND conname='exit_execution_requests_trading_account_id_fkey'
    ) THEN
        ALTER TABLE strategy_exit.execution_requests
            ADD CONSTRAINT exit_execution_requests_trading_account_id_fkey
            FOREIGN KEY (trading_account_id)
            REFERENCES identity.trading_accounts(trading_account_id)
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='strategy_exit.execution_requests'::regclass
          AND conname='exit_execution_requests_identity_check'
    ) THEN
        ALTER TABLE strategy_exit.execution_requests
            ADD CONSTRAINT exit_execution_requests_identity_check
            CHECK (
                (identity_status='LEGACY_UNKNOWN'
                 AND strategy_execution_binding_id IS NULL
                 AND execution_leg IS NULL
                 AND trading_account_id IS NULL)
                OR
                (identity_status='PROVEN'
                 AND strategy_execution_binding_id IS NOT NULL
                 AND execution_leg IS NOT NULL
                 AND trading_account_id IS NOT NULL)
            ) NOT VALID;
    END IF;
END
$$;

COMMIT;