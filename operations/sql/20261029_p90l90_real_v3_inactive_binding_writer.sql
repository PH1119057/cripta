-- Issue #243: exact TWO-strategy P90L90 REAL v3 registration, INACTIVE only.
-- MIGRATION ACTOR: postgres (never Unix/DB cripta).
-- READ: strategy_entry Cards/Activations/Plans/Bindings/Permissions,
--       identity TradingAccounts/Connectors, control Gates/Arms.
-- WRITE: one new NOLOGIN least-privilege role/function; GRANT EXECUTE to
--        cripta. No StrategyCard/Activation/Plan/Binding rows in migration.
-- A later approved cripta registration script may INSERT only one exact
-- inactive dedicated binding per owner-approved v3 Strategy.
-- Does NOT activate either Strategy, execution permission, LIVE arm or
-- Bybit Exchange operation.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

DO $create_role$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname='cripta_p90l90_real_v3_binding'
  ) THEN
    CREATE ROLE cripta_p90l90_real_v3_binding NOLOGIN NOSUPERUSER
      NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles
     WHERE rolname='cripta_p90l90_real_v3_binding'
       AND rolcanlogin=false AND rolsuper=false
       AND rolcreatedb=false AND rolcreaterole=false
       AND rolinherit=false AND rolreplication=false
       AND rolbypassrls=false
  ) OR pg_has_role('cripta','cripta_p90l90_real_v3_binding','MEMBER')
     OR has_table_privilege(
       'cripta','strategy_entry.strategy_execution_bindings','INSERT'
     ) THEN
    RAISE EXCEPTION 'P90_REAL_V3_WRITER_ROLE_OR_LEGACY_GRANT_DRIFT';
  END IF;
  IF to_regprocedure(
     'strategy_entry.register_p90l90_real_v3_inactive_binding(text,text,text)'
  ) IS NOT NULL THEN
    IF NOT EXISTS (
      SELECT 1 FROM pg_proc f JOIN pg_namespace n ON n.oid=f.pronamespace
       WHERE n.nspname='strategy_entry'
         AND f.proname='register_p90l90_real_v3_inactive_binding'
         AND pg_get_userbyid(f.proowner)='cripta_p90l90_real_v3_binding'
         AND f.prosecdef IS TRUE
         AND f.prorettype='text'::regtype::oid
         AND md5(f.prosrc)='2c60c9e575126e72070ea09a88a13a6a'
         AND has_function_privilege(
           'cripta',f.oid,'EXECUTE'
         )
    ) THEN
      RAISE EXCEPTION 'P90_REAL_V3_EXISTING_WRITER_DRIFT';
    END IF;
  END IF;
END
$create_role$;

GRANT USAGE ON SCHEMA strategy_entry,identity,control
  TO cripta_p90l90_real_v3_binding;
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
  TO cripta_p90l90_real_v3_binding;
GRANT INSERT ON strategy_entry.strategy_execution_bindings
  TO cripta_p90l90_real_v3_binding;
-- Ownership transfer only within this atomic migration.
GRANT CREATE ON SCHEMA strategy_entry TO cripta_p90l90_real_v3_binding;

