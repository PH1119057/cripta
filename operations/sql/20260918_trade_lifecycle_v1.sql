BEGIN;

-- Technical support schema additions for the owner-approved Strategy/Entry/Exit lifecycle.
-- This migration adds no trading policy and does not enable any execution gate.

ALTER TABLE runtime.position_ownership
    ADD COLUMN IF NOT EXISTS account_ref text,
    ADD COLUMN IF NOT EXISTS strategy_config_fingerprint text,
    ADD COLUMN IF NOT EXISTS strategy_activation_id text,
    ADD COLUMN IF NOT EXISTS strategy_attempt_id text,
    ADD COLUMN IF NOT EXISTS entry_decision_id text,
    ADD COLUMN IF NOT EXISTS entry_execution_request_id text,
    ADD COLUMN IF NOT EXISTS entry_plan_fingerprint text,
    ADD COLUMN IF NOT EXISTS exit_plan_fingerprint text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='position_ownership_strategy_activation_fkey'
          AND conrelid='runtime.position_ownership'::regclass
    ) THEN
        ALTER TABLE runtime.position_ownership
            ADD CONSTRAINT position_ownership_strategy_activation_fkey
            FOREIGN KEY (strategy_activation_id)
            REFERENCES strategy_entry.strategy_activations(activation_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='position_ownership_strategy_attempt_fkey'
          AND conrelid='runtime.position_ownership'::regclass
    ) THEN
        ALTER TABLE runtime.position_ownership
            ADD CONSTRAINT position_ownership_strategy_attempt_fkey
            FOREIGN KEY (strategy_attempt_id)
            REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='position_ownership_entry_decision_fkey'
          AND conrelid='runtime.position_ownership'::regclass
    ) THEN
        ALTER TABLE runtime.position_ownership
            ADD CONSTRAINT position_ownership_entry_decision_fkey
            FOREIGN KEY (entry_decision_id)
            REFERENCES strategy_entry.entry_decisions(entry_decision_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='position_ownership_entry_request_fkey'
          AND conrelid='runtime.position_ownership'::regclass
    ) THEN
        ALTER TABLE runtime.position_ownership
            ADD CONSTRAINT position_ownership_entry_request_fkey
            FOREIGN KEY (entry_execution_request_id)
            REFERENCES strategy_entry.execution_requests(execution_request_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='position_ownership_entry_plan_fkey'
          AND conrelid='runtime.position_ownership'::regclass
    ) THEN
        ALTER TABLE runtime.position_ownership
            ADD CONSTRAINT position_ownership_entry_plan_fkey
            FOREIGN KEY (entry_plan_fingerprint)
            REFERENCES strategy_entry.entry_plans(entry_plan_fingerprint);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='position_ownership_exit_plan_fkey'
          AND conrelid='runtime.position_ownership'::regclass
    ) THEN
        ALTER TABLE runtime.position_ownership
            ADD CONSTRAINT position_ownership_exit_plan_fkey
            FOREIGN KEY (exit_plan_fingerprint)
            REFERENCES strategy_entry.exit_plans(exit_plan_fingerprint);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='position_ownership_universal_lineage_check'
          AND conrelid='runtime.position_ownership'::regclass
    ) THEN
        ALTER TABLE runtime.position_ownership
            ADD CONSTRAINT position_ownership_universal_lineage_check
            CHECK (
                bot_instance_id <> 'universal-entry'
                OR (
                    account_ref IS NOT NULL
                    AND strategy_config_fingerprint IS NOT NULL
                    AND strategy_activation_id IS NOT NULL
                    AND strategy_attempt_id IS NOT NULL
                    AND entry_decision_id IS NOT NULL
                    AND entry_execution_request_id IS NOT NULL
                    AND entry_plan_fingerprint IS NOT NULL
                    AND exit_plan_fingerprint IS NOT NULL
                    AND exchange_position_key IS NOT NULL
                )
            );
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_position_ownership_active_exchange_slot
    ON runtime.position_ownership(exchange_position_key)
    WHERE state IN ('OPEN','RECONCILIATION_REQUIRED')
      AND exchange_position_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS runtime.plan_consumptions (
    plan_consumption_id text PRIMARY KEY,
    plan_kind text NOT NULL,
    entry_plan_fingerprint text REFERENCES strategy_entry.entry_plans(entry_plan_fingerprint),
    exit_plan_fingerprint text REFERENCES strategy_entry.exit_plans(exit_plan_fingerprint),
    strategy_activation_id text NOT NULL
        REFERENCES strategy_entry.strategy_activations(activation_id),
    consumer_kind text NOT NULL,
    consumer_instance_id text NOT NULL,
    loaded_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    status text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (plan_kind IN ('ENTRY','EXIT')),
    CHECK (consumer_kind IN ('ENTRY_ENGINE','EXIT_ENGINE')),
    CHECK (status IN ('LOADED','STALE','ERROR')),
    CHECK (jsonb_typeof(payload)='object'),
    CHECK (
        (plan_kind='ENTRY' AND entry_plan_fingerprint IS NOT NULL
                           AND exit_plan_fingerprint IS NULL
                           AND consumer_kind='ENTRY_ENGINE')
        OR
        (plan_kind='EXIT' AND exit_plan_fingerprint IS NOT NULL
                          AND entry_plan_fingerprint IS NULL
                          AND consumer_kind='EXIT_ENGINE')
    ),
    UNIQUE NULLS NOT DISTINCT (
        plan_kind,entry_plan_fingerprint,exit_plan_fingerprint,
        strategy_activation_id,consumer_kind,consumer_instance_id
    )
);

