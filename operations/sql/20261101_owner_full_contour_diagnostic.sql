-- Owner decision 2026-11-01: durable full-contour REAL diagnostic control.
-- This migration creates no Strategy row, execution permission, LIVE arm or
-- Exchange mutation.  Registration and owner trigger remain separate actions.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

CREATE TABLE IF NOT EXISTS control.owner_full_contour_diagnostic_runs(
    run_id TEXT PRIMARY KEY,
    requested_at TIMESTAMPTZ NOT NULL,
    requested_by TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source='dashboard:owner-full-contour-diagnostic'),
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('LONG','SHORT')),
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    strategy_config_fingerprint TEXT NOT NULL,
    strategy_activation_id TEXT NOT NULL,
    strategy_execution_binding_id TEXT NOT NULL,
    trading_account_id TEXT NOT NULL,
    account_connector_id TEXT NOT NULL,
    release_commit TEXT NOT NULL CHECK(release_commit ~ '^[0-9a-f]{40}$'),
    state TEXT NOT NULL CHECK(state IN (
        'REQUESTED','MATERIALIZED','DISPATCHED','POSITION_OPEN',
        'CLOSED','FAILED','RECONCILIATION_REQUIRED'
    )),
    signal_id TEXT,
    strategy_attempt_id TEXT,
    entry_decision_id TEXT,
    entry_execution_request_id TEXT,
    entry_command_id TEXT,
    strategy_position_id TEXT,
    error TEXT,
    completed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (
      (state IN ('CLOSED','FAILED') AND completed_at IS NOT NULL)
      OR (state NOT IN ('CLOSED','FAILED') AND completed_at IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS owner_full_contour_one_active_run
ON control.owner_full_contour_diagnostic_runs((1))
WHERE state IN (
  'REQUESTED','MATERIALIZED','DISPATCHED','POSITION_OPEN',
  'RECONCILIATION_REQUIRED'
);

CREATE INDEX IF NOT EXISTS owner_full_contour_run_account_time
ON control.owner_full_contour_diagnostic_runs(
  trading_account_id,requested_at DESC,run_id
);

REVOKE ALL ON control.owner_full_contour_diagnostic_runs FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON control.owner_full_contour_diagnostic_runs TO cripta;

DO $role$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles
     WHERE rolname='cripta_owner_full_contour_binding'
  ) THEN
    CREATE ROLE cripta_owner_full_contour_binding NOLOGIN NOSUPERUSER
      NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
  END IF;
END
$role$;

GRANT USAGE ON SCHEMA strategy_entry,identity,control
  TO cripta_owner_full_contour_binding;
GRANT SELECT ON
  strategy_entry.strategy_cards,
  strategy_entry.strategy_activations,
  strategy_entry.entry_plans,
  strategy_entry.exit_plans,
  strategy_entry.strategy_execution_bindings,
  strategy_entry.execution_permissions,
  identity.trading_accounts,
  identity.account_connectors,
  control.execution_gates,
  control.live_arm_sessions,
  control.account_live_arm_sessions
  TO cripta_owner_full_contour_binding;
GRANT INSERT ON strategy_entry.strategy_execution_bindings
  TO cripta_owner_full_contour_binding;
GRANT CREATE ON SCHEMA strategy_entry TO cripta_owner_full_contour_binding;

CREATE OR REPLACE FUNCTION strategy_entry.register_owner_full_contour_binding(
    p_binding_id text, p_activation_id text, p_account_id text
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
DECLARE
  v_strategy text;
  v_version text;
  v_card jsonb;
  v_account text;
  v_connector text;
  v_activation text;
  v_binding text;
  v_symbols jsonb;
BEGIN
  IF session_user <> 'cripta'
     OR current_setting('transaction_isolation') <> 'serializable'
  THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_WRONG_DB_ACTOR_OR_ISOLATION';
  END IF;

  SELECT a.strategy_id,a.strategy_version,c.card_json
    INTO v_strategy,v_version,v_card
    FROM strategy_entry.strategy_activations a
    JOIN strategy_entry.strategy_cards c
      ON (c.strategy_id,c.strategy_version,c.strategy_config_fingerprint)
       = (a.strategy_id,a.strategy_version,a.strategy_config_fingerprint)
   WHERE a.activation_id=p_activation_id
     AND a.strategy_version='1.0-owner-full-contour-20261101'
     AND a.enabled=true AND a.disabled_at IS NULL;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_ENABLED_IMMUTABLE_CARD_REQUIRED';
  END IF;

  IF v_strategy='owner_full_contour_diag_main' THEN
    v_account:='ta-2122c81ecff14f3b85b102db605212f7';
    v_connector:='ac-ed73de20410548d6841aeafa5f196f70';
    v_activation:='activation-owner-full-contour-main-20261101';
    v_binding:='binding-owner-full-contour-main-20261101';
    v_symbols:='["INJUSDT","LTCUSDT"]'::jsonb;
  ELSIF v_strategy='owner_full_contour_diag_sub' THEN
    v_account:='ta-853ac5fcd58185697396d33cd57459fa';
    v_connector:='ac-ec57819f875f1bfd929a382d6dc8a125';
    v_activation:='activation-owner-full-contour-sub-20261101';
    v_binding:='binding-owner-full-contour-sub-20261101';
    v_symbols:='["APTUSDT","ARBUSDT","DOTUSDT"]'::jsonb;
  ELSE
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_STRATEGY_NOT_APPROVED';
  END IF;

  IF (p_binding_id,p_activation_id,p_account_id)
      IS DISTINCT FROM (v_binding,v_activation,v_account)
     OR v_card->'symbols' IS DISTINCT FROM v_symbols
  THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_ACCOUNT_SCOPE_DRIFT';
  END IF;

  IF (v_card->'scope'->>'payload_json')::jsonb->>'kind'
        IS DISTINCT FROM 'OWNER_FULL_CONTOUR_DIAGNOSTIC'
     OR (v_card->'capital_policy'->>'payload_json')::jsonb->>'requested_amount'
        IS DISTINCT FROM '10'
     OR (v_card->'capital_policy'->>'payload_json')::jsonb->>'leverage'
        IS DISTINCT FROM '1'
     OR (v_card->'entry_policy'->>'payload_json')::jsonb
          ->'execution_policy'->>'order_type' IS DISTINCT FROM 'MARKET'
     OR (v_card->'protection_policy'->>'payload_json')::jsonb
          ->'initial_protection'->>'stop_loss_pct' IS DISTINCT FROM '0.5'
  THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_POLICY_NOT_EXACT';
  END IF;

  IF EXISTS (
       SELECT 1 FROM control.execution_gates
        WHERE mode IN ('mainnet','testnet') AND enabled<>0
     )
     OR EXISTS (
       SELECT 1 FROM strategy_entry.execution_permissions WHERE enabled=true
     )
     OR EXISTS (
       SELECT 1 FROM control.live_arm_sessions WHERE state='ACTIVE'
     )
     OR EXISTS (
       SELECT 1 FROM control.account_live_arm_sessions WHERE state='ACTIVE'
     )
  THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_REGISTRATION_REQUIRES_DISARMED_STATE';
  END IF;

  IF NOT EXISTS (
    SELECT 1
      FROM identity.trading_accounts a
      JOIN identity.account_connectors c
        ON c.trading_account_id=a.trading_account_id
     WHERE a.trading_account_id=v_account
       AND a.environment='MAINNET'
       AND a.status='ACTIVE'
       AND a.account_type='UNIFIED'
       AND a.venue_id='venue-bybit'
       AND c.account_connector_id=v_connector
       AND c.status='ACTIVE'
       AND c.retired_at IS NULL
       AND c.adapter_id='BYBIT_V5_PRIVATE'
       AND c.connector_kind='CEX_PRIVATE_RUNTIME'
       AND nullif(btrim(c.credential_ref),'') IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_ACCOUNT_NOT_PROVEN';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM strategy_entry.entry_plans p
     WHERE p.strategy_id=v_strategy AND p.strategy_version=v_version
  ) OR NOT EXISTS (
    SELECT 1 FROM strategy_entry.exit_plans p
     WHERE p.strategy_id=v_strategy AND p.strategy_version=v_version
  ) THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_PLAN_MISSING';
  END IF;

  IF EXISTS (
    SELECT 1 FROM strategy_entry.strategy_execution_bindings
     WHERE strategy_activation_id=v_activation
  ) THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_BINDING_ALREADY_EXISTS';
  END IF;

  INSERT INTO strategy_entry.strategy_execution_bindings(
    strategy_execution_binding_id,strategy_activation_id,
    execution_leg,execution_role,trading_account_id,
    allocation_mode,binding_state,effective_from,effective_to,
    operator,source,reason
  ) VALUES (
    v_binding,v_activation,'PRIMARY','PRIMARY',v_account,
    'DEDICATED_STRATEGY','ACTIVE',transaction_timestamp(),NULL,
    'owner','OWNER_FULL_CONTOUR_DIAGNOSTIC_20261101',
    'DIAGNOSTIC_CAPABILITY_ONLY_EXECUTION_PERMISSION_OFF'
  );
  RETURN v_binding;
END;
$function$;

ALTER FUNCTION strategy_entry.register_owner_full_contour_binding(text,text,text)
  OWNER TO cripta_owner_full_contour_binding;
REVOKE CREATE ON SCHEMA strategy_entry FROM cripta_owner_full_contour_binding;
REVOKE ALL ON FUNCTION
  strategy_entry.register_owner_full_contour_binding(text,text,text)
  FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
  strategy_entry.register_owner_full_contour_binding(text,text,text)
  TO cripta;

COMMIT;
