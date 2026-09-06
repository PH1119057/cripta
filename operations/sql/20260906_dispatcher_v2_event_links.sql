BEGIN;

CREATE SCHEMA IF NOT EXISTS research_context;

CREATE OR REPLACE FUNCTION research_context.reject_dispatcher_v2_event_link_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'dispatcher_v2 event context links are append-only';
END;
$$;

CREATE TABLE IF NOT EXISTS research_context.dispatcher_v2_event_links (
    event_type TEXT NOT NULL,
    reference_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    signal_id TEXT,
    strategy_attempt_id TEXT,
    position_id TEXT,
    trade_id TEXT,
    symbol TEXT,
    side TEXT,
    global_context_id TEXT NOT NULL
        REFERENCES dispatcher_v2.global_market_contexts(global_context_id),
    global_observed_at TIMESTAMPTZ NOT NULL,
    global_age_seconds DOUBLE PRECISION NOT NULL CHECK (global_age_seconds >= 0),
    coin_context_id TEXT REFERENCES dispatcher_v2.coin_market_contexts(coin_context_id),
    coin_observed_at TIMESTAMPTZ,
    coin_age_seconds DOUBLE PRECISION CHECK (coin_age_seconds IS NULL OR coin_age_seconds >= 0),
    capacity_snapshot_id TEXT
        REFERENCES dispatcher_v2.trading_capacity_snapshots(capacity_snapshot_id),
    capacity_observed_at TIMESTAMPTZ,
    capacity_age_seconds DOUBLE PRECISION
        CHECK (capacity_age_seconds IS NULL OR capacity_age_seconds >= 0),
    observed_context_mode TEXT NOT NULL DEFAULT 'OBSERVED_CONTEXT'
        CHECK (observed_context_mode = 'OBSERVED_CONTEXT'),
    consumed_context_mode TEXT NOT NULL DEFAULT 'NOT_CONSUMED'
        CHECK (consumed_context_mode = 'NOT_CONSUMED'),
    link_quality TEXT NOT NULL CHECK (link_quality IN (
        'GLOBAL_COIN_CAPACITY_CAUSAL_PRIOR',
        'GLOBAL_COIN_CAUSAL_PRIOR_NO_CAPACITY',
        'GLOBAL_CAPACITY_CAUSAL_PRIOR_NO_COIN',
        'GLOBAL_ONLY_CAUSAL_PRIOR'
    )),
    event_payload JSONB NOT NULL CHECK (jsonb_typeof(event_payload) = 'object'),
    provenance JSONB NOT NULL CHECK (jsonb_typeof(provenance) = 'object'),
    linked_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    trading_effect TEXT NOT NULL DEFAULT 'NONE' CHECK (trading_effect = 'NONE'),
    PRIMARY KEY (event_type, reference_id),
    CHECK ((coin_context_id IS NULL) = (coin_observed_at IS NULL)),
    CHECK ((coin_context_id IS NULL) = (coin_age_seconds IS NULL)),
    CHECK ((capacity_snapshot_id IS NULL) = (capacity_observed_at IS NULL)),
    CHECK ((capacity_snapshot_id IS NULL) = (capacity_age_seconds IS NULL)),
    CHECK ((provenance ->> 'trading_command') = 'false')
);

CREATE INDEX IF NOT EXISTS dispatcher_v2_event_links_at
    ON research_context.dispatcher_v2_event_links(occurred_at DESC);
CREATE INDEX IF NOT EXISTS dispatcher_v2_event_links_signal
    ON research_context.dispatcher_v2_event_links(signal_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS dispatcher_v2_event_links_symbol
    ON research_context.dispatcher_v2_event_links(symbol, occurred_at DESC);
CREATE INDEX IF NOT EXISTS dispatcher_v2_event_links_global
    ON research_context.dispatcher_v2_event_links(global_context_id);

DROP TRIGGER IF EXISTS dispatcher_v2_event_links_immutable
    ON research_context.dispatcher_v2_event_links;
CREATE TRIGGER dispatcher_v2_event_links_immutable
BEFORE UPDATE OR DELETE ON research_context.dispatcher_v2_event_links
FOR EACH ROW EXECUTE FUNCTION research_context.reject_dispatcher_v2_event_link_mutation();

REVOKE ALL ON research_context.dispatcher_v2_event_links FROM PUBLIC;
GRANT USAGE ON SCHEMA research_context TO cripta;
GRANT SELECT, INSERT ON research_context.dispatcher_v2_event_links TO cripta;

COMMIT;
