BEGIN;

CREATE TABLE IF NOT EXISTS strategy_entry.execution_dispatches (
    dispatch_id text PRIMARY KEY,
    execution_request_id text NOT NULL UNIQUE
        REFERENCES strategy_entry.execution_requests(execution_request_id),
    command_id text UNIQUE,
    state text NOT NULL,
    reason text NOT NULL,
    strategy_attempt_id text NOT NULL,
    entry_decision_id text NOT NULL,
    signal_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    exit_plan_fingerprint text,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (state IN ('DISPATCHED','BLOCKED')),
    CHECK (jsonb_typeof(payload) = 'object'),
    CHECK (
        (state='DISPATCHED' AND command_id IS NOT NULL AND exit_plan_fingerprint IS NOT NULL)
        OR
        (state='BLOCKED' AND command_id IS NULL)
    ),
    FOREIGN KEY (strategy_attempt_id, signal_id)
        REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id, signal_id),
    FOREIGN KEY (entry_decision_id, strategy_attempt_id, signal_id)
        REFERENCES strategy_entry.entry_decisions(
            entry_decision_id, strategy_attempt_id, signal_id
        ),
    FOREIGN KEY (exit_plan_fingerprint)
        REFERENCES strategy_entry.exit_plans(exit_plan_fingerprint)
);

CREATE INDEX IF NOT EXISTS ix_strategy_entry_execution_dispatches_command
    ON strategy_entry.execution_dispatches(command_id)
    WHERE command_id IS NOT NULL;

DROP TRIGGER IF EXISTS execution_dispatches_immutable
    ON strategy_entry.execution_dispatches;
CREATE TRIGGER execution_dispatches_immutable
BEFORE UPDATE OR DELETE ON strategy_entry.execution_dispatches
FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();

REVOKE ALL ON strategy_entry.execution_dispatches FROM PUBLIC;
GRANT SELECT, INSERT ON strategy_entry.execution_dispatches TO cripta;
REVOKE UPDATE, DELETE ON strategy_entry.execution_dispatches FROM cripta;

COMMIT;
