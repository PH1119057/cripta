-- #470 R06: append-only bootstrap/publication provenance for the EXISTING
-- P90 Strategy/Plan decision cursor. This is NOT a second cursor, a source
-- checkpoint reset, strategy activation or financial permission.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

ALTER TABLE strategy_entry.p90_scoped_native_consumer_checkpoints
  ADD COLUMN IF NOT EXISTS bootstrap_publication_id bigint
    REFERENCES analytics.market_publications(publication_id);
ALTER TABLE strategy_entry.p90_scoped_native_consumer_checkpoints
  ADD COLUMN IF NOT EXISTS last_publication_id bigint
    REFERENCES analytics.market_publications(publication_id);
ALTER TABLE strategy_entry.p90_scoped_native_consumer_checkpoints
  ADD COLUMN IF NOT EXISTS bootstrap_source_at timestamptz;

-- Older exact immutable checkpoints may exist. Never fabricate provenance.
-- The R06 physical writer fails closed if these columns are NULL or if an
-- existing checkpoint has not undergone separate versioned compatibility.
COMMENT ON COLUMN
 strategy_entry.p90_scoped_native_consumer_checkpoints.bootstrap_publication_id
 IS 'Immutable first-new-published-boundary R06 Entry epoch; never technical Observer ACK';
COMMENT ON COLUMN
 strategy_entry.p90_scoped_native_consumer_checkpoints.last_publication_id
 IS 'Last verified publication whose native prefix was processed inside this existing cursor transaction';

DO $r06_acl$
BEGIN
  IF NOT has_table_privilege(
    'cripta','strategy_entry.p90_scoped_native_consumer_checkpoints','SELECT'
  ) OR NOT has_table_privilege(
    'cripta','strategy_entry.p90_scoped_native_consumer_checkpoints','INSERT'
  ) OR NOT has_table_privilege(
    'cripta','strategy_entry.p90_scoped_native_consumer_checkpoints','UPDATE'
  ) OR has_table_privilege(
    'cripta','strategy_entry.p90_scoped_native_consumer_checkpoints','DELETE'
  ) THEN
    RAISE EXCEPTION 'R06_P90_DECISION_CHECKPOINT_ACL_INVALID';
  END IF;
END $r06_acl$;
COMMIT;
