BEGIN;

CREATE SCHEMA IF NOT EXISTS strategy_entry AUTHORIZATION postgres;
REVOKE ALL ON SCHEMA strategy_entry FROM PUBLIC;
GRANT USAGE ON SCHEMA strategy_entry TO cripta;

CREATE OR REPLACE FUNCTION strategy_entry.reject_immutable_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'immutable strategy_entry record % cannot be changed', TG_TABLE_NAME;
END;
$$;

CREATE TABLE IF NOT EXISTS strategy_entry.strategy_cards (
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    name text NOT NULL,
    description text NOT NULL,
    card_json jsonb NOT NULL,
    approved_at timestamptz NOT NULL,
    approved_source text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (strategy_id, strategy_version, strategy_config_fingerprint),
    UNIQUE (strategy_id, strategy_version),
    CHECK (jsonb_typeof(card_json) = 'object'),
    CHECK (card_json->>'strategy_id' = strategy_id),
    CHECK (card_json->>'strategy_version' = strategy_version),
    CHECK (card_json->>'strategy_config_fingerprint' = strategy_config_fingerprint)
);

CREATE TABLE IF NOT EXISTS strategy_entry.strategy_activations (
    activation_id text PRIMARY KEY,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    enabled boolean NOT NULL,
    enabled_at timestamptz NOT NULL,
    disabled_at timestamptz,
    scope jsonb NOT NULL DEFAULT '{}'::jsonb,
    operator text NOT NULL,
    source text NOT NULL,
    change_reason text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (activation_id, strategy_id, strategy_version, strategy_config_fingerprint),
    FOREIGN KEY (strategy_id, strategy_version, strategy_config_fingerprint)
        REFERENCES strategy_entry.strategy_cards(
            strategy_id, strategy_version, strategy_config_fingerprint
        ),
    CHECK (jsonb_typeof(scope) = 'object'),
    CHECK (
        (enabled AND disabled_at IS NULL)
        OR
        ((NOT enabled) AND disabled_at IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS strategy_entry.strategy_activation_events (
    activation_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    activation_id text NOT NULL REFERENCES strategy_entry.strategy_activations(activation_id),
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    enabled boolean NOT NULL,
    enabled_at timestamptz NOT NULL,
    disabled_at timestamptz,
    operator text NOT NULL,
    source text NOT NULL,
    reason text NOT NULL,
    scope jsonb NOT NULL,
    CHECK (jsonb_typeof(scope) = 'object')
);

CREATE OR REPLACE FUNCTION strategy_entry.guard_activation_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF ROW(
        OLD.activation_id,
        OLD.strategy_id,
        OLD.strategy_version,
        OLD.strategy_config_fingerprint,
        OLD.scope,
        OLD.created_at
    ) IS DISTINCT FROM ROW(
        NEW.activation_id,
        NEW.strategy_id,
        NEW.strategy_version,
        NEW.strategy_config_fingerprint,
        NEW.scope,
        NEW.created_at
    ) THEN
        RAISE EXCEPTION 'StrategyActivation identity/scope is immutable; create a new activation';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION strategy_entry.log_activation_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = strategy_entry, pg_catalog
AS $$
BEGIN
    INSERT INTO strategy_activation_events(
        activation_id, enabled, enabled_at, disabled_at,
        operator, source, reason, scope
    ) VALUES (
        NEW.activation_id, NEW.enabled, NEW.enabled_at, NEW.disabled_at,
        NEW.operator, NEW.source, NEW.change_reason, NEW.scope
    );
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS strategy_activations_guard_update
    ON strategy_entry.strategy_activations;
CREATE TRIGGER strategy_activations_guard_update
BEFORE UPDATE ON strategy_entry.strategy_activations
FOR EACH ROW EXECUTE FUNCTION strategy_entry.guard_activation_update();

DROP TRIGGER IF EXISTS strategy_activations_journal
    ON strategy_entry.strategy_activations;
CREATE TRIGGER strategy_activations_journal
AFTER INSERT OR UPDATE ON strategy_entry.strategy_activations
FOR EACH ROW EXECUTE FUNCTION strategy_entry.log_activation_event();

CREATE TABLE IF NOT EXISTS strategy_entry.entry_plans (
    entry_plan_fingerprint text PRIMARY KEY,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_version text NOT NULL,
    plan_json jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (
        entry_plan_fingerprint, strategy_id, strategy_version,
        strategy_config_fingerprint
    ),
    FOREIGN KEY (strategy_id, strategy_version, strategy_config_fingerprint)
        REFERENCES strategy_entry.strategy_cards(
            strategy_id, strategy_version, strategy_config_fingerprint
        ),
    CHECK (jsonb_typeof(plan_json) = 'object'),
    CHECK (plan_json->>'entry_plan_fingerprint' = entry_plan_fingerprint)
);

CREATE TABLE IF NOT EXISTS strategy_entry.exit_plans (
    exit_plan_fingerprint text PRIMARY KEY,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    exit_plan_version text NOT NULL,
    plan_json jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (
        exit_plan_fingerprint, strategy_id, strategy_version,
        strategy_config_fingerprint
    ),
    FOREIGN KEY (strategy_id, strategy_version, strategy_config_fingerprint)
        REFERENCES strategy_entry.strategy_cards(
            strategy_id, strategy_version, strategy_config_fingerprint
        ),
    CHECK (jsonb_typeof(plan_json) = 'object'),
    CHECK (plan_json->>'exit_plan_fingerprint' = exit_plan_fingerprint)
);

CREATE TABLE IF NOT EXISTS strategy_entry.strategy_signals (
    signal_id text PRIMARY KEY,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL REFERENCES strategy_entry.entry_plans(entry_plan_fingerprint),
    strategy_activation_id text NOT NULL REFERENCES strategy_entry.strategy_activations(activation_id),
    symbol text NOT NULL,
    direction text NOT NULL,
    detected_at timestamptz NOT NULL,
    fact_id text NOT NULL,
    source_refs jsonb NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (strategy_id, strategy_version, strategy_config_fingerprint)
        REFERENCES strategy_entry.strategy_cards(
            strategy_id, strategy_version, strategy_config_fingerprint
        ),
    FOREIGN KEY (
        entry_plan_fingerprint, strategy_id, strategy_version,
        strategy_config_fingerprint
    ) REFERENCES strategy_entry.entry_plans(
        entry_plan_fingerprint, strategy_id, strategy_version,
        strategy_config_fingerprint
    ),
    FOREIGN KEY (
        strategy_activation_id, strategy_id, strategy_version,
        strategy_config_fingerprint
    ) REFERENCES strategy_entry.strategy_activations(
        activation_id, strategy_id, strategy_version,
        strategy_config_fingerprint
    ),
    UNIQUE (
        signal_id, strategy_id, strategy_version, strategy_config_fingerprint,
        entry_plan_fingerprint, strategy_activation_id
    ),
    UNIQUE (
        signal_id, strategy_id, strategy_version, strategy_config_fingerprint,
        entry_plan_fingerprint
    ),
    CHECK (direction IN ('LONG', 'SHORT')),
    CHECK (jsonb_typeof(source_refs) = 'array'),
    CHECK (jsonb_typeof(payload) = 'object')
);

CREATE TABLE IF NOT EXISTS strategy_entry.strategy_attempts (
    strategy_attempt_id text PRIMARY KEY,
    signal_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    strategy_activation_id text NOT NULL,
    created_at timestamptz NOT NULL,
    payload jsonb NOT NULL,
    UNIQUE (strategy_attempt_id, signal_id),
    UNIQUE (
        strategy_attempt_id, signal_id, strategy_id, strategy_version,
        strategy_config_fingerprint, entry_plan_fingerprint
    ),
    UNIQUE (strategy_attempt_id, signal_id, strategy_id, strategy_version),
    FOREIGN KEY (
        signal_id, strategy_id, strategy_version, strategy_config_fingerprint,
        entry_plan_fingerprint, strategy_activation_id
    ) REFERENCES strategy_entry.strategy_signals(
        signal_id, strategy_id, strategy_version, strategy_config_fingerprint,
        entry_plan_fingerprint, strategy_activation_id
    ),
    CHECK (jsonb_typeof(payload) = 'object')
);

CREATE TABLE IF NOT EXISTS strategy_entry.entry_decisions (
    entry_decision_id text PRIMARY KEY,
    strategy_attempt_id text NOT NULL,
    signal_id text NOT NULL,
    decision_code text NOT NULL,
    reason text NOT NULL,
    decided_at timestamptz NOT NULL,
    capacity_snapshot_id text,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (entry_decision_id, strategy_attempt_id, signal_id),
    FOREIGN KEY (strategy_attempt_id, signal_id)
        REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id, signal_id),
    CHECK (decision_code IN (
        'ACCEPTED',
        'STRATEGY_CONDITION_REJECTED',
        'INSUFFICIENT_AVAILABLE_FUNDS',
        'OPERATIONAL_SAFETY_BLOCKED',
        'STALE_OR_UNKNOWN_REQUIRED_STATE',
        'EXPIRED',
        'CANCELLED'
    )),
    CHECK (jsonb_typeof(payload) = 'object')
);

CREATE TABLE IF NOT EXISTS strategy_entry.context_links (
    context_link_id text PRIMARY KEY,
    signal_id text NOT NULL REFERENCES strategy_entry.strategy_signals(signal_id),
    strategy_attempt_id text,
    link_type text NOT NULL,
    requirement_id text NOT NULL,
    context_type text,
    context_id text,
    plan_mode text,
    context_observed_at timestamptz,
    linked_at timestamptz NOT NULL,
    age_seconds double precision,
    quality text,
    status text NOT NULL,
    source_refs jsonb NOT NULL,
    provenance jsonb NOT NULL,
    FOREIGN KEY (strategy_attempt_id, signal_id)
        REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id, signal_id),
    UNIQUE NULLS NOT DISTINCT (
        signal_id, strategy_attempt_id, link_type, requirement_id, context_id
    ),
    CHECK (link_type IN ('OBSERVED_CONTEXT', 'CONSUMED_CONTEXT')),
    CHECK (
        (link_type = 'OBSERVED_CONTEXT')
        OR
        (
            link_type = 'CONSUMED_CONTEXT'
            AND strategy_attempt_id IS NOT NULL
            AND context_id IS NOT NULL
            AND context_observed_at IS NOT NULL
        )
    ),
    CHECK (plan_mode IS NULL OR plan_mode IN ('OBSERVE', 'CONDITION', 'RANKING')),
    CHECK (jsonb_typeof(source_refs) = 'array'),
    CHECK (jsonb_typeof(provenance) = 'object')
);

CREATE TABLE IF NOT EXISTS strategy_entry.sensor_links (
    sensor_link_id text PRIMARY KEY,
    signal_id text NOT NULL REFERENCES strategy_entry.strategy_signals(signal_id),
    strategy_attempt_id text,
    link_type text NOT NULL,
    sensor_id text NOT NULL,
    sensor_observed_at timestamptz,
    linked_at timestamptz NOT NULL,
    age_seconds double precision,
    quality text,
    status text NOT NULL,
    source_refs jsonb NOT NULL,
    provenance jsonb NOT NULL,
    FOREIGN KEY (strategy_attempt_id, signal_id)
        REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id, signal_id),
    UNIQUE NULLS NOT DISTINCT (signal_id, strategy_attempt_id, link_type, sensor_id),
    CHECK (link_type IN ('OBSERVED_SENSOR', 'CONSUMED_SENSOR')),
    CHECK (
        (link_type = 'OBSERVED_SENSOR')
        OR
        (
            link_type = 'CONSUMED_SENSOR'
            AND strategy_attempt_id IS NOT NULL
            AND sensor_observed_at IS NOT NULL
        )
    ),
    CHECK (jsonb_typeof(source_refs) = 'array'),
    CHECK (jsonb_typeof(provenance) = 'object')
);