CREATE OR REPLACE FUNCTION runtime.guard_plan_consumption_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(
        OLD.plan_consumption_id,OLD.plan_kind,OLD.entry_plan_fingerprint,
        OLD.exit_plan_fingerprint,OLD.strategy_activation_id,OLD.consumer_kind,
        OLD.consumer_instance_id,OLD.loaded_at,OLD.created_at
    ) IS DISTINCT FROM ROW(
        NEW.plan_consumption_id,NEW.plan_kind,NEW.entry_plan_fingerprint,
        NEW.exit_plan_fingerprint,NEW.strategy_activation_id,NEW.consumer_kind,
        NEW.consumer_instance_id,NEW.loaded_at,NEW.created_at
    ) THEN
        RAISE EXCEPTION 'plan consumption identity is immutable';
    END IF;
    IF NEW.last_seen_at < OLD.last_seen_at THEN
        RAISE EXCEPTION 'plan consumption last_seen_at cannot move backwards';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS plan_consumptions_guard_update ON runtime.plan_consumptions;
CREATE TRIGGER plan_consumptions_guard_update
BEFORE UPDATE ON runtime.plan_consumptions
FOR EACH ROW EXECUTE FUNCTION runtime.guard_plan_consumption_update();

CREATE TABLE IF NOT EXISTS runtime.position_exit_claims (
    claim_id text PRIMARY KEY,
    strategy_position_id text NOT NULL UNIQUE
        REFERENCES runtime.position_ownership(position_id),
    exit_plan_fingerprint text NOT NULL
        REFERENCES strategy_entry.exit_plans(exit_plan_fingerprint),
    strategy_activation_id text NOT NULL
        REFERENCES strategy_entry.strategy_activations(activation_id),
    consumer_instance_id text NOT NULL,
    claimed_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    status text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (status IN ('CLAIMED','STALE','ERROR')),
    CHECK (jsonb_typeof(payload)='object')
);

CREATE OR REPLACE FUNCTION runtime.guard_position_exit_claim_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(
        OLD.claim_id,OLD.strategy_position_id,OLD.exit_plan_fingerprint,
        OLD.strategy_activation_id,OLD.consumer_instance_id,OLD.claimed_at,
        OLD.created_at
    ) IS DISTINCT FROM ROW(
        NEW.claim_id,NEW.strategy_position_id,NEW.exit_plan_fingerprint,
        NEW.strategy_activation_id,NEW.consumer_instance_id,NEW.claimed_at,
        NEW.created_at
    ) THEN
        RAISE EXCEPTION 'position exit claim identity is immutable';
    END IF;
    IF NEW.last_seen_at < OLD.last_seen_at THEN
        RAISE EXCEPTION 'position exit claim last_seen_at cannot move backwards';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS position_exit_claims_guard_update ON runtime.position_exit_claims;
CREATE TRIGGER position_exit_claims_guard_update
BEFORE UPDATE ON runtime.position_exit_claims
FOR EACH ROW EXECUTE FUNCTION runtime.guard_position_exit_claim_update();

