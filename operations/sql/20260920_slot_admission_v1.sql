BEGIN;

-- 2026-09-20 canonical slot-admission / position-mode / request-state foundation.
-- This migration changes no execution gate and performs no Exchange mutation.

CREATE TABLE IF NOT EXISTS runtime.position_mode_states (
    position_mode_state_ref text PRIMARY KEY,
    exchange text NOT NULL,
    account_ref text NOT NULL,
    product_category text NOT NULL,
    instrument text NOT NULL,
    position_mode text NOT NULL,
    position_idx integer,
    observed_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    fresh_until timestamptz NOT NULL,
    provenance jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (position_mode IN ('ONE_WAY','HEDGE','UNKNOWN')),
    CHECK (position_idx IS NULL OR position_idx >= 0),
    CHECK (fresh_until >= observed_at),
    CHECK (jsonb_typeof(provenance)='object')
);

CREATE INDEX IF NOT EXISTS ix_position_mode_states_scope_observed
    ON runtime.position_mode_states(account_ref,product_category,instrument,observed_at DESC);

CREATE TABLE IF NOT EXISTS runtime.exchange_position_slot_claims (
    exchange_position_slot_claim_id text PRIMARY KEY,
    exchange_position_key text NOT NULL,
    account_ref text NOT NULL,
    symbol text NOT NULL,
    position_idx integer NOT NULL,
    strategy_attempt_id text NOT NULL UNIQUE
        REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id),
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    direction text NOT NULL,
    position_mode_state_ref text NOT NULL
        REFERENCES runtime.position_mode_states(position_mode_state_ref),
    capital_reservation_id text UNIQUE
        REFERENCES runtime.capital_reservations(reservation_id),
    strategy_position_id text UNIQUE
        REFERENCES runtime.position_ownership(position_id),
    claim_state text NOT NULL,
    claimed_at timestamptz NOT NULL,
    bound_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    released_at timestamptz,
    release_reason text,
    CHECK (position_idx >= 0),
    CHECK (direction IN ('LONG','SHORT')),
    CHECK (claim_state IN ('CLAIMED','BOUND','RECONCILIATION_REQUIRED','RELEASED')),
    CHECK (
        (claim_state='RELEASED' AND released_at IS NOT NULL AND release_reason IS NOT NULL)
        OR
        (claim_state<>'RELEASED' AND released_at IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_exchange_position_slot_claims_active_slot
    ON runtime.exchange_position_slot_claims(exchange_position_key)
    WHERE claim_state IN ('CLAIMED','BOUND','RECONCILIATION_REQUIRED');

CREATE INDEX IF NOT EXISTS ix_exchange_position_slot_claims_position
    ON runtime.exchange_position_slot_claims(strategy_position_id)
    WHERE strategy_position_id IS NOT NULL;

ALTER TABLE strategy_entry.entry_decisions
    ADD COLUMN IF NOT EXISTS exchange_position_slot_claim_id text,
    ADD COLUMN IF NOT EXISTS position_mode_state_ref text;

ALTER TABLE strategy_entry.execution_requests
    ADD COLUMN IF NOT EXISTS exchange_position_slot_claim_id text,
    ADD COLUMN IF NOT EXISTS position_mode_state_ref text;

ALTER TABLE strategy_entry.execution_dispatches
    ADD COLUMN IF NOT EXISTS exchange_position_slot_claim_id text,
    ADD COLUMN IF NOT EXISTS position_mode_state_ref text;

ALTER TABLE runtime.position_ownership
    ADD COLUMN IF NOT EXISTS exchange_position_slot_claim_id text,
    ADD COLUMN IF NOT EXISTS position_mode_state_ref text,
    ADD COLUMN IF NOT EXISTS initial_protection_confirmed_at timestamptz,
    ADD COLUMN IF NOT EXISTS initial_protection_evidence jsonb,
    ADD COLUMN IF NOT EXISTS emergency_policy jsonb;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='entry_decisions_slot_claim_fkey'
          AND conrelid='strategy_entry.entry_decisions'::regclass
    ) THEN
        ALTER TABLE strategy_entry.entry_decisions
            ADD CONSTRAINT entry_decisions_slot_claim_fkey
            FOREIGN KEY (exchange_position_slot_claim_id)
            REFERENCES runtime.exchange_position_slot_claims(exchange_position_slot_claim_id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='entry_decisions_position_mode_state_fkey'
          AND conrelid='strategy_entry.entry_decisions'::regclass
    ) THEN
        ALTER TABLE strategy_entry.entry_decisions
            ADD CONSTRAINT entry_decisions_position_mode_state_fkey
            FOREIGN KEY (position_mode_state_ref)
            REFERENCES runtime.position_mode_states(position_mode_state_ref)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='execution_requests_slot_claim_fkey'
          AND conrelid='strategy_entry.execution_requests'::regclass
    ) THEN
        ALTER TABLE strategy_entry.execution_requests
            ADD CONSTRAINT execution_requests_slot_claim_fkey
            FOREIGN KEY (exchange_position_slot_claim_id)
            REFERENCES runtime.exchange_position_slot_claims(exchange_position_slot_claim_id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='execution_requests_position_mode_state_fkey'
          AND conrelid='strategy_entry.execution_requests'::regclass
    ) THEN
        ALTER TABLE strategy_entry.execution_requests
            ADD CONSTRAINT execution_requests_position_mode_state_fkey
            FOREIGN KEY (position_mode_state_ref)
            REFERENCES runtime.position_mode_states(position_mode_state_ref)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='position_ownership_slot_claim_fkey'
          AND conrelid='runtime.position_ownership'::regclass
    ) THEN
        ALTER TABLE runtime.position_ownership
            ADD CONSTRAINT position_ownership_slot_claim_fkey
            FOREIGN KEY (exchange_position_slot_claim_id)
            REFERENCES runtime.exchange_position_slot_claims(exchange_position_slot_claim_id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='position_ownership_position_mode_state_fkey'
          AND conrelid='runtime.position_ownership'::regclass
    ) THEN
        ALTER TABLE runtime.position_ownership
            ADD CONSTRAINT position_ownership_position_mode_state_fkey
            FOREIGN KEY (position_mode_state_ref)
            REFERENCES runtime.position_mode_states(position_mode_state_ref)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;