CREATE TABLE IF NOT EXISTS strategy_entry.execution_requests (
    execution_request_id text PRIMARY KEY,
    strategy_attempt_id text NOT NULL,
    entry_decision_id text NOT NULL,
    signal_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    requested_at timestamptz NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (entry_decision_id, strategy_attempt_id, signal_id)
        REFERENCES strategy_entry.entry_decisions(
            entry_decision_id, strategy_attempt_id, signal_id
        ),
    FOREIGN KEY (
        strategy_attempt_id, signal_id, strategy_id, strategy_version,
        strategy_config_fingerprint, entry_plan_fingerprint
    ) REFERENCES strategy_entry.strategy_attempts(
        strategy_attempt_id, signal_id, strategy_id, strategy_version,
        strategy_config_fingerprint, entry_plan_fingerprint
    ),
    CHECK (direction IN ('LONG', 'SHORT')),
    CHECK (jsonb_typeof(payload) = 'object')
);

CREATE TABLE IF NOT EXISTS strategy_entry.notifications (
    notification_id text PRIMARY KEY,
    strategy_attempt_id text NOT NULL,
    signal_id text NOT NULL,
    kind text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    occurred_at timestamptz NOT NULL,
    reason text NOT NULL,
    requested_amount numeric,
    available_amount numeric,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (strategy_attempt_id, signal_id)
        REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id, signal_id),
    FOREIGN KEY (strategy_attempt_id, signal_id, strategy_id, strategy_version)
        REFERENCES strategy_entry.strategy_attempts(
            strategy_attempt_id, signal_id, strategy_id, strategy_version
        ),
    CHECK (kind IN (
        'INSUFFICIENT_AVAILABLE_FUNDS',
        'OPERATIONAL_SAFETY_BLOCKED',
        'STALE_OR_UNKNOWN_REQUIRED_STATE',
        'EXCHANGE_REJECTED',
        'EXECUTION_FAILURE'
    )),
    CHECK (direction IN ('LONG', 'SHORT')),
    CHECK (jsonb_typeof(payload) = 'object')
);

