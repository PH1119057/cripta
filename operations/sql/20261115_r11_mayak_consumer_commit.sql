-- R11 #470 SOURCE-ONLY: one bounded authoritative PG MAYAK consumer commit.
-- This migration is inert source code until an owner-controlled D/R phase.
-- It stores only the latest restart unit, never raw/history retention.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

CREATE SCHEMA IF NOT EXISTS mayak_v2;
CREATE TABLE IF NOT EXISTS mayak_v2.r11_consumer_commit (
    consumer_id text PRIMARY KEY CHECK (
        consumer_id='R11_PG_ORIGINAL_MAYAK_V1'
    ),
    generation bigint NOT NULL CHECK(generation > 0),
    market_source_id text NOT NULL
        REFERENCES identity.market_sources(market_source_id),
    source_session_id text NOT NULL
        REFERENCES analytics.native_public_trade_source_sessions(source_session_id),
    producer_release_commit text NOT NULL CHECK(
        producer_release_commit ~ '^[0-9a-f]{40}$'
    ),
    publication_id bigint NOT NULL
        REFERENCES analytics.market_publications(publication_id),
    publication_as_of timestamptz NOT NULL,
    config_fingerprint text NOT NULL CHECK(
        config_fingerprint ~ '^[0-9a-f]{64}$'
    ),
    state_checkpoint bytea NOT NULL CHECK(
        octet_length(state_checkpoint) BETWEEN 100 AND 25165924
    ),
    state_sha256 text NOT NULL CHECK(state_sha256 ~ '^[0-9a-f]{64}$'),
    snapshot jsonb NOT NULL CHECK(
        jsonb_typeof(snapshot)='object'
        AND pg_column_size(snapshot) <= 16777216
    ),
    snapshot_sha256 text NOT NULL CHECK(snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    committed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

REVOKE ALL ON mayak_v2.r11_consumer_commit FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON mayak_v2.r11_consumer_commit TO cripta;
REVOKE DELETE,TRUNCATE ON mayak_v2.r11_consumer_commit FROM cripta;

DO $guard$
BEGIN
  IF NOT has_table_privilege(
      'cripta','mayak_v2.r11_consumer_commit','SELECT'
  ) OR NOT has_table_privilege(
      'cripta','mayak_v2.r11_consumer_commit','INSERT'
  ) OR NOT has_table_privilege(
      'cripta','mayak_v2.r11_consumer_commit','UPDATE'
  ) OR has_table_privilege(
      'cripta','mayak_v2.r11_consumer_commit','DELETE'
  ) OR has_table_privilege(
      'cripta','mayak_v2.r11_consumer_commit','TRUNCATE'
  ) THEN RAISE EXCEPTION 'R11_MAYAK_CONSUMER_COMMIT_GRANTS_INVALID';
  END IF;
END
$guard$;
COMMIT;
