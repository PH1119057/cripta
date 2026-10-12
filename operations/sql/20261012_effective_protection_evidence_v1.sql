-- Append-only independent Exchange protection observations.
-- Prepared migration; do not run outside the verified release workflow.
BEGIN;

CREATE TABLE IF NOT EXISTS runtime.effective_protection_evidence (
    evidence_id text PRIMARY KEY,
    observed_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    command_id text,
    strategy_position_id text,
    account_ref text NOT NULL,
    exchange_position_key text NOT NULL,
    symbol text NOT NULL,
    position_idx integer NOT NULL,
    evidence_source text NOT NULL
        CHECK (evidence_source = 'BYBIT_AUTHENTICATED_ACCOUNT_GET'),
    state text NOT NULL CHECK (state IN ('OBSERVED_PRESENT','NOT_VERIFIED','UNKNOWN')),
    stop_loss numeric,
    position_take_profit numeric,
    maker_take_profit_order_ids jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(maker_take_profit_order_ids) = 'array'),
    reason text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(evidence) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_effective_protection_exact_scope_time
 ON runtime.effective_protection_evidence
 (account_ref, symbol, position_idx, observed_at DESC, recorded_at DESC);

CREATE INDEX IF NOT EXISTS ix_effective_protection_command
 ON runtime.effective_protection_evidence
 (command_id, observed_at DESC)
 WHERE command_id IS NOT NULL;

-- DB grants and effective writer responsibility require dedicated
-- DEVELOPMENT_RELEASE review before deployment; no blanket privileges here.
COMMIT;
