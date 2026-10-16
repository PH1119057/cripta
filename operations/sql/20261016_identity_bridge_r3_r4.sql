BEGIN;

SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

ALTER TABLE runtime.account_state_generations
    ADD COLUMN IF NOT EXISTS identity_status text NOT NULL DEFAULT 'LEGACY_UNKNOWN';

ALTER TABLE runtime.position_mode_states
    ADD COLUMN IF NOT EXISTS identity_status text NOT NULL DEFAULT 'LEGACY_UNKNOWN';

ALTER TABLE dispatcher_v2.trading_capacity_snapshots
    ADD COLUMN IF NOT EXISTS identity_status text NOT NULL DEFAULT 'LEGACY_UNKNOWN';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='account_state_generations_identity_status_check'
          AND conrelid='runtime.account_state_generations'::regclass
    ) THEN
        ALTER TABLE runtime.account_state_generations
            ADD CONSTRAINT account_state_generations_identity_status_check
            CHECK (identity_status IN ('PROVEN','LEGACY_UNKNOWN'))
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='position_mode_states_identity_status_check'
          AND conrelid='runtime.position_mode_states'::regclass
    ) THEN
        ALTER TABLE runtime.position_mode_states
            ADD CONSTRAINT position_mode_states_identity_status_check
            CHECK (identity_status IN ('PROVEN','LEGACY_UNKNOWN'))
            NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='trading_capacity_snapshots_identity_status_check'
          AND conrelid='dispatcher_v2.trading_capacity_snapshots'::regclass
    ) THEN
        ALTER TABLE dispatcher_v2.trading_capacity_snapshots
            ADD CONSTRAINT trading_capacity_snapshots_identity_status_check
            CHECK (identity_status IN ('PROVEN','LEGACY_UNKNOWN'))
            NOT VALID;
    END IF;
END
$$;

COMMIT;
