BEGIN;

CREATE SCHEMA IF NOT EXISTS dispatcher_v2 AUTHORIZATION postgres;

CREATE OR REPLACE FUNCTION dispatcher_v2.reject_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'dispatcher_v2 contexts are append-only';
END;
$$;

CREATE TABLE IF NOT EXISTS dispatcher_v2.global_market_contexts (
    global_context_id text PRIMARY KEY,
    source_market_context_id text UNIQUE NOT NULL
        REFERENCES mayak_v2.shared_market_contexts(market_context_id),
    source_mayak_snapshot_id bigint NOT NULL REFERENCES mayak_v2.snapshots(id),
    observed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL,
    dispatcher_version text NOT NULL,
    schema_version text NOT NULL,
    config_fingerprint text NOT NULL,
    data_quality text NOT NULL
        CHECK (data_quality IN ('HIGH','MEDIUM','LOW','INSUFFICIENT')),
    freshness_status text NOT NULL CHECK (freshness_status IN ('FRESH','STALE','UNKNOWN')),
    freshness_age_seconds double precision,
    coverage jsonb NOT NULL CHECK (jsonb_typeof(coverage)='object'),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object'),
    provenance jsonb NOT NULL CHECK (jsonb_typeof(provenance)='object'),
    content_hash text UNIQUE NOT NULL,
    trading_effect text NOT NULL DEFAULT 'NONE' CHECK (trading_effect='NONE')
);

CREATE TABLE IF NOT EXISTS dispatcher_v2.coin_market_contexts (
    coin_context_id text PRIMARY KEY,
    global_context_id text NOT NULL
        REFERENCES dispatcher_v2.global_market_contexts(global_context_id),
    source_coin_context_id text UNIQUE NOT NULL
        REFERENCES mayak_v2.coin_market_contexts(coin_context_id),
    source_mayak_snapshot_id bigint NOT NULL REFERENCES mayak_v2.snapshots(id),
    symbol text NOT NULL,
    observed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL,
    dispatcher_version text NOT NULL,
    schema_version text NOT NULL,
    config_fingerprint text NOT NULL,
    data_quality text NOT NULL
        CHECK (data_quality IN ('HIGH','MEDIUM','LOW','INSUFFICIENT')),
    freshness_status text NOT NULL CHECK (freshness_status IN ('FRESH','STALE','UNKNOWN')),
    freshness_age_seconds double precision,
    coverage jsonb NOT NULL CHECK (jsonb_typeof(coverage)='object'),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object'),
    provenance jsonb NOT NULL CHECK (jsonb_typeof(provenance)='object'),
    content_hash text UNIQUE NOT NULL,
    trading_effect text NOT NULL DEFAULT 'NONE' CHECK (trading_effect='NONE'),
    UNIQUE(global_context_id,symbol)
);

CREATE TABLE IF NOT EXISTS dispatcher_v2.trading_capacity_snapshots (
    capacity_snapshot_id text PRIMARY KEY,
    observed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL,
    dispatcher_version text NOT NULL,
    schema_version text NOT NULL,
    config_fingerprint text NOT NULL,
    source_adapter text NOT NULL,
    source_account_ref text NOT NULL,
    account_type text,
    data_quality text NOT NULL
        CHECK (data_quality IN ('HIGH','MEDIUM','LOW','INSUFFICIENT')),
    freshness_status text NOT NULL CHECK (freshness_status IN ('FRESH','STALE','UNKNOWN')),
    freshness_age_seconds double precision,
    total_equity numeric,
    wallet_balance numeric,
    used_position_margin numeric,
    reserved_order_margin numeric,
    free_balance numeric,
    available_for_new_trading numeric,
    open_positions_count integer NOT NULL CHECK (open_positions_count >= 0),
    active_orders_count integer NOT NULL CHECK (active_orders_count >= 0),
    coverage jsonb NOT NULL CHECK (jsonb_typeof(coverage)='object'),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object'),
    provenance jsonb NOT NULL CHECK (jsonb_typeof(provenance)='object'),
    content_hash text UNIQUE NOT NULL,
    trading_effect text NOT NULL DEFAULT 'NONE' CHECK (trading_effect='NONE')
);

CREATE INDEX IF NOT EXISTS dispatcher_v2_global_at
    ON dispatcher_v2.global_market_contexts(observed_at DESC);
CREATE INDEX IF NOT EXISTS dispatcher_v2_coin_symbol_at
    ON dispatcher_v2.coin_market_contexts(symbol, observed_at DESC);
CREATE INDEX IF NOT EXISTS dispatcher_v2_coin_source_snapshot
    ON dispatcher_v2.coin_market_contexts(source_mayak_snapshot_id, symbol);
CREATE INDEX IF NOT EXISTS dispatcher_v2_capacity_at
    ON dispatcher_v2.trading_capacity_snapshots(observed_at DESC);

DROP TRIGGER IF EXISTS global_market_contexts_immutable
    ON dispatcher_v2.global_market_contexts;
CREATE TRIGGER global_market_contexts_immutable
BEFORE UPDATE OR DELETE ON dispatcher_v2.global_market_contexts
FOR EACH ROW EXECUTE FUNCTION dispatcher_v2.reject_mutation();

DROP TRIGGER IF EXISTS coin_market_contexts_immutable
    ON dispatcher_v2.coin_market_contexts;
CREATE TRIGGER coin_market_contexts_immutable
BEFORE UPDATE OR DELETE ON dispatcher_v2.coin_market_contexts
FOR EACH ROW EXECUTE FUNCTION dispatcher_v2.reject_mutation();

DROP TRIGGER IF EXISTS trading_capacity_snapshots_immutable
    ON dispatcher_v2.trading_capacity_snapshots;
CREATE TRIGGER trading_capacity_snapshots_immutable
BEFORE UPDATE OR DELETE ON dispatcher_v2.trading_capacity_snapshots
FOR EACH ROW EXECUTE FUNCTION dispatcher_v2.reject_mutation();

REVOKE ALL ON SCHEMA dispatcher_v2 FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA dispatcher_v2 FROM PUBLIC;
GRANT USAGE ON SCHEMA dispatcher_v2 TO cripta;
GRANT SELECT, INSERT ON dispatcher_v2.global_market_contexts TO cripta;
GRANT SELECT, INSERT ON dispatcher_v2.coin_market_contexts TO cripta;
GRANT SELECT, INSERT ON dispatcher_v2.trading_capacity_snapshots TO cripta;

COMMIT;
