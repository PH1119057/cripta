-- #470 R05: Additive immutable references from one publication to
-- source-committed SPOT/LINEAR compact market seconds.
-- Preserve legacy publication IDs. No historical backfill, deletes, or
-- representation of incomplete source paths as READY.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '20s';

ALTER TABLE analytics.market_publications
    ADD COLUMN IF NOT EXISTS compact_second_refs jsonb
      NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE analytics.market_publications
    ADD COLUMN IF NOT EXISTS compact_field_coverage jsonb
      NOT NULL DEFAULT '{}'::jsonb;

DO $checks$
BEGIN
  IF NOT EXISTS (
      SELECT 1 FROM pg_constraint
       WHERE conrelid='analytics.market_publications'::regclass
         AND conname='market_publications_compact_refs_object_chk'
  ) THEN
    ALTER TABLE analytics.market_publications
      ADD CONSTRAINT market_publications_compact_refs_object_chk
      CHECK (jsonb_typeof(compact_second_refs)='object');
  END IF;
  IF NOT EXISTS (
      SELECT 1 FROM pg_constraint
       WHERE conrelid='analytics.market_publications'::regclass
         AND conname='market_publications_compact_coverage_object_chk'
  ) THEN
    ALTER TABLE analytics.market_publications
      ADD CONSTRAINT market_publications_compact_coverage_object_chk
      CHECK (jsonb_typeof(compact_field_coverage)='object');
  END IF;
END
$checks$;

-- The existing publication table remains INSERT-only for the normal
-- producer. No implicit source writer rights to UPDATE or DELETE.
DO $rights$
BEGIN
  IF NOT has_table_privilege(
      'cripta','analytics.market_publications','SELECT'
  ) OR NOT has_table_privilege(
      'cripta','analytics.market_publications','INSERT'
  ) OR has_table_privilege(
      'cripta','analytics.market_publications','UPDATE'
  ) OR has_table_privilege(
      'cripta','analytics.market_publications','DELETE'
  ) THEN
      RAISE EXCEPTION 'MARKET_PUBLICATION_COMPACT_RIGHTS_INVALID';
  END IF;
END
$rights$;

COMMIT;
