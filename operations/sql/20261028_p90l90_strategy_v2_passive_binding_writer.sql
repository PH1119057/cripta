-- Versioned, least-privilege passive registration of two independent P90L90 v2
-- Strategy-scoped SECOND48 cards. This is a PostgreSQL migration applied
-- ONLY by approved postgres DB actor, after the legacy v1 binding migration.
-- No login/role inheritance to cripta; no account, LIVE or permission changes.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '20s';

DO $verify_prior$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles
                 WHERE rolname='cripta_p90l90_passive_binding'
                   AND rolcanlogin=false AND rolsuper=false) OR
     to_regprocedure(
       'strategy_entry.register_p90l90_inactive_binding(text,text,text)'
     ) IS NULL OR
     has_table_privilege(
       'cripta','strategy_entry.strategy_execution_bindings','INSERT'
     ) OR
     pg_has_role('cripta','cripta_p90l90_passive_binding','MEMBER')
  THEN
    RAISE EXCEPTION 'P90L90_V2_PRIOR_LEAST_PRIVILEGE_RAIL_MISSING';
  END IF;
END;
$verify_prior$;

-- Temporary only: the NOLOGIN owner needs CREATE to become owner of this
-- one function. Revoked BEFORE COMMIT. No broad UPDATE/INSERT to cripta.
GRANT SELECT ON control.account_live_arm_sessions
  TO cripta_p90l90_passive_binding;
GRANT CREATE ON SCHEMA strategy_entry TO cripta_p90l90_passive_binding;