CREATE TABLE IF NOT EXISTS runtime.capital_reservations (
    reservation_id text PRIMARY KEY,
    account_ref text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    signal_id text NOT NULL,
    strategy_attempt_id text NOT NULL UNIQUE,
    requested_amount numeric NOT NULL,
    amount_currency text NOT NULL,
    capacity_snapshot_id text NOT NULL,
    capacity_observed_at timestamptz NOT NULL,
    capacity_available_at_reservation numeric NOT NULL,
    pre_dispatch_expires_at timestamptz NOT NULL,
    state text NOT NULL,
    state_reason text,
    exchange_commitment_ref text,
    exchange_commitment_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (requested_amount > 0),
    CHECK (capacity_available_at_reservation >= 0),
    CHECK (amount_currency <> ''),
    CHECK (state IN (
        'RESERVED',
        'DISPATCHED',
        'PENDING_EXCHANGE_REFLECTION',
        'CONSUMED',
        'RELEASED',
        'RECONCILIATION_REQUIRED'
    )),
    FOREIGN KEY (
        strategy_attempt_id,signal_id,strategy_id,strategy_version,
        strategy_config_fingerprint,entry_plan_fingerprint
    ) REFERENCES strategy_entry.strategy_attempts(
        strategy_attempt_id,signal_id,strategy_id,strategy_version,
        strategy_config_fingerprint,entry_plan_fingerprint
    ) DEFERRABLE INITIALLY DEFERRED
);

ALTER TABLE runtime.capital_reservations
    ADD COLUMN IF NOT EXISTS strategy_position_id text,
    ADD COLUMN IF NOT EXISTS pre_dispatch_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS state_reason text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='capital_reservations_strategy_position_fkey'
          AND conrelid='runtime.capital_reservations'::regclass
    ) THEN
        ALTER TABLE runtime.capital_reservations
            ADD CONSTRAINT capital_reservations_strategy_position_fkey
            FOREIGN KEY (strategy_position_id)
            REFERENCES runtime.position_ownership(position_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_capital_reservations_reserved_expiry
    ON runtime.capital_reservations(pre_dispatch_expires_at)
    WHERE state='RESERVED';

CREATE INDEX IF NOT EXISTS ix_capital_reservations_account_active
    ON runtime.capital_reservations(account_ref,state,created_at)
    WHERE state IN (
        'RESERVED','DISPATCHED','PENDING_EXCHANGE_REFLECTION',
        'CONSUMED','RECONCILIATION_REQUIRED'
    );

CREATE OR REPLACE FUNCTION runtime.guard_capital_reservation_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(
        OLD.reservation_id,OLD.account_ref,OLD.strategy_id,OLD.strategy_version,
        OLD.strategy_config_fingerprint,OLD.entry_plan_fingerprint,OLD.signal_id,
        OLD.strategy_attempt_id,OLD.requested_amount,OLD.amount_currency,
        OLD.capacity_snapshot_id,OLD.capacity_observed_at,
        OLD.capacity_available_at_reservation,OLD.pre_dispatch_expires_at,
        OLD.created_at
    ) IS DISTINCT FROM ROW(
        NEW.reservation_id,NEW.account_ref,NEW.strategy_id,NEW.strategy_version,
        NEW.strategy_config_fingerprint,NEW.entry_plan_fingerprint,NEW.signal_id,
        NEW.strategy_attempt_id,NEW.requested_amount,NEW.amount_currency,
        NEW.capacity_snapshot_id,NEW.capacity_observed_at,
        NEW.capacity_available_at_reservation,NEW.pre_dispatch_expires_at,
        NEW.created_at
    ) THEN
        RAISE EXCEPTION 'capital reservation identity is immutable';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS capital_reservations_guard_update ON runtime.capital_reservations;
CREATE TRIGGER capital_reservations_guard_update
BEFORE UPDATE ON runtime.capital_reservations
FOR EACH ROW EXECUTE FUNCTION runtime.guard_capital_reservation_update();

ALTER TABLE strategy_entry.entry_decisions
    ADD COLUMN IF NOT EXISTS capital_reservation_id text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='entry_decisions_capital_reservation_fkey'
          AND conrelid='strategy_entry.entry_decisions'::regclass
    ) THEN
        ALTER TABLE strategy_entry.entry_decisions
            ADD CONSTRAINT entry_decisions_capital_reservation_fkey
            FOREIGN KEY (capital_reservation_id)
            REFERENCES runtime.capital_reservations(reservation_id);
    END IF;
