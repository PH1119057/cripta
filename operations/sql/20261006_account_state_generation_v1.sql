BEGIN;

-- Owner decision 2026-10-06:
-- real Entry admission uses one coherent account-state generation rather than
-- independent wallet/reconciliation/capacity wall-clock age gates.
-- This migration changes no execution gate and performs no Exchange mutation.

CREATE TABLE IF NOT EXISTS runtime.account_state_generations (
    generation_id text PRIMARY KEY,
    account_ref text NOT NULL,
    reason text NOT NULL,
    state text NOT NULL,
    started_at timestamptz NOT NULL,
    completed_at timestamptz,
    account_type text,
    total_equity numeric,
    wallet_balance numeric,
    available_balance numeric,
    positions_count integer,
    active_orders_count integer,
    position_mode_refs jsonb NOT NULL DEFAULT '{}'::jsonb,
    wallet_payload jsonb,
    error text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (state IN ('COLLECTING','COMPLETE','FAILED')),
    CHECK (positions_count IS NULL OR positions_count >= 0),
    CHECK (active_orders_count IS NULL OR active_orders_count >= 0),
    CHECK (jsonb_typeof(position_mode_refs)='object'),
    CHECK (wallet_payload IS NULL OR jsonb_typeof(wallet_payload)='object'),
    CHECK (
        (state='COLLECTING' AND completed_at IS NULL)
        OR
        (state='FAILED' AND completed_at IS NOT NULL)
        OR
        (
            state='COMPLETE'
            AND completed_at IS NOT NULL
            AND account_type IS NOT NULL
            AND total_equity IS NOT NULL
            AND wallet_balance IS NOT NULL
            AND available_balance IS NOT NULL
            AND positions_count IS NOT NULL
            AND active_orders_count IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS ix_account_state_generations_account_started
    ON runtime.account_state_generations(account_ref,started_at DESC);

GRANT SELECT,INSERT,UPDATE ON runtime.account_state_generations TO cripta;

-- Slot claim and capital reservation are members of the same outer admission
-- transaction. The capital FK is already deferred; make the slot-attempt FK
-- equally deferred so no child-before-parent state can escape COMMIT.
ALTER TABLE runtime.exchange_position_slot_claims
    DROP CONSTRAINT IF EXISTS exchange_position_slot_claims_strategy_attempt_id_fkey;

ALTER TABLE runtime.exchange_position_slot_claims
    ADD CONSTRAINT exchange_position_slot_claims_strategy_attempt_id_fkey
    FOREIGN KEY (strategy_attempt_id)
    REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id)
    DEFERRABLE INITIALLY DEFERRED;

COMMIT;