CREATE FUNCTION strategy_entry.register_p90l90_v2_inactive_binding(
    p_binding_id text,
    p_activation_id text,
    p_trading_account_id text
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
DECLARE
  v_strategy_id text;
  v_expected_account text;
  v_expected_activation text;
  v_expected_binding text;
  v_expected_kind text;
  v_expected_connector text;
  v_expected_symbols jsonb;
  v_card jsonb;
  v_lifecycle jsonb;
  v_capital jsonb;
  v_entry jsonb;
BEGIN
  IF session_user <> 'cripta' THEN
    RAISE EXCEPTION 'P90L90_V2_WRONG_DB_SESSION_ACTOR';
  END IF;
  IF current_setting('transaction_isolation') <> 'serializable' THEN
    RAISE EXCEPTION 'P90L90_V2_SERIALIZABLE_REQUIRED';
  END IF;
  IF p_binding_id IS NULL OR p_activation_id IS NULL OR
     p_trading_account_id IS NULL THEN
    RAISE EXCEPTION 'P90L90_V2_NULL_BINDING_INPUT';
  END IF;
  SELECT a.strategy_id,c.card_json INTO v_strategy_id,v_card
    FROM strategy_entry.strategy_activations a
    JOIN strategy_entry.strategy_cards c
      ON c.strategy_id=a.strategy_id
     AND c.strategy_version=a.strategy_version
     AND c.strategy_config_fingerprint=a.strategy_config_fingerprint
   WHERE a.activation_id=p_activation_id
     AND a.enabled=false AND a.disabled_at IS NOT NULL
     AND a.strategy_version='1.1-p90l90-strategy-second48-20261028';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'P90L90_V2_ACTIVATION_VERSION_NOT_EXACT';
  END IF;
  IF v_strategy_id='p90l90_legacy_main_ltc_inj' THEN
    v_expected_account:='ta-2122c81ecff14f3b85b102db605212f7';
    v_expected_activation:='activation-p90l90-legacy-main-20261028-v2';
    v_expected_binding:='binding-p90l90-legacy-main-20261028-v2';
    v_expected_connector:='ac-ed73de20410548d6841aeafa5f196f70';
    v_expected_kind:='MAIN';
    v_expected_symbols:='["INJUSDT","LTCUSDT"]'::jsonb;
  ELSIF v_strategy_id='p90l90_legacy_sub_dot_arb_apt' THEN
    v_expected_account:='ta-853ac5fcd58185697396d33cd57459fa';
    v_expected_activation:='activation-p90l90-legacy-sub-20261028-v2';
    v_expected_binding:='binding-p90l90-legacy-sub-20261028-v2';
    v_expected_connector:='ac-ec57819f875f1bfd929a382d6dc8a125';
    v_expected_kind:='SUB';
    v_expected_symbols:='["APTUSDT","ARBUSDT","DOTUSDT"]'::jsonb;
  ELSE
    RAISE EXCEPTION 'P90L90_V2_STRATEGY_NOT_APPROVED';
  END IF;
  IF (p_binding_id,p_activation_id,p_trading_account_id) IS DISTINCT FROM
     (v_expected_binding,v_expected_activation,v_expected_account)
     OR v_card->'symbols' IS DISTINCT FROM v_expected_symbols
  THEN
    RAISE EXCEPTION 'P90L90_V2_ACCOUNT_AND_UNIVERSE_IDENTITY_MISMATCH';
  END IF;
  v_lifecycle := (v_card->'lifecycle_policy'->>'payload_json')::jsonb;
  v_capital := (v_card->'capital_policy'->>'payload_json')::jsonb;
  v_entry := (v_card->'entry_policy'->>'payload_json')::jsonb;
  IF v_lifecycle->'second_hardstop_embargo' IS DISTINCT FROM
     '{"enabled":true,"hardstop_count":2,"rolling_hours":48,'
     '"new_entry_pause_hours":48,"close_existing_positions":false,'
     '"domain":"STRATEGY_VERSION"}'::jsonb
     OR v_capital->'paper_simulation' IS DISTINCT FROM
     '{"enabled":true,"mode":"NO_ORDER_NATIVE_PUBLIC_TRADE_PROXY",'
     '"notional_usdt":"10","schema_leverage":1,'
     '"fees":"TAKER_ENTRY_RESEARCH_PROXY",'
     '"exchange_fill_confirmed":false}'::jsonb
     OR v_entry->'execution_policy'->>'order_type' <> 'NOT_APPROVED'
  THEN
    RAISE EXCEPTION 'P90L90_V2_NO_ORDER_SECOND48_POLICY_MISMATCH';
  END IF;

  -- All old v1 identities remain traceable, disabled and INACTIVE;
  -- a new binding MUST NOT replace or silently retire them.
  IF NOT EXISTS (
    SELECT 1 FROM strategy_entry.strategy_execution_bindings b
    JOIN strategy_entry.strategy_activations old
      ON old.activation_id=b.strategy_activation_id
    WHERE old.strategy_id=v_strategy_id
      AND old.strategy_version='1.0-p90l90-legacy-passive-20261027'
      AND old.enabled=false AND old.disabled_at IS NOT NULL
      AND b.trading_account_id=v_expected_account
      AND b.binding_state='INACTIVE'
  ) THEN
    RAISE EXCEPTION 'P90L90_V2_OLD_PASSIVE_BINDING_PROVENANCE_MISSING';
  END IF;
  IF EXISTS (SELECT 1 FROM control.execution_gates
             WHERE mode IN ('mainnet','testnet') AND enabled<>0)
     OR (SELECT count(*) FROM control.execution_gates
         WHERE mode IN ('mainnet','testnet')) <> 2
     OR EXISTS (SELECT 1 FROM strategy_entry.execution_permissions
                WHERE enabled=true)
     OR EXISTS (SELECT 1 FROM strategy_entry.strategy_activations
                WHERE enabled=true)
     OR EXISTS (SELECT 1 FROM control.live_arm_sessions
                WHERE state='ACTIVE')
     OR EXISTS (SELECT 1 FROM control.account_live_arm_sessions
                WHERE state='ACTIVE')
  THEN
    RAISE EXCEPTION 'P90L90_V2_REAL_CONTROLS_NOT_DISARMED';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM identity.trading_accounts a
    JOIN identity.account_connectors c
      ON c.trading_account_id=a.trading_account_id
    WHERE a.trading_account_id=v_expected_account
      AND a.account_kind=v_expected_kind
      AND a.account_type='UNIFIED'
      AND a.environment='MAINNET'
      AND a.status='ACTIVE'
      AND c.account_connector_id=v_expected_connector
      AND c.status='ACTIVE' AND c.retired_at IS NULL
      AND c.connector_kind='CEX_PRIVATE_RUNTIME'
      AND c.adapter_id='BYBIT_V5_PRIVATE'
  ) OR (SELECT count(*) FROM identity.account_connectors
        WHERE trading_account_id=v_expected_account
          AND status='ACTIVE' AND retired_at IS NULL
          AND connector_kind='CEX_PRIVATE_RUNTIME'
          AND adapter_id='BYBIT_V5_PRIVATE') <> 1
  THEN
    RAISE EXCEPTION 'P90L90_V2_ACCOUNT_CONNECTOR_NOT_PROVEN';
  END IF;

  INSERT INTO strategy_entry.strategy_execution_bindings (
      strategy_execution_binding_id,strategy_activation_id,
      execution_leg,execution_role,trading_account_id,
      allocation_mode,binding_state,effective_from,effective_to,
      operator,source,reason
  ) VALUES (
      p_binding_id,p_activation_id,'PRIMARY','PRIMARY',
      v_expected_account,'DEDICATED_STRATEGY','INACTIVE',
      transaction_timestamp(),NULL,'owner',
      'OWNER_APPROVED_P90L90_V2_SECOND48_ISSUE_243',
      'INACTIVE_NO_ORDER_SEPARATE_STRATEGY_VERSION_MAPPING'
  );
  RETURN p_binding_id;
END;
$function$;

ALTER FUNCTION strategy_entry.register_p90l90_v2_inactive_binding(text,text,text)
  OWNER TO cripta_p90l90_passive_binding;
REVOKE CREATE ON SCHEMA strategy_entry FROM cripta_p90l90_passive_binding;
REVOKE ALL ON FUNCTION
  strategy_entry.register_p90l90_v2_inactive_binding(text,text,text)
  FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
  strategy_entry.register_p90l90_v2_inactive_binding(text,text,text)
  TO cripta;

DO $verify$
BEGIN
  IF has_table_privilege('cripta',
      'strategy_entry.strategy_execution_bindings','INSERT') OR
     pg_has_role('cripta','cripta_p90l90_passive_binding','MEMBER') OR
     NOT has_function_privilege('cripta',
      'strategy_entry.register_p90l90_v2_inactive_binding(text,text,text)',
      'EXECUTE') OR
     has_schema_privilege('cripta_p90l90_passive_binding',
      'strategy_entry','CREATE')
  THEN
    RAISE EXCEPTION 'P90L90_V2_POST_MIGRATION_PRIVILEGE_INVARIANT';
  END IF;
END;
$verify$;
COMMIT;