END $$;

ALTER TABLE strategy_entry.execution_requests
    ADD COLUMN IF NOT EXISTS exit_plan_fingerprint text,
    ADD COLUMN IF NOT EXISTS capital_reservation_id text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='execution_requests_exit_plan_fkey'
          AND conrelid='strategy_entry.execution_requests'::regclass
    ) THEN
        ALTER TABLE strategy_entry.execution_requests
            ADD CONSTRAINT execution_requests_exit_plan_fkey
            FOREIGN KEY (exit_plan_fingerprint)
            REFERENCES strategy_entry.exit_plans(exit_plan_fingerprint);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='execution_requests_capital_reservation_fkey'
          AND conrelid='strategy_entry.execution_requests'::regclass
    ) THEN
        ALTER TABLE strategy_entry.execution_requests
            ADD CONSTRAINT execution_requests_capital_reservation_fkey
            FOREIGN KEY (capital_reservation_id)
            REFERENCES runtime.capital_reservations(reservation_id);
    END IF;
END $$;

ALTER TABLE strategy_entry.execution_dispatches
    ADD COLUMN IF NOT EXISTS capital_reservation_id text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='execution_dispatches_capital_reservation_fkey'
          AND conrelid='strategy_entry.execution_dispatches'::regclass
    ) THEN
        ALTER TABLE strategy_entry.execution_dispatches
            ADD CONSTRAINT execution_dispatches_capital_reservation_fkey
            FOREIGN KEY (capital_reservation_id)
            REFERENCES runtime.capital_reservations(reservation_id);
    END IF;
END $$;

CREATE SCHEMA IF NOT EXISTS strategy_exit AUTHORIZATION postgres;
REVOKE ALL ON SCHEMA strategy_exit FROM PUBLIC;
GRANT USAGE ON SCHEMA strategy_exit TO cripta;

CREATE TABLE IF NOT EXISTS strategy_exit.exit_observations (
    observation_id text PRIMARY KEY,
    strategy_position_id text NOT NULL
        REFERENCES runtime.position_ownership(position_id),
    exit_plan_fingerprint text NOT NULL
        REFERENCES strategy_entry.exit_plans(exit_plan_fingerprint),
    symbol text NOT NULL,
    event_kind text NOT NULL,
    event_at timestamptz NOT NULL,
    observed_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    attributes jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (jsonb_typeof(attributes)='object'),
    CHECK (jsonb_typeof(source_refs)='array'),
    UNIQUE (observation_id,strategy_position_id,exit_plan_fingerprint)
);

ALTER TABLE strategy_exit.exit_observations
    ADD COLUMN IF NOT EXISTS event_at timestamptz,
    ADD COLUMN IF NOT EXISTS received_at timestamptz;

CREATE TABLE IF NOT EXISTS strategy_exit.exit_decisions (
    exit_decision_id text PRIMARY KEY,
    strategy_position_id text NOT NULL
        REFERENCES runtime.position_ownership(position_id),
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    exit_plan_fingerprint text NOT NULL,
    observation_id text NOT NULL,
    rule_id text NOT NULL,
    rule_priority integer NOT NULL,
    repeat_policy text NOT NULL,
    action_kind text NOT NULL,
    requested_mutation jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    decided_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (action_kind IN (
        'SET_STOP','SET_TP','SET_PROTECTION','SET_TRAILING','REDUCE','CLOSE'
    )),
    CONSTRAINT exit_decisions_repeat_policy_check
        CHECK (repeat_policy IN ('ONCE_PER_POSITION','EACH_MATCH')),
    CHECK (jsonb_typeof(requested_mutation)='object'),
    CHECK (jsonb_typeof(source_refs)='array'),
    UNIQUE (
        exit_decision_id,strategy_position_id,strategy_id,strategy_version,
        strategy_config_fingerprint,exit_plan_fingerprint
    ),
    FOREIGN KEY (
        exit_plan_fingerprint,strategy_id,strategy_version,strategy_config_fingerprint
    ) REFERENCES strategy_entry.exit_plans(
        exit_plan_fingerprint,strategy_id,strategy_version,strategy_config_fingerprint
    ),
    CONSTRAINT exit_decisions_observation_fkey
        FOREIGN KEY (
            observation_id,strategy_position_id,exit_plan_fingerprint
        ) REFERENCES strategy_exit.exit_observations(
            observation_id,strategy_position_id,exit_plan_fingerprint
        )
);

