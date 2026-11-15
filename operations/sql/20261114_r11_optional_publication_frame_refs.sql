-- R11 #470 source-only migration: immutable optional original frame refs.
-- Do not apply to production before approved migration actor/backup/rollback
-- and existing producer release gates. Existing publications remain unproven.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

ALTER TABLE analytics.market_publications
  ADD COLUMN IF NOT EXISTS original_optional_frame_refs jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS original_optional_frame_coverage jsonb NOT NULL DEFAULT '{}'::jsonb;

DO $r11$
BEGIN
  IF NOT EXISTS (
      SELECT 1 FROM pg_constraint
      WHERE conrelid='analytics.market_publications'::regclass
        AND conname='r11_optional_frame_refs_object'
  ) THEN
    ALTER TABLE analytics.market_publications
      ADD CONSTRAINT r11_optional_frame_refs_object
        CHECK (jsonb_typeof(original_optional_frame_refs)='object'
           AND jsonb_typeof(original_optional_frame_coverage)='object');
  END IF;
END
$r11$;

-- No UPDATE or DELETE permissions are created for the runtime role.
REVOKE UPDATE,DELETE,TRUNCATE ON analytics.market_publications FROM cripta;
COMMIT;
