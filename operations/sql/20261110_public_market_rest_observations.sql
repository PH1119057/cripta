-- #470 R04 source-only REST observations; NOT an account/private Exchange fact.
-- Owner: existing Native MarketSource process / exact native producer epoch.
-- Additive, append-only, no Strategy/consumer cutover or default activation.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

CREATE TABLE IF NOT EXISTS analytics.public_market_rest_observations (
    rest_observation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    market_source_id text NOT NULL,
    venue_id text NOT NULL,
    source_session_id text NOT NULL REFERENCES
      analytics.native_public_trade_source_sessions(source_session_id),
    instrument_id text NOT NULL,
    product_category text NOT NULL CHECK(product_category IN ('SPOT','LINEAR')),
    external_symbol text NOT NULL CHECK(length(external_symbol) BETWEEN 2 AND 32),
    dataset text NOT NULL CHECK(dataset IN ('ACCOUNT_RATIO','INSTRUMENT_INFO')),
    source_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    source_period text NOT NULL DEFAULT '',
    compact_values jsonb NOT NULL CHECK(jsonb_typeof(compact_values)='object'),
    producer_release_commit text NOT NULL CHECK(
      producer_release_commit ~ '^[a-f0-9]{40}$'
    ),
    UNIQUE (source_session_id,instrument_id,dataset,source_at),
    FOREIGN KEY (market_source_id,venue_id)
      REFERENCES identity.market_sources(market_source_id,venue_id),
    FOREIGN KEY (instrument_id,venue_id,product_category,external_symbol)
      REFERENCES identity.instruments(
        instrument_id,venue_id,product_category,external_symbol
      ),
    CHECK (source_at<=received_at + interval '5 seconds'),
    CHECK (
      (dataset='ACCOUNT_RATIO' AND product_category='LINEAR'
          AND source_period='5min')
      OR (dataset='INSTRUMENT_INFO' AND source_period='')
    )
);

CREATE INDEX IF NOT EXISTS public_market_rest_source_recent_idx
ON analytics.public_market_rest_observations (
  source_session_id,instrument_id,dataset,source_at DESC
);

REVOKE ALL ON analytics.public_market_rest_observations FROM PUBLIC;
GRANT SELECT,INSERT ON analytics.public_market_rest_observations TO cripta;
REVOKE UPDATE,DELETE,TRUNCATE ON analytics.public_market_rest_observations
FROM cripta;
GRANT USAGE,SELECT ON SEQUENCE
  analytics.public_market_rest_observations_rest_observation_id_seq TO cripta;

DO $verify$
BEGIN
  IF NOT has_table_privilege(
      'cripta','analytics.public_market_rest_observations','SELECT'
  ) OR NOT has_table_privilege(
      'cripta','analytics.public_market_rest_observations','INSERT'
  ) OR has_table_privilege(
      'cripta','analytics.public_market_rest_observations','UPDATE'
  ) OR has_table_privilege(
      'cripta','analytics.public_market_rest_observations','DELETE'
  ) THEN
      RAISE EXCEPTION 'PUBLIC_REST_OBSERVATION_PRIVILEGE_DRIFT';
  END IF;
END $verify$;
COMMIT;