ALTER TABLE strategy_exit.exit_decisions
    ADD COLUMN IF NOT EXISTS observation_id text,
    ADD COLUMN IF NOT EXISTS rule_priority integer,
    ADD COLUMN IF NOT EXISTS repeat_policy text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='exit_decisions_observation_fkey'
          AND conrelid='strategy_exit.exit_decisions'::regclass
    ) THEN
        ALTER TABLE strategy_exit.exit_decisions
            ADD CONSTRAINT exit_decisions_observation_fkey
            FOREIGN KEY (observation_id,strategy_position_id,exit_plan_fingerprint)
            REFERENCES strategy_exit.exit_observations(
                observation_id,strategy_position_id,exit_plan_fingerprint
            ) NOT VALID;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='exit_decisions_repeat_policy_check'
          AND conrelid='strategy_exit.exit_decisions'::regclass
    ) THEN
        ALTER TABLE strategy_exit.exit_decisions
            ADD CONSTRAINT exit_decisions_repeat_policy_check
            CHECK (
                repeat_policy IS NULL
                OR repeat_policy IN ('ONCE_PER_POSITION','EACH_MATCH')
            ) NOT VALID;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS strategy_exit.shadow_evaluations (
    evaluation_id text PRIMARY KEY,
    observation_id text NOT NULL,
    strategy_position_id text NOT NULL,
    exit_plan_fingerprint text NOT NULL,
    status text NOT NULL,
    reason text NOT NULL,
    matched_rule_ids jsonb NOT NULL,
    exit_decision_id text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (status IN (
        'NO_EXECUTABLE_EXIT_RULES','NO_MATCH','DECISION_CREATED','BLOCKED'
    )),
    CHECK (jsonb_typeof(matched_rule_ids)='array'),
    CHECK (
        (status='DECISION_CREATED' AND exit_decision_id IS NOT NULL)
        OR
        (status<>'DECISION_CREATED' AND exit_decision_id IS NULL)
    ),
    FOREIGN KEY (
        observation_id,strategy_position_id,exit_plan_fingerprint
    ) REFERENCES strategy_exit.exit_observations(
        observation_id,strategy_position_id,exit_plan_fingerprint
    ),
    FOREIGN KEY (exit_decision_id)
        REFERENCES strategy_exit.exit_decisions(exit_decision_id)
);

CREATE TABLE IF NOT EXISTS strategy_exit.execution_materialization_blocks (
    block_id text PRIMARY KEY,
    exit_decision_id text NOT NULL UNIQUE
        REFERENCES strategy_exit.exit_decisions(exit_decision_id),
    strategy_position_id text NOT NULL
        REFERENCES runtime.position_ownership(position_id),
    reason text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (jsonb_typeof(payload)='object')
);

CREATE TABLE IF NOT EXISTS strategy_exit.execution_requests (
    execution_request_id text PRIMARY KEY,
    exit_decision_id text NOT NULL,
    strategy_position_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    exit_plan_fingerprint text NOT NULL,
    account_ref text NOT NULL,
    exchange_position_key text NOT NULL,
    position_idx integer NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    action_kind text NOT NULL,
    requested_mutation jsonb NOT NULL,
    requested_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (position_idx >= 0),
    CHECK (direction IN ('LONG','SHORT')),
    CHECK (action_kind IN (
        'SET_STOP','SET_TP','SET_PROTECTION','SET_TRAILING','REDUCE','CLOSE'
    )),
    CHECK (jsonb_typeof(requested_mutation)='object'),
    UNIQUE (execution_request_id,exit_decision_id,strategy_position_id),
    FOREIGN KEY (
        exit_decision_id,strategy_position_id,strategy_id,strategy_version,
        strategy_config_fingerprint,exit_plan_fingerprint
    ) REFERENCES strategy_exit.exit_decisions(
        exit_decision_id,strategy_position_id,strategy_id,strategy_version,
        strategy_config_fingerprint,exit_plan_fingerprint
    )
);

ALTER TABLE strategy_exit.execution_requests
    ADD COLUMN IF NOT EXISTS expires_at timestamptz;

