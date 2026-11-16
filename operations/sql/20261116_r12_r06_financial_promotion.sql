-- #642 R12: durable handoff from R06 SOURCE_ONLY facts to account-scoped
-- Universal Entry financial admission. R06 remains NO-ORDER; this migration
-- adds immutable future-signal payload plus a separate disarmed promotion cursor.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

ALTER TABLE strategy_entry.p90_r06_publication_observations
  ADD COLUMN IF NOT EXISTS candidate_fact jsonb;

COMMENT ON COLUMN strategy_entry.p90_r06_publication_observations.candidate_fact
 IS 'Exact immutable P90 crossing MarketFactEnvelope payload for future account-scoped Universal Entry admission; NULL means historical SOURCE_ONLY evidence and is never promoted';

CREATE TABLE IF NOT EXISTS strategy_entry.p90_r06_financial_promotion_checkpoints (
  trading_account_id text NOT NULL,
  strategy_id text NOT NULL,
  strategy_version text NOT NULL,
  strategy_activation_id text NOT NULL,
  strategy_execution_binding_id text NOT NULL,
  source_session_id text NOT NULL REFERENCES
    analytics.native_public_trade_source_sessions(source_session_id),
  frame_number bigint NOT NULL CHECK(frame_number >= 0),
  frame_index integer NOT NULL CHECK(frame_index >= 0),
  last_publication_id bigint NOT NULL REFERENCES analytics.market_publications(publication_id),
  consumer_release_commit text NOT NULL CHECK(consumer_release_commit ~ '^[0-9a-f]{40}$'),
  state text NOT NULL CHECK(state IN ('DISARMED_BASELINE','ARMED_PROGRESS')),
  last_signal_id text,
  last_outcome text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(trading_account_id,strategy_id,strategy_version)
);

REVOKE ALL ON strategy_entry.p90_r06_financial_promotion_checkpoints FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON strategy_entry.p90_r06_financial_promotion_checkpoints TO cripta;
REVOKE DELETE,TRUNCATE ON strategy_entry.p90_r06_financial_promotion_checkpoints FROM cripta;

DO $r12_acl$
BEGIN
  IF NOT has_table_privilege(
    'cripta','strategy_entry.p90_r06_financial_promotion_checkpoints','SELECT'
  ) OR NOT has_table_privilege(
    'cripta','strategy_entry.p90_r06_financial_promotion_checkpoints','INSERT'
  ) OR NOT has_table_privilege(
    'cripta','strategy_entry.p90_r06_financial_promotion_checkpoints','UPDATE'
  ) OR has_table_privilege(
    'cripta','strategy_entry.p90_r06_financial_promotion_checkpoints','DELETE'
  ) THEN
    RAISE EXCEPTION 'R12_P90_FINANCIAL_PROMOTION_CHECKPOINT_ACL_INVALID';
  END IF;
END $r12_acl$;

COMMIT;
