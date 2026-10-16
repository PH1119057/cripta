BEGIN;

SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

CREATE TABLE IF NOT EXISTS strategy_entry.strategy_execution_bindings (
    strategy_execution_binding_id text PRIMARY KEY,
    strategy_activation_id text NOT NULL
        REFERENCES strategy_entry.strategy_activations(activation_id),
    execution_leg text NOT NULL,
    execution_role text NOT NULL,
    trading_account_id text NOT NULL
        REFERENCES identity.trading_accounts(trading_account_id),
    allocation_mode text NOT NULL DEFAULT 'DEDICATED_STRATEGY',
    binding_state text NOT NULL,
    effective_from timestamptz NOT NULL,
    effective_to timestamptz,
    operator text NOT NULL,
    source text NOT NULL,
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (btrim(execution_leg) <> ''),
    CHECK (btrim(execution_role) <> ''),
    CHECK (allocation_mode = 'DEDICATED_STRATEGY'),
    CHECK (binding_state IN ('ACTIVE','INACTIVE','RETIRED')),
    CHECK (effective_to IS NULL OR effective_to > effective_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_strategy_execution_bindings_active_leg
    ON strategy_entry.strategy_execution_bindings(
        strategy_activation_id,
        execution_leg
    )
    WHERE binding_state='ACTIVE';

CREATE UNIQUE INDEX IF NOT EXISTS ux_strategy_execution_bindings_dedicated_account
    ON strategy_entry.strategy_execution_bindings(trading_account_id)
    WHERE binding_state='ACTIVE'
      AND allocation_mode='DEDICATED_STRATEGY';

GRANT SELECT ON strategy_entry.strategy_execution_bindings TO cripta;

COMMIT;