ALTER TABLE strategy_entry.entry_decisions
    DROP CONSTRAINT IF EXISTS entry_decisions_decision_code_check;
ALTER TABLE strategy_entry.entry_decisions
    ADD CONSTRAINT entry_decisions_decision_code_check
    CHECK (decision_code IN (
        'ACCEPTED',
        'STRATEGY_CONDITION_REJECTED',
        'INSUFFICIENT_AVAILABLE_FUNDS',
        'EXCHANGE_POSITION_OWNERSHIP_CONFLICT',
        'OPERATIONAL_SAFETY_BLOCKED',
        'STALE_OR_UNKNOWN_REQUIRED_STATE',
        'EXPIRED',
        'CANCELLED'
    ));

CREATE TABLE IF NOT EXISTS strategy_entry.execution_request_state_events (
    request_state_event_id text PRIMARY KEY,
    execution_request_id text NOT NULL
        REFERENCES strategy_entry.execution_requests(execution_request_id),
    state text NOT NULL,
    occurred_at timestamptz NOT NULL,
    reason text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (state IN (
        'REQUEST_PENDING',
        'REQUEST_DISPATCHED',
        'REQUEST_ACKNOWLEDGED',
        'REQUEST_EXPIRED',
        'REQUEST_CANCELLED',
        'REQUEST_RECONCILIATION_REQUIRED',
        'REQUEST_TERMINAL'
    )),
    CHECK (jsonb_typeof(payload)='object')
);

CREATE INDEX IF NOT EXISTS ix_execution_request_state_events_request_time
    ON strategy_entry.execution_request_state_events(execution_request_id,occurred_at);

INSERT INTO strategy_entry.execution_request_state_events(
    request_state_event_id,execution_request_id,state,occurred_at,reason,payload
)
SELECT
    'reqstate-legacy-' || substr(md5(r.execution_request_id),1,24),
    r.execution_request_id,
    'REQUEST_TERMINAL',
    r.created_at,
    'LEGACY_PRE_CANON_REQUEST',
    jsonb_build_object('legacy_pre_canon',true)
FROM strategy_entry.execution_requests r
WHERE NOT EXISTS (
    SELECT 1 FROM strategy_entry.execution_request_state_events e
    WHERE e.execution_request_id=r.execution_request_id
);

