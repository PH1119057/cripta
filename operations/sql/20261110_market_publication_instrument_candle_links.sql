-- #470 R03/R05: one publication, no bare-symbol SPOT/LINEAR aliasing.
-- References only to existing canonical analytics.closed_candles_effective.
-- Original OHLCV remains in analytics.closed_candles, no second candle lake.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

ALTER TABLE analytics.market_publications
  ADD COLUMN IF NOT EXISTS closed_instrument_refs jsonb
    NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE analytics.market_publications
  ADD COLUMN IF NOT EXISTS closed_instrument_coverage jsonb
    NOT NULL DEFAULT '{}'::jsonb;

DO $guard$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid='analytics.market_publications'::regclass
      AND conname='market_publications_closed_instrument_refs_obj_chk'
  ) THEN
    ALTER TABLE analytics.market_publications
      ADD CONSTRAINT market_publications_closed_instrument_refs_obj_chk
      CHECK(jsonb_typeof(closed_instrument_refs)='object');
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid='analytics.market_publications'::regclass
      AND conname='market_publications_closed_instrument_coverage_obj_chk'
  ) THEN
    ALTER TABLE analytics.market_publications
      ADD CONSTRAINT market_publications_closed_instrument_coverage_obj_chk
      CHECK(jsonb_typeof(closed_instrument_coverage)='object');
  END IF;
END $guard$;
COMMIT;
