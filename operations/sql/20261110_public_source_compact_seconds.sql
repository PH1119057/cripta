-- #470 R04: compact event-driven public source read-model only, NOT a raw lake.
-- One authorized existing Native MarketSource process writes both spot+linear
-- under a source epoch; trading consumers are NOT changed in this migration.
-- Retention / transport-epoch gap and 20-symbol roll-out require separately
-- verified storage, writer permissions and capability/parity gates.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '20s';

CREATE TABLE IF NOT EXISTS analytics.public_market_source_seconds (
    market_source_id text NOT NULL
      REFERENCES identity.market_sources(market_source_id),
    source_session_id text NOT NULL
      REFERENCES analytics.native_public_trade_source_sessions(source_session_id),
    instrument_id text NOT NULL
      REFERENCES identity.instruments(instrument_id),
    product_category text NOT NULL CHECK(product_category IN ('SPOT','LINEAR')),
    external_symbol text NOT NULL CHECK(length(external_symbol) BETWEEN 2 AND 32),
    observed_second timestamptz NOT NULL,
    source_time_min timestamptz NOT NULL,
    source_time_max timestamptz NOT NULL,
    compact_features jsonb NOT NULL CHECK(
      jsonb_typeof(compact_features)='object'
    ),
    producer_release_commit text NOT NULL CHECK(
      producer_release_commit ~ '^[a-f0-9]{40}$'
    ),
    committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (source_session_id,instrument_id,observed_second),
    FOREIGN KEY (
      instrument_id,market_source_id,product_category,external_symbol
    ) REFERENCES identity.instruments (
      instrument_id,market_source_id,product_category,external_symbol
    ) DEFERRABLE INITIALLY IMMEDIATE,
    CHECK (date_trunc('second',observed_second)=observed_second),
    CHECK (source_time_min<=source_time_max)
);

CREATE INDEX IF NOT EXISTS public_market_source_seconds_recent_idx
ON analytics.public_market_source_seconds (
  market_source_id,instrument_id,observed_second DESC
);

REVOKE ALL ON analytics.public_market_source_seconds FROM PUBLIC;
GRANT SELECT,INSERT ON analytics.public_market_source_seconds TO cripta;
REVOKE UPDATE,DELETE,TRUNCATE ON analytics.public_market_source_seconds FROM cripta;

DO $guard$
BEGIN
  IF NOT has_table_privilege(
      'cripta','analytics.public_market_source_seconds','SELECT'
  ) OR NOT has_table_privilege(
      'cripta','analytics.public_market_source_seconds','INSERT'
  ) OR has_table_privilege(
      'cripta','analytics.public_market_source_seconds','UPDATE'
  ) OR has_table_privilege(
      'cripta','analytics.public_market_source_seconds','DELETE'
  ) THEN RAISE EXCEPTION 'PUBLIC_MARKET_SOURCE_SECOND_PRIVILEGES_INVALID';
  END IF;
END
$guard$;
COMMIT;
