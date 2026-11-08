-- #470: one durable checkpoint per exact P90 Strategy and native source
-- session. Consumer checkpoint is not a source cursor nor financial permission.
-- Database transaction must contain both Engine effects and checkpoint update.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

CREATE TABLE IF NOT EXISTS strategy_entry.p90_scoped_native_consumer_checkpoints (
    strategy_id text NOT NULL CHECK(length(strategy_id) BETWEEN 1 AND 128),
    strategy_version text NOT NULL CHECK(length(strategy_version) BETWEEN 1 AND 128),
    entry_plan_fingerprint text NOT NULL CHECK(length(entry_plan_fingerprint)=64),
    exit_plan_fingerprint text NOT NULL CHECK(length(exit_plan_fingerprint)=64),
    source_session_id text NOT NULL REFERENCES
        analytics.native_public_trade_source_sessions(source_session_id),
    producer_release_commit text NOT NULL CHECK(length(producer_release_commit)=40),
    consumer_release_commit text NOT NULL CHECK(length(consumer_release_commit)=40),
    frame_number bigint NOT NULL CHECK(frame_number >= 0),
    frame_index integer NOT NULL CHECK(frame_index >= 0),
    last_exec_id text,
    checked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (strategy_id,strategy_version,source_session_id)
);
REVOKE ALL ON strategy_entry.p90_scoped_native_consumer_checkpoints FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON
    strategy_entry.p90_scoped_native_consumer_checkpoints TO cripta;
REVOKE DELETE,TRUNCATE ON
    strategy_entry.p90_scoped_native_consumer_checkpoints FROM cripta;
DO $verify$
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
        RAISE EXCEPTION 'P90_SCOPED_CONSUMER_CHECKPOINT_ACL_DRIFT';
    END IF;
END $verify$;
COMMIT;
