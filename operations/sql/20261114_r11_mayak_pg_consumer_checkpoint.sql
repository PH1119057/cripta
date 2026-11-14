-- #470 R11 source-only: PG-atomic MAYAK+Dispatcher original replay checkpoint.
-- Requires existing analytics.market_publications and mayak_v2 snapshots.
-- Apply in one controlled no-start migration by authorized postgres role
-- ONLY after full R00-R14 code-first reviews. Never via runtime cripta DDL.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

CREATE TABLE IF NOT EXISTS mayak_v2.r11_market_publication_checkpoints (
    consumer_key text PRIMARY KEY CHECK (consumer_key = 'MAYAK_SEVEN_PG_V1'),
    publication_id bigint NOT NULL
       REFERENCES analytics.market_publications(publication_id),
    source_session_id text NOT NULL
       REFERENCES analytics.native_public_trade_source_sessions(source_session_id),
    producer_release_commit text NOT NULL
       CHECK (producer_release_commit ~ '^[a-f0-9]{40}$'),
    publication_as_of timestamptz NOT NULL,
    snapshot_id bigint NOT NULL REFERENCES mayak_v2.snapshots(id),
    config_fingerprint text NOT NULL CHECK (config_fingerprint ~ '^[a-f0-9]{64}$'),
    state_sha256 text NOT NULL CHECK (state_sha256 ~ '^[a-f0-9]{64}$'),
    replay_engine_state bytea NOT NULL
       CHECK (octet_length(replay_engine_state) BETWEEN 1 AND 25165824),
    consumer_quality text NOT NULL DEFAULT 'WARMUP'
       CHECK (consumer_quality IN ('WARMUP','DEGRADED')),
    stored_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
REVOKE ALL ON mayak_v2.r11_market_publication_checkpoints FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON mayak_v2.r11_market_publication_checkpoints TO cripta;
REVOKE DELETE,TRUNCATE ON mayak_v2.r11_market_publication_checkpoints FROM cripta;
DO $check$
BEGIN
 IF NOT has_table_privilege('cripta','mayak_v2.r11_market_publication_checkpoints','SELECT')
 OR NOT has_table_privilege('cripta','mayak_v2.r11_market_publication_checkpoints','INSERT')
 OR NOT has_table_privilege('cripta','mayak_v2.r11_market_publication_checkpoints','UPDATE')
 OR has_table_privilege('cripta','mayak_v2.r11_market_publication_checkpoints','DELETE')
 OR has_table_privilege('cripta','mayak_v2.r11_market_publication_checkpoints','TRUNCATE')
 THEN RAISE EXCEPTION 'R11_MAYAK_CHECKPOINT_EFFECTIVE_GRANTS_INVALID'; END IF;
END $check$;
COMMIT;
