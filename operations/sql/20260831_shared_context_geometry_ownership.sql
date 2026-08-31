BEGIN;

CREATE SCHEMA IF NOT EXISTS monitoring;
CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS mayak_v2.shared_market_contexts (
    market_context_id text PRIMARY KEY,
    mayak_snapshot_id bigint NOT NULL REFERENCES mayak_v2.snapshots(id),
    observed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    mayak_version text NOT NULL,
    schema_version text NOT NULL,
    config_fingerprint text NOT NULL,
    data_quality text NOT NULL,
    payload jsonb NOT NULL,
    provenance jsonb NOT NULL,
    content_hash text UNIQUE NOT NULL,
    CHECK (payload->>'market_context_id' = market_context_id),
    CHECK (payload->'provenance'->>'trading_command' = 'false')
);

CREATE TABLE IF NOT EXISTS monitoring.entry_geometry_handoffs (
    geometry_handoff_id text PRIMARY KEY,
    signal_id text UNIQUE NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    entry_fingerprint text NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL,
    signal_at timestamptz NOT NULL,
    geometry_observed_at timestamptz NOT NULL,
    geometry_version text NOT NULL,
    config_fingerprint text NOT NULL,
    geometry_hash text UNIQUE NOT NULL,
    payload jsonb NOT NULL,
    provenance jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (geometry_observed_at <= signal_at)
);

ALTER TABLE strategy_dispatcher.runs
    ADD COLUMN IF NOT EXISTS market_context_id text;
ALTER TABLE strategy_dispatcher.assessments
    ADD COLUMN IF NOT EXISTS market_context_id text;
ALTER TABLE runtime.m3_consumed_context
    ADD COLUMN IF NOT EXISTS market_context_id text;

CREATE TABLE IF NOT EXISTS runtime.entry_geometry_bindings (
    entry_command_id text PRIMARY KEY,
    geometry_handoff_id text UNIQUE NOT NULL
        REFERENCES monitoring.entry_geometry_handoffs(geometry_handoff_id),
    signal_id text UNIQUE NOT NULL,
    bot_instance_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    bound_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    payload jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime.position_ownership (
    position_id text PRIMARY KEY,
    trade_id text UNIQUE NOT NULL,
    bot_instance_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    signal_id text NOT NULL,
    entry_command_id text UNIQUE NOT NULL,
    geometry_handoff_id text REFERENCES monitoring.entry_geometry_handoffs(geometry_handoff_id),
    symbol text NOT NULL,
    side text NOT NULL,
    actual_avg_fill numeric NOT NULL,
    actual_qty numeric NOT NULL,
    fill_at timestamptz NOT NULL,
    exchange_order_ids jsonb NOT NULL,
    client_order_ids jsonb NOT NULL,
    execution_ids jsonb NOT NULL,
    state text NOT NULL DEFAULT 'OPEN',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (state IN ('OPEN', 'CLOSED', 'RECONCILIATION_REQUIRED'))
);

CREATE OR REPLACE FUNCTION runtime.reject_immutable_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'immutable audit record % cannot be changed', TG_TABLE_NAME;
END;
$$;

DROP TRIGGER IF EXISTS shared_market_contexts_immutable ON mayak_v2.shared_market_contexts;
CREATE TRIGGER shared_market_contexts_immutable
BEFORE UPDATE OR DELETE ON mayak_v2.shared_market_contexts
FOR EACH ROW EXECUTE FUNCTION runtime.reject_immutable_change();

DROP TRIGGER IF EXISTS entry_geometry_handoffs_immutable ON monitoring.entry_geometry_handoffs;
CREATE TRIGGER entry_geometry_handoffs_immutable
BEFORE UPDATE OR DELETE ON monitoring.entry_geometry_handoffs
FOR EACH ROW EXECUTE FUNCTION runtime.reject_immutable_change();

CREATE OR REPLACE VIEW analytics.shared_market_context_consumption AS
SELECT a.assessment_id,
       a.profile_id,
       a.profile_version,
       a.market_context_id,
       c.mayak_snapshot_id,
       c.observed_at AS market_observed_at,
       a.observed_at AS assessment_observed_at,
       a.status,
       a.suitability,
       a.confidence,
       a.data_quality
FROM strategy_dispatcher.assessments a
LEFT JOIN mayak_v2.shared_market_contexts c
  ON c.market_context_id = a.market_context_id;

CREATE OR REPLACE VIEW analytics.position_lifecycle_identity AS
SELECT o.position_id,
       o.trade_id,
       o.bot_instance_id,
       o.strategy_id,
       o.strategy_version,
       o.signal_id,
       o.entry_command_id,
       o.geometry_handoff_id,
       o.symbol,
       o.side,
       o.actual_avg_fill,
       o.actual_qty,
       o.fill_at,
       o.state,
       g.signal_at,
       g.geometry_observed_at,
       g.geometry_version,
       g.geometry_hash
FROM runtime.position_ownership o
LEFT JOIN monitoring.entry_geometry_handoffs g
  ON g.geometry_handoff_id = o.geometry_handoff_id;

COMMIT;
