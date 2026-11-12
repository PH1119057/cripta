-- Preserve old GAP checkpoints. New no-order epoch has a new future boundary.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';
CREATE TABLE IF NOT EXISTS strategy_entry.p90_r06_fresh_epoch_transitions (
 strategy_id text NOT NULL, strategy_version text NOT NULL,
 previous_source_session_id text NOT NULL REFERENCES analytics.native_public_trade_source_sessions(source_session_id),
 source_session_id text NOT NULL REFERENCES analytics.native_public_trade_source_sessions(source_session_id),
 previous_consumer_release text NOT NULL,
 consumer_release_commit text NOT NULL,
 previous_frame_number bigint NOT NULL, previous_frame_index integer NOT NULL,
 previous_last_exec_id text,
 previous_bootstrap_publication_id bigint NOT NULL REFERENCES analytics.market_publications(publication_id),
 previous_last_publication_id bigint NOT NULL REFERENCES analytics.market_publications(publication_id),
 previous_causal_state_sha256 text NOT NULL,
 bootstrap_publication_id bigint NOT NULL REFERENCES analytics.market_publications(publication_id),
 bootstrap_source_at timestamptz NOT NULL,
 transition_kind text NOT NULL DEFAULT 'NO_ORDER_FRESH_EPOCH_AFTER_GAP'
   CHECK(transition_kind='NO_ORDER_FRESH_EPOCH_AFTER_GAP'),
 recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(strategy_id,strategy_version,source_session_id),
 CHECK(previous_source_session_id<>source_session_id)
);
REVOKE ALL ON strategy_entry.p90_r06_fresh_epoch_transitions FROM PUBLIC;
GRANT SELECT,INSERT ON strategy_entry.p90_r06_fresh_epoch_transitions TO cripta;
REVOKE UPDATE,DELETE,TRUNCATE ON strategy_entry.p90_r06_fresh_epoch_transitions FROM cripta;
COMMIT;
