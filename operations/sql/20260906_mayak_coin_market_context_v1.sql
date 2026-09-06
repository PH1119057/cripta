BEGIN;

CREATE TABLE IF NOT EXISTS mayak_v2.coin_market_contexts (
    coin_context_id TEXT PRIMARY KEY,
    mayak_snapshot_id BIGINT NOT NULL REFERENCES mayak_v2.snapshots(id),
    observed_at TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    config_fingerprint TEXT NOT NULL,
    data_quality TEXT NOT NULL CHECK (
        data_quality IN ('HIGH','MEDIUM','LOW','INSUFFICIENT')
    ),
    payload JSONB NOT NULL,
    provenance JSONB NOT NULL,
    content_hash TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT coin_market_contexts_payload_object_check
        CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT coin_market_contexts_provenance_object_check
        CHECK (jsonb_typeof(provenance) = 'object'),
    CONSTRAINT coin_market_contexts_no_trade_command_check
        CHECK ((provenance->>'trading_command') = 'false')
);

CREATE INDEX IF NOT EXISTS mayak_v2_coin_market_context_symbol_at
    ON mayak_v2.coin_market_contexts(symbol, observed_at DESC);
CREATE INDEX IF NOT EXISTS mayak_v2_coin_market_context_snapshot
    ON mayak_v2.coin_market_contexts(mayak_snapshot_id, symbol);

DROP TRIGGER IF EXISTS coin_market_contexts_immutable ON mayak_v2.coin_market_contexts;
CREATE TRIGGER coin_market_contexts_immutable
BEFORE UPDATE OR DELETE ON mayak_v2.coin_market_contexts
FOR EACH ROW EXECUTE FUNCTION runtime.reject_immutable_change();

REVOKE ALL ON mayak_v2.coin_market_contexts FROM PUBLIC;
GRANT SELECT, INSERT ON mayak_v2.coin_market_contexts TO cripta;

COMMIT;