CREATE TABLE IF NOT EXISTS strategy_exit.execution_dispatches (
    dispatch_id text PRIMARY KEY,
    execution_request_id text NOT NULL UNIQUE
        REFERENCES strategy_exit.execution_requests(execution_request_id),
    command_id text UNIQUE,
    state text NOT NULL,
    reason text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (state IN ('DISPATCHED','BLOCKED')),
    CHECK (jsonb_typeof(payload)='object'),
    CHECK (
        (state='DISPATCHED' AND command_id IS NOT NULL)
        OR
        (state='BLOCKED' AND command_id IS NULL)
    )
);

DROP TRIGGER IF EXISTS exit_observations_immutable ON strategy_exit.exit_observations;
CREATE TRIGGER exit_observations_immutable
BEFORE UPDATE OR DELETE ON strategy_exit.exit_observations
FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();

DROP TRIGGER IF EXISTS exit_shadow_evaluations_immutable ON strategy_exit.shadow_evaluations;
CREATE TRIGGER exit_shadow_evaluations_immutable
BEFORE UPDATE OR DELETE ON strategy_exit.shadow_evaluations
FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();

DROP TRIGGER IF EXISTS exit_decisions_immutable ON strategy_exit.exit_decisions;
CREATE TRIGGER exit_decisions_immutable
BEFORE UPDATE OR DELETE ON strategy_exit.exit_decisions
FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();

DROP TRIGGER IF EXISTS exit_execution_materialization_blocks_immutable
    ON strategy_exit.execution_materialization_blocks;
CREATE TRIGGER exit_execution_materialization_blocks_immutable
BEFORE UPDATE OR DELETE ON strategy_exit.execution_materialization_blocks
FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();

DROP TRIGGER IF EXISTS exit_execution_requests_immutable ON strategy_exit.execution_requests;
CREATE TRIGGER exit_execution_requests_immutable
BEFORE UPDATE OR DELETE ON strategy_exit.execution_requests
FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();

DROP TRIGGER IF EXISTS exit_execution_dispatches_immutable ON strategy_exit.execution_dispatches;
CREATE TRIGGER exit_execution_dispatches_immutable
BEFORE UPDATE OR DELETE ON strategy_exit.execution_dispatches
FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();

CREATE TABLE IF NOT EXISTS runtime.trade_lifecycle_events (
    lifecycle_event_id text PRIMARY KEY,
    event_type text NOT NULL,
    occurred_at timestamptz NOT NULL,
    strategy_id text,
    strategy_version text,
    strategy_config_fingerprint text,
    strategy_activation_id text,
    signal_id text,
    strategy_attempt_id text,
    entry_decision_id text,
    entry_execution_request_id text,
    strategy_position_id text REFERENCES runtime.position_ownership(position_id),
    exit_plan_fingerprint text,
    exit_decision_id text,
    exit_execution_request_id text,
    exact_ids jsonb NOT NULL,
    payload jsonb NOT NULL,
    provenance jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (event_type IN (
        'STRATEGY_ACTIVATED',
        'PLANS_MATERIALIZED',
        'PLANS_PUBLISHED',
        'ENTRY_PLAN_CONSUMED',
        'STRATEGY_SIGNAL_CREATED',
        'ENTRY_ATTEMPT_CREATED',
        'CAPITAL_RESERVED',
        'ENTRY_DECIDED',
        'ENTRY_REQUEST_CREATED',
        'ENTRY_REQUEST_DISPATCHED',
        'ENTRY_ORDER_ACKNOWLEDGED',
        'ENTRY_FILLED',
        'STRATEGY_POSITION_CREATED',
        'EXIT_PLAN_BOUND',
        'EXIT_POSITION_CLAIMED',
        'EXIT_DECISION_CREATED',
        'EXIT_REQUEST_CREATED',
        'EXIT_REQUEST_DISPATCHED',
        'EXIT_MUTATION_ACKNOWLEDGED',
        'EXIT_MUTATION_CONFIRMED',
        'POSITION_CLOSED',
        'ECONOMICS_FINALIZED',
        'LIFECYCLE_FAULT'
    )),
    CHECK (jsonb_typeof(exact_ids)='object'),
    CHECK (jsonb_typeof(payload)='object'),
    CHECK (jsonb_typeof(provenance)='object')
);

DROP TRIGGER IF EXISTS trade_lifecycle_events_immutable ON runtime.trade_lifecycle_events;
CREATE TRIGGER trade_lifecycle_events_immutable
BEFORE UPDATE OR DELETE ON runtime.trade_lifecycle_events
FOR EACH ROW EXECUTE FUNCTION runtime.reject_immutable_change();

