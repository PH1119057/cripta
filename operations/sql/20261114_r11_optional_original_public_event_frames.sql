-- #470 R11 stage5 SOURCE-ONLY. Original provider-frame evidence for
-- optional SPOT publicTrade and SPOT/LINEAR BOOK, LINEAR TICKER/LIQUIDATION.
-- One native public MarketSource writer/PG connection; do NOT deploy until
-- approved offline migration/actor/retention/backup/writer ownership gates.
-- P90 LINEAR original publicTrade is ALREADY kept separately in the native
-- source event journal: this table explicitly rejects duplicate LINEAR TRADE.
-- Raw provider frame is the parsed JSON envelope, NOT original WS bytes.
-- JSONB preserves arrays/identities and original factual content, but does
-- NOT itself prove transport continuity, a complete source epoch or MAYAK.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '20s';

CREATE TABLE IF NOT EXISTS analytics.public_source_original_event_frames (
    source_session_id text NOT NULL
        REFERENCES analytics.native_public_trade_source_sessions(source_session_id),
    transport_epoch_id text NOT NULL
        CHECK(transport_epoch_id ~ '^[0-9a-f]{32}$'),
    market_source_id text NOT NULL,
    venue_id text NOT NULL DEFAULT 'venue-bybit',
    instrument_id text NOT NULL,
    product_category text NOT NULL
        CHECK(product_category IN ('SPOT','LINEAR')),
    external_symbol text NOT NULL CHECK(
        external_symbol ~ '^[A-Z0-9]{2,32}$'
    ),
    fact_family text NOT NULL CHECK(fact_family IN (
        'SPOT_TRADE','ORDERBOOK_50','TICKER','LIQUIDATION'
    )),
    source_topic text NOT NULL CHECK(length(source_topic) BETWEEN 5 AND 72),
    source_frame_number bigint NOT NULL CHECK(source_frame_number > 0),
    frame_type text NOT NULL CHECK(length(frame_type) <= 16),
    provider_frame_at timestamptz NOT NULL,
    source_event_first_at timestamptz NOT NULL,
    source_event_last_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    provider_frame jsonb NOT NULL CHECK(
        jsonb_typeof(provider_frame)='object'
    ),
    decoded_original_facts jsonb NOT NULL CHECK(
        jsonb_typeof(decoded_original_facts)='array'
        AND jsonb_array_length(decoded_original_facts) BETWEEN 1 AND 4096
    ),
    frame_content_sha256 text NOT NULL CHECK(
        frame_content_sha256 ~ '^[0-9a-f]{64}$'
    ),
    producer_release_commit text NOT NULL CHECK(
        producer_release_commit ~ '^[0-9a-f]{40}$'
    ),
    committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (
        source_session_id,transport_epoch_id,product_category,
        source_frame_number
    ),
    FOREIGN KEY (market_source_id,venue_id)
        REFERENCES identity.market_sources(market_source_id,venue_id),
    FOREIGN KEY (
        instrument_id,venue_id,product_category,external_symbol
    ) REFERENCES identity.instruments (
        instrument_id,venue_id,product_category,external_symbol
    ),
    CHECK(source_event_first_at <= source_event_last_at),
    CHECK(provider_frame_at <= received_at + interval '5 seconds'),
    CHECK(source_event_last_at <= received_at + interval '5 seconds'),
    CHECK (
        (fact_family='SPOT_TRADE'
         AND product_category='SPOT'
         AND source_topic='publicTrade.'||external_symbol)
        OR
        (fact_family='ORDERBOOK_50'
         AND source_topic='orderbook.50.'||external_symbol)
        OR
        (fact_family='TICKER'
         AND product_category='LINEAR'
         AND source_topic='tickers.'||external_symbol)
        OR
        (fact_family='LIQUIDATION'
         AND product_category='LINEAR'
         AND source_topic='allLiquidation.'||external_symbol)
    )
);

CREATE INDEX IF NOT EXISTS public_source_original_event_frames_replay_idx
ON analytics.public_source_original_event_frames (
    source_session_id,transport_epoch_id,product_category,
    instrument_id,source_topic,source_frame_number
);

-- Only the approved native MarketSource runtime role can append source rows.
-- This is CANONICAL_SQL_GRANTS, not production effective grants; DDL is
-- executed by a separately authorized migration actor (usually postgres).
REVOKE ALL ON analytics.public_source_original_event_frames FROM PUBLIC;
GRANT SELECT, INSERT ON analytics.public_source_original_event_frames TO cripta;
REVOKE UPDATE, DELETE, TRUNCATE
ON analytics.public_source_original_event_frames FROM cripta;

DO $guard$
BEGIN
  IF NOT has_table_privilege(
      'cripta','analytics.public_source_original_event_frames','SELECT'
  ) OR NOT has_table_privilege(
      'cripta','analytics.public_source_original_event_frames','INSERT'
  ) OR has_table_privilege(
      'cripta','analytics.public_source_original_event_frames','UPDATE'
  ) OR has_table_privilege(
      'cripta','analytics.public_source_original_event_frames','DELETE'
  ) THEN RAISE EXCEPTION 'R11_ORIGINAL_EVENT_FRAME_GRANTS_INVALID';
  END IF;
END
$guard$;
COMMIT;