ALTER TABLE runtime.lifecycle_faults
    DROP CONSTRAINT IF EXISTS lifecycle_faults_fault_code_check;
ALTER TABLE runtime.lifecycle_faults
    ADD CONSTRAINT lifecycle_faults_fault_code_check
    CHECK (fault_code IN (
        'PLAN_PAIR_INCOMPLETE',
        'ENTRY_PLAN_NOT_CONSUMED',
        'ENTRY_REQUEST_NOT_DISPATCHED',
        'ENTRY_EXECUTION_AMBIGUOUS',
        'POSITION_LINEAGE_INCOMPLETE',
        'POSITION_WITHOUT_EXIT_PLAN',
        'POSITION_WITHOUT_EXIT_OWNER',
        'POSITION_WITHOUT_CONFIRMED_INITIAL_PROTECTION',
        'EXIT_REQUEST_NOT_DISPATCHED',
        'EXIT_EXECUTION_AMBIGUOUS',
        'EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN',
        'EXCHANGE_POSITION_MODE_MISMATCH',
        'EXCHANGE_STATE_DIVERGED',
        'CAPITAL_RESERVATION_STUCK'
    ));

DO $$
DECLARE
    rec record;
BEGIN
    FOR rec IN
        SELECT conname
        FROM pg_constraint
        WHERE conrelid='analytics.counterfactual_candidates'::regclass
          AND contype='c'
          AND pg_get_constraintdef(oid) LIKE '%decision_code%'
    LOOP
        EXECUTE format(
            'ALTER TABLE analytics.counterfactual_candidates DROP CONSTRAINT %I',
            rec.conname
        );
    END LOOP;
END $$;

ALTER TABLE analytics.counterfactual_candidates
    ADD CONSTRAINT counterfactual_candidates_decision_code_check
    CHECK (decision_code IN (
        'INSUFFICIENT_AVAILABLE_FUNDS',
        'EXCHANGE_POSITION_OWNERSHIP_CONFLICT'
    ));

CREATE TABLE IF NOT EXISTS runtime.lifecycle_fault_deliveries (
    delivery_id text PRIMARY KEY,
    fault_id text NOT NULL UNIQUE
        REFERENCES runtime.lifecycle_faults(fault_id),
    channel text NOT NULL,
    state text NOT NULL,
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL,
    last_attempt_at timestamptz,
    delivered_at timestamptz,
    acknowledged_at timestamptz,
    escalation_at timestamptz,
    last_error text,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (channel IN ('OWNER_WEBHOOK')),
    CHECK (state IN ('PENDING','DELIVERED','ACKNOWLEDGED','ESCALATION_REQUIRED')),
    CHECK (attempts >= 0),
    CHECK (jsonb_typeof(payload)='object')
);

ALTER TABLE runtime.trade_lifecycle_events
    ADD COLUMN IF NOT EXISTS exchange_position_slot_claim_id text,
    ADD COLUMN IF NOT EXISTS position_mode_state_ref text,
    ADD COLUMN IF NOT EXISTS capital_reservation_id text;

ALTER TABLE runtime.trade_lifecycle_events
    DROP CONSTRAINT IF EXISTS trade_lifecycle_events_event_type_check;
ALTER TABLE runtime.trade_lifecycle_events
    ADD CONSTRAINT trade_lifecycle_events_event_type_check
    CHECK (event_type IN (
        'STRATEGY_ACTIVATED',
        'PLANS_MATERIALIZED',
        'PLANS_PUBLISHED',
        'ENTRY_PLAN_CONSUMED',
        'STRATEGY_SIGNAL_CREATED',
        'ENTRY_ATTEMPT_CREATED',
        'POSITION_MODE_VALIDATED',
        'EXCHANGE_SLOT_CLAIMED',
        'CAPITAL_RESERVED',
        'ENTRY_DECIDED',
        'ENTRY_REQUEST_CREATED',
        'ENTRY_REQUEST_STATE_CHANGED',
        'ENTRY_REQUEST_DISPATCHED',
        'ENTRY_ORDER_ACKNOWLEDGED',
        'ENTRY_FILLED',
        'STRATEGY_POSITION_CREATED',
        'EXCHANGE_SLOT_BOUND',
        'INITIAL_PROTECTION_CONFIRMED',
        'EXIT_PLAN_BOUND',
        'EXIT_POSITION_CLAIMED',
        'EXIT_DECISION_CREATED',
        'EXIT_REQUEST_CREATED',
        'EXIT_REQUEST_DISPATCHED',
        'EXIT_MUTATION_ACKNOWLEDGED',
        'EXIT_MUTATION_CONFIRMED',
        'POSITION_CLOSED',
        'EXCHANGE_SLOT_RELEASED',
        'CAPITAL_RESERVATION_RELEASED',
        'ECONOMICS_FINALIZED',
        'LIFECYCLE_FAULT',
        'CRITICAL_FAULT_DELIVERY'
    ));