CREATE TABLE IF NOT EXISTS runtime.lifecycle_faults (
    fault_id text PRIMARY KEY,
    fault_code text NOT NULL,
    severity text NOT NULL,
    state text NOT NULL,
    strategy_position_id text REFERENCES runtime.position_ownership(position_id),
    detected_at timestamptz NOT NULL,
    resolved_at timestamptz,
    exact_ids jsonb NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (fault_code IN (
        'PLAN_PAIR_INCOMPLETE',
        'ENTRY_PLAN_NOT_CONSUMED',
        'ENTRY_REQUEST_NOT_DISPATCHED',
        'ENTRY_EXECUTION_AMBIGUOUS',
        'POSITION_LINEAGE_INCOMPLETE',
        'POSITION_WITHOUT_EXIT_PLAN',
        'POSITION_WITHOUT_EXIT_OWNER',
        'EXIT_REQUEST_NOT_DISPATCHED',
        'EXIT_EXECUTION_AMBIGUOUS',
        'EXCHANGE_POSITION_OWNERSHIP_CONFLICT',
        'EXCHANGE_STATE_DIVERGED',
        'CAPITAL_RESERVATION_STUCK'
    )),
    CHECK (severity IN ('WARNING','ERROR','CRITICAL')),
    CHECK (state IN ('OPEN','RESOLVED')),
    CHECK (
        (state='OPEN' AND resolved_at IS NULL)
        OR
        (state='RESOLVED' AND resolved_at IS NOT NULL)
    ),
    CHECK (jsonb_typeof(exact_ids)='object'),
    CHECK (jsonb_typeof(payload)='object')
);

CREATE OR REPLACE FUNCTION runtime.guard_lifecycle_fault_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(
        OLD.fault_id,OLD.fault_code,OLD.severity,OLD.strategy_position_id,
        OLD.detected_at,OLD.exact_ids,OLD.created_at
    ) IS DISTINCT FROM ROW(
        NEW.fault_id,NEW.fault_code,NEW.severity,NEW.strategy_position_id,
        NEW.detected_at,NEW.exact_ids,NEW.created_at
    ) THEN
        RAISE EXCEPTION 'lifecycle fault identity is immutable';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS lifecycle_faults_guard_update ON runtime.lifecycle_faults;
CREATE TRIGGER lifecycle_faults_guard_update
BEFORE UPDATE ON runtime.lifecycle_faults
FOR EACH ROW EXECUTE FUNCTION runtime.guard_lifecycle_fault_update();

CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS analytics.counterfactual_candidates (
    counterfactual_id text PRIMARY KEY,
    strategy_activation_id text NOT NULL
        REFERENCES strategy_entry.strategy_activations(activation_id),
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    exit_plan_fingerprint text NOT NULL,
    signal_id text NOT NULL,
    strategy_attempt_id text NOT NULL,
    entry_decision_id text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    decided_at timestamptz NOT NULL,
    captured_at timestamptz NOT NULL,
    decision_code text NOT NULL,
    requested_amount numeric NOT NULL,
    amount_currency text,
    capacity_snapshot_id text,
    reported_available_amount numeric,
    decision_reason text NOT NULL,
    evidence jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (decision_code='INSUFFICIENT_AVAILABLE_FUNDS'),
    CHECK (direction IN ('LONG','SHORT')),
    CHECK (requested_amount > 0),
    CHECK (reported_available_amount IS NULL OR reported_available_amount >= 0),
    CHECK (jsonb_typeof(evidence)='object'),
    FOREIGN KEY (
        entry_decision_id,strategy_attempt_id,signal_id
    ) REFERENCES strategy_entry.entry_decisions(
        entry_decision_id,strategy_attempt_id,signal_id
    ),
    FOREIGN KEY (
        entry_plan_fingerprint,strategy_id,strategy_version,
        strategy_config_fingerprint
    ) REFERENCES strategy_entry.entry_plans(
        entry_plan_fingerprint,strategy_id,strategy_version,
        strategy_config_fingerprint
    ),
    FOREIGN KEY (
        exit_plan_fingerprint,strategy_id,strategy_version,
        strategy_config_fingerprint
    ) REFERENCES strategy_entry.exit_plans(
        exit_plan_fingerprint,strategy_id,strategy_version,
        strategy_config_fingerprint
    )
);

