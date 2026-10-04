
BEGIN;

CREATE TABLE IF NOT EXISTS strategy_entry.reverse_transitions (
    reverse_transition_id text PRIMARY KEY,
    strategy_position_id text NOT NULL
      REFERENCES runtime.position_ownership(position_id),
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    exit_plan_fingerprint text NOT NULL,
    strategy_activation_id text NOT NULL,
    signal_id text NOT NULL
      REFERENCES strategy_entry.strategy_signals(signal_id),
    source_strategy_attempt_id text NOT NULL
      REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id),
    symbol text NOT NULL,
    from_direction text NOT NULL CHECK (from_direction IN ('LONG','SHORT')),
    to_direction text NOT NULL CHECK (to_direction IN ('LONG','SHORT')),
    state text NOT NULL CHECK (
      state IN (
        'REQUESTED',
        'CLOSE_DISPATCHED',
        'FLAT_CONFIRMED',
        'OPEN_REQUESTED',
        'OPEN_DISPATCHED',
        'COMPLETED',
        'RECONCILIATION_REQUIRED',
        'FAILED'
      )
    ),
    close_command_id text,
    open_strategy_attempt_id text,
    open_entry_decision_id text,
    open_execution_request_id text,
    requested_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    completed_at timestamptz,
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload)='object'),
    UNIQUE(source_strategy_attempt_id),
    CHECK (from_direction <> to_direction)
);

CREATE UNIQUE INDEX IF NOT EXISTS reverse_transitions_one_active_position
ON strategy_entry.reverse_transitions(strategy_position_id)
WHERE state NOT IN ('COMPLETED','FAILED');

CREATE INDEX IF NOT EXISTS reverse_transitions_state_requested
ON strategy_entry.reverse_transitions(state,requested_at);

GRANT SELECT,INSERT,UPDATE ON strategy_entry.reverse_transitions TO cripta;

COMMIT;