CREATE OR REPLACE FUNCTION strategy_entry.register_p90l90_real_v3_inactive_binding(
    p_binding_id text, p_activation_id text, p_account_id text
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
DECLARE
  v_strategy text;
  v_card jsonb;
  v_kind text;
  v_account text;
  v_connector text;
  v_activation text;
  v_binding text;
  v_symbols jsonb;
  v_capital jsonb;
  v_entry jsonb;
  v_protection jsonb;
  v_exit jsonb;
BEGIN
  IF session_user <> 'cripta'
     OR current_setting('transaction_isolation') <> 'serializable'
  THEN
    RAISE EXCEPTION 'P90_REAL_V3_WRONG_DB_ACTOR_OR_ISOLATION';
  END IF;
  IF p_binding_id IS NULL OR p_activation_id IS NULL OR p_account_id IS NULL
  THEN
    RAISE EXCEPTION 'P90_REAL_V3_NULL_BINDING_ARGUMENT';
  END IF;
  SELECT a.strategy_id,c.card_json
    INTO v_strategy,v_card
    FROM strategy_entry.strategy_activations a
    JOIN strategy_entry.strategy_cards c
      ON (c.strategy_id,c.strategy_version,c.strategy_config_fingerprint)
       = (a.strategy_id,a.strategy_version,a.strategy_config_fingerprint)
   WHERE a.activation_id=p_activation_id
     AND a.strategy_version='2.0-p90l90-real-main-sub-20261028'
     AND a.enabled=false AND a.disabled_at IS NOT NULL;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'P90_REAL_V3_INACTIVE_IMMUTABLE_CARD_REQUIRED';
  END IF;

  IF v_strategy='p90l90_legacy_main_ltc_inj' THEN
    v_kind:='MAIN';
    v_account:='ta-2122c81ecff14f3b85b102db605212f7';
    v_connector:='ac-ed73de20410548d6841aeafa5f196f70';
    v_activation:='activation-p90l90-real-main-20261029-v3';
    v_binding:='binding-p90l90-real-main-20261029-v3';
    v_symbols:='["INJUSDT","LTCUSDT"]'::jsonb;
  ELSIF v_strategy='p90l90_legacy_sub_dot_arb_apt' THEN
    v_kind:='SUB';
    v_account:='ta-853ac5fcd58185697396d33cd57459fa';
    v_connector:='ac-ec57819f875f1bfd929a382d6dc8a125';
    v_activation:='activation-p90l90-real-sub-20261029-v3';
    v_binding:='binding-p90l90-real-sub-20261029-v3';
    v_symbols:='["APTUSDT","ARBUSDT","DOTUSDT"]'::jsonb;
  ELSE
    RAISE EXCEPTION 'P90_REAL_V3_UNAPPROVED_STRATEGY';
  END IF;
  IF (p_binding_id,p_activation_id,p_account_id)
      IS DISTINCT FROM (v_binding,v_activation,v_account)
     OR v_card->'symbols' IS DISTINCT FROM v_symbols
  THEN
    RAISE EXCEPTION 'P90_REAL_V3_ACCOUNT_CARD_SCOPE_DRIFT';
  END IF;
  v_capital := (v_card->'capital_policy'->>'payload_json')::jsonb;
  v_entry := (v_card->'entry_policy'->>'payload_json')::jsonb;
  v_protection := (v_card->'protection_policy'->>'payload_json')::jsonb;
  v_exit := (v_card->'exit_policy'->>'payload_json')::jsonb;
  IF v_capital->>'requested_amount' IS DISTINCT FROM '10'
     OR v_capital->>'leverage' IS DISTINCT FROM '1'
     OR v_capital->>'margin_mode' IS DISTINCT FROM 'ISOLATED'
     OR v_capital->>'max_open_positions_per_symbol' IS DISTINCT FROM '1'
     OR v_capital->>'max_simultaneous_committed_usdt'
          IS DISTINCT FROM (
            CASE WHEN v_kind='MAIN' THEN '20' ELSE '30' END
          )
     OR v_entry->'execution_policy'->>'order_type' IS DISTINCT FROM 'MARKET'
     OR v_protection->'initial_protection'->>'stop_loss_pct' IS DISTINCT FROM '10'
     OR v_protection->'initial_protection'->>'take_profit_enabled' IS DISTINCT FROM 'true'
     OR v_exit->'execution_policy'->>'max_request_age_seconds' IS DISTINCT FROM '5'
     OR v_exit->'trailing'->>'activation_profit_pct' IS DISTINCT FROM '0.5'
     OR v_exit->'trailing'->>'distance_pct' IS DISTINCT FROM '0.35'
  THEN
    RAISE EXCEPTION 'P90_REAL_V3_OWNER_POLICY_NOT_EXACT';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM strategy_entry.entry_plans p
     WHERE p.strategy_id=v_strategy
       AND p.strategy_version='2.0-p90l90-real-main-sub-20261028'
       AND p.entry_plan_fingerprint IS NOT NULL
  ) OR NOT EXISTS (
    SELECT 1 FROM strategy_entry.exit_plans p
     WHERE p.strategy_id=v_strategy
       AND p.strategy_version='2.0-p90l90-real-main-sub-20261028'
       AND p.exit_plan_fingerprint IS NOT NULL
  ) OR EXISTS (
    SELECT 1 FROM strategy_entry.strategy_execution_bindings b
     WHERE b.strategy_activation_id=v_activation
  ) THEN
    RAISE EXCEPTION 'P90_REAL_V3_PLAN_OR_BINDING_STATE_NOT_EXACT';
  END IF;

  IF EXISTS (SELECT 1 FROM control.execution_gates
              WHERE mode IN ('mainnet','testnet') AND enabled<>0)
     OR (SELECT count(*) FROM control.execution_gates
          WHERE mode IN ('mainnet','testnet'))<>2
     OR EXISTS (SELECT 1 FROM strategy_entry.execution_permissions
                 WHERE enabled=true)
     OR EXISTS (SELECT 1 FROM control.live_arm_sessions
                 WHERE state='ACTIVE')
     OR EXISTS (SELECT 1 FROM control.account_live_arm_sessions
                 WHERE state='ACTIVE')
     OR EXISTS (SELECT 1 FROM strategy_entry.strategy_activations
                 WHERE enabled=true)
  THEN
    RAISE EXCEPTION 'P90_REAL_V3_REGISTRATION_REQUIRES_DISARMED_STATE';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM identity.trading_accounts a
    JOIN identity.account_connectors c
      ON c.trading_account_id=a.trading_account_id
   WHERE a.trading_account_id=v_account
     AND a.account_kind=v_kind AND a.account_type='UNIFIED'
     AND a.environment='MAINNET' AND a.status='ACTIVE'
     AND a.venue_id='venue-bybit'
     AND a.external_account_ref ~ '^[0-9]+$'
     AND a.external_account_ref ~ '[1-9]'
     AND c.account_connector_id=v_connector
     AND c.status='ACTIVE' AND c.retired_at IS NULL
     AND c.adapter_id='BYBIT_V5_PRIVATE'
     AND c.connector_kind='CEX_PRIVATE_RUNTIME'
     AND nullif(btrim(c.credential_ref),'') IS NOT NULL
  ) OR (
    SELECT count(*) FROM identity.account_connectors
     WHERE trading_account_id=v_account
       AND status='ACTIVE' AND retired_at IS NULL
  )<>1 THEN
    RAISE EXCEPTION 'P90_REAL_V3_INDEPENDENT_ACCOUNT_NOT_PROVEN';
  END IF;

  INSERT INTO strategy_entry.strategy_execution_bindings(
    strategy_execution_binding_id,strategy_activation_id,
    execution_leg,execution_role,trading_account_id,
    allocation_mode,binding_state,effective_from,effective_to,
    operator,source,reason
  ) VALUES (
    v_binding,v_activation,'PRIMARY','PRIMARY',v_account,
    'DEDICATED_STRATEGY','INACTIVE',transaction_timestamp(),NULL,
    'owner','OWNER_P90L90_REAL_V3_ISSUE_243',
    'INACTIVE_STRATEGY_ONLY_NO_EXCHANGE_AND_NO_LIVE_ARM'
  );
  RETURN v_binding;
