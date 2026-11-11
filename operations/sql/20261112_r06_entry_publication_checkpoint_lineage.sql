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

-- Durable native Entry automata and immutable source-only parity observations
-- remain inside the SAME legacy Strategy decision checkpoint transaction.
ALTER TABLE strategy_entry.p90_scoped_native_consumer_checkpoints
  ADD COLUMN IF NOT EXISTS causal_state jsonb NOT NULL
    DEFAULT '{"version":1,"by_symbol":{}}'::jsonb;
CREATE TABLE IF NOT EXISTS strategy_entry.p90_r06_publication_observations (
  strategy_id text NOT NULL,
  strategy_version text NOT NULL,
  source_session_id text NOT NULL REFERENCES
    analytics.native_public_trade_source_sessions(source_session_id),
  frame_number bigint NOT NULL CHECK(frame_number >= 0),
  frame_index integer NOT NULL CHECK(frame_index >= 0),
  native_exec_id text NOT NULL CHECK(length(native_exec_id)>0),
  publication_id bigint NOT NULL REFERENCES analytics.market_publications(publication_id),
  signal_id text NOT NULL CHECK(length(signal_id)>0),
  symbol text NOT NULL,
  direction text NOT NULL CHECK(direction IN ('LONG','SHORT')),
  event_at timestamptz NOT NULL,
  PRIMARY KEY(strategy_id,strategy_version,source_session_id,frame_number,frame_index,direction)
);
REVOKE ALL ON strategy_entry.p90_r06_publication_observations FROM PUBLIC;
GRANT SELECT,INSERT ON strategy_entry.p90_r06_publication_observations TO cripta;
REVOKE UPDATE,DELETE,TRUNCATE ON strategy_entry.p90_r06_publication_observations FROM cripta;


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
