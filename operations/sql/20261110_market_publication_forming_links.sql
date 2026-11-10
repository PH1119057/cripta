-- #470 R05: one shared publication references forming 1m ORIGINAL observations.
-- Never insert a forming bar into analytics.closed_candles and never certify
-- "FORMING_KLINE" as provider-confirmed CLOSED_KLINE.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

ALTER TABLE analytics.market_publications
  ADD COLUMN IF NOT EXISTS forming_minute_refs jsonb
    NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE analytics.market_publications
  ADD COLUMN IF NOT EXISTS forming_field_coverage jsonb
    NOT NULL DEFAULT '{}'::jsonb;

DO $guard$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid='analytics.market_publications'::regclass
      AND conname='market_publications_forming_refs_object_chk'
  ) THEN
    ALTER TABLE analytics.market_publications
      ADD CONSTRAINT market_publications_forming_refs_object_chk
      CHECK(jsonb_typeof(forming_minute_refs)='object');
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid='analytics.market_publications'::regclass
      AND conname='market_publications_forming_coverage_object_chk'
  ) THEN
    ALTER TABLE analytics.market_publications
      ADD CONSTRAINT market_publications_forming_coverage_object_chk
      CHECK(jsonb_typeof(forming_field_coverage)='object');
  END IF;
END $guard$;
COMMIT;
