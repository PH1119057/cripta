-- #470 R04 forming 1m: append-only public source observations, not closed candles.
-- One existing MarketSource writer owns the only source epoch and the exact
-- provider event; no duplicate raw trade tape and no consumer switching.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '20s';

CREATE TABLE IF NOT EXISTS analytics.public_forming_minute_observations (
    forming_observation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    market_source_id text NOT NULL,
    venue_id text NOT NULL,
    source_session_id text NOT NULL REFERENCES
      analytics.native_public_trade_source_sessions(source_session_id),
    instrument_id text NOT NULL,
    product_category text NOT NULL CHECK(product_category='LINEAR'),
    external_symbol text NOT NULL CHECK(length(external_symbol) BETWEEN 2 AND 32),
    timeframe text NOT NULL CHECK(timeframe='1'),
    opened_at timestamptz NOT NULL,
    closed_at timestamptz NOT NULL,
    source_at timestamptz NOT NULL,
    provider_last_match_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL,
    forming_ohlcv jsonb NOT NULL CHECK(jsonb_typeof(forming_ohlcv)='object'),
    producer_release_commit text NOT NULL
      CHECK(producer_release_commit ~ '^[a-f0-9]{40}$'),
    UNIQUE (source_session_id,instrument_id,opened_at,source_at),
    FOREIGN KEY (market_source_id,venue_id)
      REFERENCES identity.market_sources(market_source_id,venue_id),
    FOREIGN KEY (instrument_id,venue_id,product_category,external_symbol)
      REFERENCES identity.instruments(
        instrument_id,venue_id,product_category,external_symbol
      ),
    CHECK (date_trunc('minute',opened_at)=opened_at),
    CHECK (closed_at=opened_at+interval '1 minute'),
    CHECK (opened_at<=source_at AND source_at<closed_at),
    CHECK (opened_at<=provider_last_match_at AND
           provider_last_match_at<closed_at),
    CHECK (provider_last_match_at<=source_at+interval '5 seconds'),
    CHECK (source_at<=received_at+interval '5 seconds')
);

CREATE INDEX IF NOT EXISTS public_forming_minute_recent_idx
ON analytics.public_forming_minute_observations (
  source_session_id,instrument_id,source_at DESC
);

REVOKE ALL ON analytics.public_forming_minute_observations FROM PUBLIC;
GRANT SELECT,INSERT ON analytics.public_forming_minute_observations TO cripta;
REVOKE UPDATE,DELETE,TRUNCATE ON analytics.public_forming_minute_observations
FROM cripta;
GRANT USAGE,SELECT ON SEQUENCE
  analytics.public_forming_minute_observations_forming_observation_id_seq
TO cripta;

DO $verify$
BEGIN
  IF NOT has_table_privilege(
      'cripta','analytics.public_forming_minute_observations','SELECT'
  ) OR NOT has_table_privilege(
      'cripta','analytics.public_forming_minute_observations','INSERT'
  ) OR has_table_privilege(
      'cripta','analytics.public_forming_minute_observations','UPDATE'
  ) OR has_table_privilege(
      'cripta','analytics.public_forming_minute_observations','DELETE'
  ) THEN
      RAISE EXCEPTION 'PUBLIC_FORMING_OBSERVATION_PRIVILEGE_DRIFT';
  END IF;
END $verify$;
COMMIT;