CREATE TABLE IF NOT EXISTS strategy_entry.shadow_parity_runs (
    parity_run_id text PRIMARY KEY,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    baseline_source_commit text NOT NULL,
    universal_source_commit text NOT NULL,
    status text NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    summary jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (strategy_id, strategy_version, strategy_config_fingerprint)
        REFERENCES strategy_entry.strategy_cards(
            strategy_id, strategy_version, strategy_config_fingerprint
        ),
    CHECK (status IN ('PASS', 'FAIL')),
    CHECK (finished_at >= started_at),
    CHECK (jsonb_typeof(summary) = 'object')
);

CREATE TABLE IF NOT EXISTS strategy_entry.shadow_parity_events (
    parity_event_id text PRIMARY KEY,
    parity_run_id text NOT NULL REFERENCES strategy_entry.shadow_parity_runs(parity_run_id),
    causal_key text NOT NULL,
    event_at timestamptz NOT NULL,
    legacy_payload jsonb NOT NULL,
    universal_payload jsonb NOT NULL,
    equivalent boolean NOT NULL,
    difference jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (parity_run_id, causal_key),
    CHECK (jsonb_typeof(legacy_payload) = 'object'),
    CHECK (jsonb_typeof(universal_payload) = 'object'),
    CHECK (jsonb_typeof(difference) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_strategy_entry_activations_enabled
    ON strategy_entry.strategy_activations(enabled, strategy_id, strategy_version);
CREATE INDEX IF NOT EXISTS ix_strategy_entry_signals_symbol_time
    ON strategy_entry.strategy_signals(symbol, detected_at, signal_id);
CREATE INDEX IF NOT EXISTS ix_strategy_entry_attempts_signal
    ON strategy_entry.strategy_attempts(signal_id, strategy_attempt_id);
CREATE INDEX IF NOT EXISTS ix_strategy_entry_decisions_attempt
    ON strategy_entry.entry_decisions(strategy_attempt_id, decided_at);
CREATE INDEX IF NOT EXISTS ix_strategy_entry_context_signal
    ON strategy_entry.context_links(signal_id, link_type, requirement_id);
CREATE INDEX IF NOT EXISTS ix_strategy_entry_sensor_signal
    ON strategy_entry.sensor_links(signal_id, link_type, sensor_id);

DO $$
DECLARE
    target_table text;
BEGIN
    FOREACH target_table IN ARRAY ARRAY[
        'strategy_cards',
        'strategy_activation_events',
        'entry_plans',
        'exit_plans',
        'strategy_signals',
        'strategy_attempts',
        'entry_decisions',
        'context_links',
        'sensor_links',
        'execution_requests',
        'notifications',
        'shadow_parity_runs',
        'shadow_parity_events'
    ] LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS %I ON strategy_entry.%I',
            target_table || '_immutable',
            target_table
        );
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON strategy_entry.%I '
            'FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change()',
            target_table || '_immutable',
            target_table
        );
    END LOOP;
