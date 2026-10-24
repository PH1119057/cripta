-- R04: Additive account-scoped OWNER ARM, no seed, no live activation.
-- The existing Strategy/symbol-scoped sessions remain unchanged.
-- Installation never inserts ACTIVE rows and never sets mainnet enabled.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

CREATE TABLE IF NOT EXISTS control.account_live_arm_sessions (
    account_live_arm_session_id text PRIMARY KEY,
    strategy_activation_id text NOT NULL
        REFERENCES strategy_entry.strategy_activations(activation_id),
    strategy_execution_binding_id text NOT NULL
        REFERENCES strategy_entry.strategy_execution_bindings(strategy_execution_binding_id),
    trading_account_id text NOT NULL
        REFERENCES identity.trading_accounts(trading_account_id),
    account_connector_id text NOT NULL
        REFERENCES identity.account_connectors(account_connector_id),
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    symbol text NOT NULL,
    release_commit text NOT NULL,
    state text NOT NULL CHECK (state IN ('ACTIVE','DISARMED','SUPERSEDED')),
    owner_approved_at timestamptz NOT NULL,
    activated_at timestamptz NOT NULL,
    deactivated_at timestamptz,
    owner_approval_source text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (btrim(account_live_arm_session_id) <> ''),
    CHECK (btrim(symbol) <> ''),
    CHECK (release_commit ~ '^[0-9a-f]{40}$'),
    CHECK (btrim(owner_approval_source) <> ''),
    CHECK (owner_approved_at <= activated_at),
    CHECK (
        (state = 'ACTIVE' AND deactivated_at IS NULL)
        OR (state <> 'ACTIVE' AND deactivated_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_account_live_arm_active_exact_scope
    ON control.account_live_arm_sessions (
        strategy_execution_binding_id,trading_account_id,
        account_connector_id,symbol
    )
    WHERE state='ACTIVE';

-- Readers only. Only separately authorized owner-control rail may create arms.
GRANT SELECT ON control.account_live_arm_sessions TO cripta;
COMMIT;