END;
$function$;

ALTER FUNCTION strategy_entry.register_p90l90_real_v3_inactive_binding(
  text,text,text
) OWNER TO cripta_p90l90_real_v3_binding;
REVOKE CREATE ON SCHEMA strategy_entry FROM cripta_p90l90_real_v3_binding;
REVOKE ALL ON FUNCTION
  strategy_entry.register_p90l90_real_v3_inactive_binding(text,text,text)
  FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
  strategy_entry.register_p90l90_real_v3_inactive_binding(text,text,text)
  TO cripta;

DO $postcheck$
BEGIN
  IF has_table_privilege(
       'cripta','strategy_entry.strategy_execution_bindings','INSERT'
     ) OR pg_has_role(
       'cripta','cripta_p90l90_real_v3_binding','MEMBER'
     ) OR has_schema_privilege(
       'cripta_p90l90_real_v3_binding','strategy_entry','CREATE'
     ) OR NOT has_function_privilege(
       'cripta',
       'strategy_entry.register_p90l90_real_v3_inactive_binding(text,text,text)',
       'EXECUTE'
     ) THEN
    RAISE EXCEPTION 'P90_REAL_V3_WRITER_POST_MIGRATION_PRIVILEGE_DRIFT';
  END IF;
END
$postcheck$;
COMMIT;
