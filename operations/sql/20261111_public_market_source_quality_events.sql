-- #470 R04: append-only per-product/topic transport quality evidence.
-- A quality event is NOT an Entry/Exit readiness grant. Reconnect does not
-- remove the old GAP or authorize a synthetic native continuity repair.
-- Schema migration is INERT until separately approved native R04 cutover.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

CREATE TABLE IF NOT EXISTS analytics.public_market_source_quality_events (
    quality_event_id text PRIMARY KEY CHECK (
        quality_event_id ~ '^[a-f0-9]{32}$'
    ),
    market_source_id text NOT NULL,
    venue_id text NOT NULL,
    source_session_id text NOT NULL REFERENCES
        analytics.native_public_trade_source_sessions(source_session_id),
    transport_epoch_id text NOT NULL CHECK (
        transport_epoch_id ~ '^[a-f0-9]{32}$'
    ),
    product_category text NOT NULL
        CHECK (product_category IN ('SPOT','LINEAR')),
    dataset_topic text NOT NULL
        CHECK (dataset_topic ~ '^[A-Za-z0-9_./:*-]{1,128}$'),
    external_symbol text NOT NULL DEFAULT ''
        CHECK (external_symbol='' OR external_symbol ~ '^[A-Z0-9]{2,32}$'),
    instrument_id text,
    quality_state text NOT NULL
        CHECK (quality_state IN (
            'GAP','NO_DATA','STALE','NOT_LISTED','UNKNOWN','RECOVERING'
        )),
    reason text NOT NULL CHECK(reason ~ '^[A-Z][A-Z0-9_]{2,127}$'),
    observed_at timestamptz NOT NULL,
    producer_release_commit text NOT NULL CHECK(
        producer_release_commit ~ '^[a-f0-9]{40}$'
    ),
    committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (market_source_id,venue_id)
        REFERENCES identity.market_sources(market_source_id,venue_id),
    FOREIGN KEY (
        instrument_id, venue_id, product_category, external_symbol
    ) REFERENCES identity.instruments (
        instrument_id, venue_id, product_category, external_symbol
    ),
    CHECK (
        (dataset_topic='*' AND external_symbol='' AND instrument_id IS NULL)
        OR
        (dataset_topic<>'*' AND external_symbol<>'' AND instrument_id IS NOT NULL)
    ),
    CHECK (
        quality_state<>'NOT_LISTED'
        OR dataset_topic='REST:market/instruments-info:' || external_symbol
    )
);
CREATE INDEX IF NOT EXISTS public_source_quality_by_scope_recent
ON analytics.public_market_source_quality_events (
    source_session_id, product_category, dataset_topic,
    external_symbol, observed_at DESC
);
REVOKE ALL ON analytics.public_market_source_quality_events FROM PUBLIC;
GRANT SELECT, INSERT ON analytics.public_market_source_quality_events TO cripta;
REVOKE UPDATE, DELETE, TRUNCATE
ON analytics.public_market_source_quality_events FROM cripta;
DO $guard$
BEGIN
    IF NOT has_table_privilege(
        'cripta','analytics.public_market_source_quality_events','SELECT'
    ) OR NOT has_table_privilege(
        'cripta','analytics.public_market_source_quality_events','INSERT'
    ) OR has_table_privilege(
        'cripta','analytics.public_market_source_quality_events','UPDATE'
    ) OR has_table_privilege(
        'cripta','analytics.public_market_source_quality_events','DELETE'
    ) THEN RAISE EXCEPTION 'PUBLIC_SOURCE_QUALITY_PRIVILEGE_DRIFT';
    END IF;
END $guard$;
COMMIT;