DROP TRIGGER IF EXISTS counterfactual_candidates_immutable
    ON analytics.counterfactual_candidates;
CREATE TRIGGER counterfactual_candidates_immutable
BEFORE UPDATE OR DELETE ON analytics.counterfactual_candidates
FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();

CREATE TABLE IF NOT EXISTS analytics.counterfactual_outcomes (
    outcome_id text PRIMARY KEY,
    counterfactual_id text NOT NULL
        REFERENCES analytics.counterfactual_candidates(counterfactual_id),
    status text NOT NULL,
    evaluated_at timestamptz NOT NULL,
    opened_at timestamptz,
    entry_price numeric,
    closed_at timestamptz,
    exit_price numeric,
    exit_reason text,
    gross_pnl numeric,
    fees numeric,
    funding numeric,
    slippage numeric,
    net_pnl_after_fees numeric,
    economics_status text NOT NULL,
    evidence jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (status IN ('NO_ENTRY','OPEN_AT_CUTOFF','CLOSED')),
    CHECK (economics_status IN (
        'NO_ENTRY','OPEN_UNREALIZED','PARTIAL_NO_FUNDING','COMPLETE'
    )),
    CHECK (entry_price IS NULL OR entry_price > 0),
    CHECK (exit_price IS NULL OR exit_price > 0),
    CHECK (jsonb_typeof(evidence)='object'),
    CHECK (
        (status='NO_ENTRY'
            AND opened_at IS NULL AND entry_price IS NULL
            AND closed_at IS NULL AND exit_price IS NULL)
        OR
        (status='OPEN_AT_CUTOFF'
            AND opened_at IS NOT NULL AND entry_price IS NOT NULL
            AND closed_at IS NULL AND exit_price IS NULL)
        OR
        (status='CLOSED'
            AND opened_at IS NOT NULL AND entry_price IS NOT NULL
            AND closed_at IS NOT NULL AND exit_price IS NOT NULL)
    ),
    UNIQUE(counterfactual_id,evaluated_at,status)
);

DROP TRIGGER IF EXISTS counterfactual_outcomes_immutable
    ON analytics.counterfactual_outcomes;
CREATE TRIGGER counterfactual_outcomes_immutable
BEFORE UPDATE OR DELETE ON analytics.counterfactual_outcomes
FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();

REVOKE ALL ON analytics.counterfactual_candidates,
    analytics.counterfactual_outcomes FROM PUBLIC;
GRANT SELECT,INSERT ON analytics.counterfactual_candidates,
    analytics.counterfactual_outcomes TO cripta;
REVOKE UPDATE,DELETE ON analytics.counterfactual_candidates,
    analytics.counterfactual_outcomes FROM cripta;

REVOKE ALL ON runtime.plan_consumptions,runtime.position_exit_claims,
    runtime.capital_reservations,runtime.trade_lifecycle_events,
    runtime.lifecycle_faults FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON runtime.plan_consumptions TO cripta;
GRANT SELECT,INSERT,UPDATE ON runtime.position_exit_claims TO cripta;
GRANT SELECT,INSERT,UPDATE ON runtime.capital_reservations TO cripta;
GRANT SELECT,INSERT ON runtime.trade_lifecycle_events TO cripta;
GRANT SELECT,INSERT,UPDATE ON runtime.lifecycle_faults TO cripta;

REVOKE ALL ON strategy_exit.exit_observations,
    strategy_exit.shadow_evaluations,strategy_exit.exit_decisions,
    strategy_exit.execution_materialization_blocks,
    strategy_exit.execution_requests,strategy_exit.execution_dispatches FROM PUBLIC;
GRANT SELECT,INSERT ON strategy_exit.exit_observations,
    strategy_exit.shadow_evaluations,strategy_exit.exit_decisions,
    strategy_exit.execution_materialization_blocks,
    strategy_exit.execution_requests,strategy_exit.execution_dispatches TO cripta;

REVOKE UPDATE,DELETE ON strategy_exit.exit_observations,
    strategy_exit.shadow_evaluations,strategy_exit.exit_decisions,
    strategy_exit.execution_materialization_blocks,
    strategy_exit.execution_requests,strategy_exit.execution_dispatches FROM cripta;
REVOKE DELETE ON runtime.plan_consumptions,runtime.position_exit_claims,
    runtime.capital_reservations,runtime.trade_lifecycle_events,
    runtime.lifecycle_faults FROM cripta;

COMMIT;