ALTER TABLE strategy_entry.paper_orders
    ALTER COLUMN execution_request_id DROP NOT NULL,
    ALTER COLUMN entry_decision_id DROP NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_paper_orders_strategy_attempt
    ON strategy_entry.paper_orders(strategy_attempt_id);

CREATE TABLE IF NOT EXISTS control.live_arm_evidence (
    evidence_id text PRIMARY KEY,
    check_code text NOT NULL,
    scope_type text NOT NULL,
    scope_key text NOT NULL,
    status text NOT NULL,
    checked_at timestamptz NOT NULL,
    valid_until timestamptz,
    release_commit text NOT NULL,
    source text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (check_code IN (
        'CANON_CURRENT',
        'REMOTE_COMMIT_VERIFIED',
        'SOURCE_LIVE_IDENTITY',
        'TESTS',
        'LIVE_EQUIVALENCE',
        'EXCHANGE_ACCOUNT_IDENTITY',
        'POSITION_MODE_FRESH',
        'POSITION_IDX_EXPECTED',
        'PHYSICAL_SLOT_CLAIM_CONTRACT',
        'CAPITAL_RESERVATION_CONTRACT',
        'EXACT_STRATEGY_ACTIVATION',
        'ENTRY_PLAN_EXECUTABLE',
        'EXIT_PLAN_EXECUTABLE',
        'INITIAL_PROTECTION_EXECUTABLE',
        'TERMINAL_LOSS_CONTAINMENT_PATH',
        'EMERGENCY_POLICY_SUPPORTED',
        'LIFECYCLE_SUPERVISOR_BEHAVIOR',
        'CRITICAL_FAULT_DELIVERY',
        'RECONCILIATION_PATH',
        'MAINNET_GATE_EXPLICIT_OWNER_APPROVAL',
        'MICRO_LIVE_LIMITS',
        'ROLLBACK_OR_KILL_PATH'
    )),
    CHECK (scope_type IN ('GLOBAL','STRATEGY','STRATEGY_SYMBOL')),
    CHECK (status IN ('PASS','FAIL','UNKNOWN','STALE','NOT_CHECKED_HERE')),
    CHECK (length(release_commit)=40),
    CHECK (valid_until IS NULL OR valid_until >= checked_at),
    CHECK (jsonb_typeof(evidence)='object')
);

CREATE INDEX IF NOT EXISTS ix_live_arm_evidence_latest
    ON control.live_arm_evidence(check_code,scope_type,scope_key,checked_at DESC,created_at DESC);

GRANT SELECT,INSERT ON control.live_arm_evidence TO cripta;
REVOKE UPDATE,DELETE ON control.live_arm_evidence FROM cripta;

CREATE TABLE IF NOT EXISTS control.live_arm_sessions (
    live_arm_session_id text PRIMARY KEY,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    strategy_activation_id text NOT NULL,
    symbol text NOT NULL,
    release_commit text NOT NULL,
    state text NOT NULL,
    owner_approved_at timestamptz NOT NULL,
    activated_at timestamptz NOT NULL,
    deactivated_at timestamptz,
    source text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (state IN ('ACTIVE','CLOSED')),
    CHECK (length(release_commit)=40),
    CHECK (
        (state='ACTIVE' AND deactivated_at IS NULL)
        OR
        (state='CLOSED' AND deactivated_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_live_arm_sessions_active_scope
    ON control.live_arm_sessions(
        strategy_id,strategy_version,strategy_config_fingerprint,
        strategy_activation_id,symbol
    )
    WHERE state='ACTIVE';

GRANT SELECT,INSERT,UPDATE ON control.live_arm_sessions TO cripta;
REVOKE DELETE ON control.live_arm_sessions FROM cripta;

COMMIT;
