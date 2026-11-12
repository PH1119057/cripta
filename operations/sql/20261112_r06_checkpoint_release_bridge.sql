-- Explicit no-order, same-epoch release adoption; NEVER a cursor reset.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';
CREATE TABLE IF NOT EXISTS strategy_entry.p90_r06_checkpoint_release_bridges (
  strategy_id text NOT NULL,
  strategy_version text NOT NULL,
  source_session_id text NOT NULL REFERENCES analytics.native_public_trade_source_sessions(source_session_id),
  previous_release text NOT NULL CHECK(previous_release ~ '^[0-9a-f]{40}$'),
  consumer_release_commit text NOT NULL CHECK(consumer_release_commit ~ '^[0-9a-f]{40}$'),
  frame_number bigint NOT NULL CHECK(frame_number >= 0),
  frame_index integer NOT NULL CHECK(frame_index >= 0),
  last_exec_id text,
  bootstrap_publication_id bigint NOT NULL REFERENCES analytics.market_publications(publication_id),
  last_publication_id bigint NOT NULL REFERENCES analytics.market_publications(publication_id),
  causal_state_sha256 text NOT NULL CHECK(causal_state_sha256 ~ '^[0-9a-f]{64}$'),
  bridged_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(strategy_id,strategy_version,source_session_id,consumer_release_commit)
);
REVOKE ALL ON strategy_entry.p90_r06_checkpoint_release_bridges FROM PUBLIC;
GRANT SELECT,INSERT ON strategy_entry.p90_r06_checkpoint_release_bridges TO cripta;
REVOKE UPDATE,DELETE,TRUNCATE ON strategy_entry.p90_r06_checkpoint_release_bridges FROM cripta;
COMMIT;
