-- Supersede only the compatibility-version CHECK; preserve append-only rows.
-- V1 attestations remain valid and traceable. V2 is the reviewed exact-delta
-- contract for the 2026-11-06 P90 stabilization release.
BEGIN;
SET LOCAL lock_timeout='3s';

ALTER TABLE strategy_entry.p90l90_real_source_release_compatibility
  DROP CONSTRAINT IF EXISTS
    p90l90_real_source_release_compatibility_contract_version_check;

ALTER TABLE strategy_entry.p90l90_real_source_release_compatibility
  ADD CONSTRAINT p90l90_real_source_release_compatibility_contract_version_check
  CHECK (
    contract_version IN (
      'P90_REAL_SOURCE_RELEASE_COMPATIBILITY_V1',
      'P90_REAL_SOURCE_RELEASE_COMPATIBILITY_V2'
    )
  );

COMMIT;