END;
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA strategy_entry FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA strategy_entry FROM PUBLIC;

GRANT SELECT, INSERT ON
    strategy_entry.strategy_cards,
    strategy_entry.entry_plans,
    strategy_entry.exit_plans,
    strategy_entry.strategy_signals,
    strategy_entry.strategy_attempts,
    strategy_entry.entry_decisions,
    strategy_entry.context_links,
    strategy_entry.sensor_links,
    strategy_entry.execution_requests,
    strategy_entry.notifications,
    strategy_entry.shadow_parity_runs,
    strategy_entry.shadow_parity_events
TO cripta;

GRANT SELECT, INSERT, UPDATE ON strategy_entry.strategy_activations TO cripta;
GRANT SELECT ON strategy_entry.strategy_activation_events TO cripta;

REVOKE DELETE ON ALL TABLES IN SCHEMA strategy_entry FROM cripta;
REVOKE UPDATE ON
    strategy_entry.strategy_cards,
    strategy_entry.strategy_activation_events,
    strategy_entry.entry_plans,
    strategy_entry.exit_plans,
    strategy_entry.strategy_signals,
    strategy_entry.strategy_attempts,
    strategy_entry.entry_decisions,
    strategy_entry.context_links,
    strategy_entry.sensor_links,
    strategy_entry.execution_requests,
    strategy_entry.notifications,
    strategy_entry.shadow_parity_runs,
    strategy_entry.shadow_parity_events
FROM cripta;

COMMIT;
