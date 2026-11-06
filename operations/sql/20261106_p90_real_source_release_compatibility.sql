-- Explicit operator attestation only. No provenance UPDATE or financial rights.
BEGIN;
SET LOCAL lock_timeout='3s';
CREATE TABLE IF NOT EXISTS strategy_entry.p90l90_real_source_release_compatibility (
  epoch_id text NOT NULL REFERENCES strategy_entry.p90l90_real_source_epochs(epoch_id),
  loaded_commit text NOT NULL CHECK (loaded_commit ~ '^[0-9a-f]{40}$'),
  origin_commit text NOT NULL CHECK (origin_commit ~ '^[0-9a-f]{40}$'),
  contract_version text NOT NULL
    CHECK (contract_version='P90_REAL_SOURCE_RELEASE_COMPATIBILITY_V1'),
  consumer_digest text NOT NULL CHECK (consumer_digest ~ '^[0-9a-f]{64}$'),
  plan_pair_digest text NOT NULL CHECK (plan_pair_digest ~ '^[0-9a-f]{64}$'),
  checkpoint_digest text NOT NULL CHECK (checkpoint_digest ~ '^[0-9a-f]{64}$'),
  checkpoint_evidence jsonb NOT NULL
    CHECK (jsonb_typeof(checkpoint_evidence)='array'
       AND jsonb_array_length(checkpoint_evidence)=5),
  registered_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(epoch_id,loaded_commit)
);
CREATE OR REPLACE FUNCTION strategy_entry.guard_real_source_compatibility_immutable()
RETURNS trigger LANGUAGE plpgsql AS $body$
BEGIN
  RAISE EXCEPTION 'P90_REAL_SOURCE_COMPATIBILITY_APPEND_ONLY';
END
$body$;
DROP TRIGGER IF EXISTS p90_real_source_compatibility_immutable
  ON strategy_entry.p90l90_real_source_release_compatibility;
CREATE TRIGGER p90_real_source_compatibility_immutable
BEFORE UPDATE OR DELETE ON strategy_entry.p90l90_real_source_release_compatibility
FOR EACH ROW EXECUTE FUNCTION strategy_entry.guard_real_source_compatibility_immutable();
REVOKE ALL ON strategy_entry.p90l90_real_source_release_compatibility FROM PUBLIC;
GRANT SELECT,INSERT ON strategy_entry.p90l90_real_source_release_compatibility TO cripta;
REVOKE UPDATE,DELETE,TRUNCATE ON
  strategy_entry.p90l90_real_source_release_compatibility FROM cripta;
COMMIT;
