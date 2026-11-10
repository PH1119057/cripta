-- #470 R05: REST source references in SAME immutable market_publications row.
-- One canonical publication_id with WS + native + REST references only.
-- No duplicate source prices/ratio/catalog JSON in per-second publication.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

ALTER TABLE analytics.market_publications
  ADD COLUMN IF NOT EXISTS rest_observation_refs jsonb
    NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE analytics.market_publications
  ADD COLUMN IF NOT EXISTS rest_field_coverage jsonb
    NOT NULL DEFAULT '{}'::jsonb;

DO $guard$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid='analytics.market_publications'::regclass
      AND conname='market_publications_rest_refs_object_chk'
  ) THEN
    ALTER TABLE analytics.market_publications
      ADD CONSTRAINT market_publications_rest_refs_object_chk
      CHECK(jsonb_typeof(rest_observation_refs)='object');
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid='analytics.market_publications'::regclass
      AND conname='market_publications_rest_coverage_object_chk'
  ) THEN
    ALTER TABLE analytics.market_publications
      ADD CONSTRAINT market_publications_rest_coverage_object_chk
      CHECK(jsonb_typeof(rest_field_coverage)='object');
  END IF;
END $guard$;
COMMIT;
