-- Issue #470 R05: ONE compact shared market read-model, NOT a new raw feed.
-- No live consumer switches, account/Exchange changes, deletes or retention
-- execution. Each row pins durable original native + confirmed closed bar keys.
-- New producer code defaults OFF until exact source and schema gates PASS.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '20s';

CREATE TABLE IF NOT EXISTS analytics.market_publications (
    publication_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    market_source_id text NOT NULL
      REFERENCES identity.market_sources(market_source_id),
    source_session_id text NOT NULL
      REFERENCES analytics.native_public_trade_source_sessions(source_session_id),
    producer_release_commit text NOT NULL
      CHECK (producer_release_commit ~ '^[a-f0-9]{40}$'),
    as_of_second timestamptz NOT NULL,
    native_watermarks jsonb NOT NULL
      CHECK (jsonb_typeof(native_watermarks) = 'object'),
    closed_candle_watermarks jsonb NOT NULL
      CHECK (jsonb_typeof(closed_candle_watermarks) = 'object'),
    field_coverage jsonb NOT NULL
      CHECK (jsonb_typeof(field_coverage) = 'object'),
    publication_status text NOT NULL
      CHECK (publication_status IN ('WATERMARKS_ONLY')),
    published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (market_source_id,source_session_id,as_of_second),
    CHECK (date_trunc('second',as_of_second)=as_of_second)
);

CREATE INDEX IF NOT EXISTS market_publications_source_time_idx
ON analytics.market_publications(
    market_source_id,as_of_second DESC,publication_id DESC
);

REVOKE ALL ON analytics.market_publications FROM PUBLIC;
GRANT SELECT,INSERT ON analytics.market_publications TO cripta;
REVOKE UPDATE,DELETE,TRUNCATE ON analytics.market_publications FROM cripta;
GRANT USAGE,SELECT ON SEQUENCE analytics.market_publications_publication_id_seq
TO cripta;

DO $publication$
BEGIN
    IF NOT has_table_privilege(
        'cripta','analytics.market_publications','INSERT'
    ) OR NOT has_table_privilege(
        'cripta','analytics.market_publications','SELECT'
    ) OR has_table_privilege(
        'cripta','analytics.market_publications','UPDATE'
    ) OR has_table_privilege(
        'cripta','analytics.market_publications','DELETE'
    ) OR has_table_privilege(
        'cripta','analytics.market_publications','TRUNCATE'
    ) THEN
        RAISE EXCEPTION 'MARKET_PUBLICATION_PRIVILEGE_DRIFT';
    END IF;
END
$publication$;
COMMIT;
