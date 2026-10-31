-- Issue #243: narrow P90 REAL v3 PREARM + account OWNER ARM control rail.
-- MIGRATION ACTOR: postgres. Runtime role cripta receives EXECUTE only.
-- No function below sends Exchange requests. PREARM has trading_effect=NONE:
-- it atomically enables the two exact immutable v3 StrategyActivations and
-- their two pre-registered dedicated MAIN/SUB bindings while mainnet is OFF.
-- Account-arm rows may be created only after exact release-bound Strategy/
-- symbol owner-arm evidence/session already exists. Global mainnet gate is
-- NEVER opened by SQL helpers; Python owner control opens it LAST.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';

DO $role$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname='cripta_p90l90_real_v3_control'
  ) THEN
    CREATE ROLE cripta_p90l90_real_v3_control NOLOGIN NOSUPERUSER
      NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles
     WHERE rolname='cripta_p90l90_real_v3_control'
       AND rolcanlogin=false AND rolsuper=false
       AND rolcreatedb=false AND rolcreaterole=false
       AND rolinherit=false AND rolreplication=false
       AND rolbypassrls=false
  ) OR pg_has_role('cripta','cripta_p90l90_real_v3_control','MEMBER')
  THEN
    RAISE EXCEPTION 'P90_REAL_CONTROL_ROLE_DRIFT';
  END IF;
END
$role$;

GRANT USAGE ON SCHEMA strategy_entry,identity,control,runtime
  TO cripta_p90l90_real_v3_control;
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
  control.account_live_arm_sessions,
  runtime.position_ownership
  TO cripta_p90l90_real_v3_control;
GRANT UPDATE ON strategy_entry.strategy_activations,
  strategy_entry.strategy_execution_bindings
  TO cripta_p90l90_real_v3_control;
GRANT INSERT,UPDATE ON control.account_live_arm_sessions
  TO cripta_p90l90_real_v3_control;
GRANT CREATE ON SCHEMA strategy_entry,control
  TO cripta_p90l90_real_v3_control;

CREATE OR REPLACE FUNCTION strategy_entry.prearm_p90l90_real_v3(
    p_at timestamptz,
    p_operator text,
    p_source text
) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
DECLARE
  v_count integer;
BEGIN
  IF session_user <> 'cripta'
     OR current_setting('transaction_isolation') <> 'serializable'
     OR p_at IS NULL
     OR nullif(btrim(p_operator),'') IS NULL
     OR nullif(btrim(p_source),'') IS NULL
  THEN
    RAISE EXCEPTION 'P90_REAL_PREARM_CALLER_OR_ARGUMENT_INVALID';
  END IF;
  IF EXISTS (
      SELECT 1 FROM control.execution_gates
       WHERE mode IN ('mainnet','testnet') AND enabled<>0
     )
     OR (SELECT count(*) FROM control.execution_gates
          WHERE mode IN ('mainnet','testnet'))<>2
     OR EXISTS (
      SELECT 1 FROM strategy_entry.execution_permissions WHERE enabled=true
     )
     OR EXISTS (
      SELECT 1 FROM control.live_arm_sessions WHERE state='ACTIVE'
     )
     OR EXISTS (
      SELECT 1 FROM control.account_live_arm_sessions WHERE state='ACTIVE'
     )
     OR EXISTS (
      SELECT 1 FROM runtime.position_ownership
       WHERE state IN ('OPEN','RECONCILIATION_REQUIRED')
     )
  THEN
    RAISE EXCEPTION 'P90_REAL_PREARM_REQUIRES_FULLY_DISARMED_FLAT_STATE';
  END IF;

  PERFORM 1
    FROM strategy_entry.strategy_activations a
    JOIN strategy_entry.strategy_execution_bindings b
      ON b.strategy_activation_id=a.activation_id
   WHERE (
     (a.strategy_id='p90l90_legacy_main_ltc_inj'
      AND a.activation_id='activation-p90l90-real-main-20261029-v3'
      AND b.strategy_execution_binding_id='binding-p90l90-real-main-20261029-v3'
      AND b.trading_account_id='ta-2122c81ecff14f3b85b102db605212f7')
     OR
     (a.strategy_id='p90l90_legacy_sub_dot_arb_apt'
      AND a.activation_id='activation-p90l90-real-sub-20261029-v3'
      AND b.strategy_execution_binding_id='binding-p90l90-real-sub-20261029-v3'
      AND b.trading_account_id='ta-853ac5fcd58185697396d33cd57459fa')
   )
     AND a.strategy_version='2.0-p90l90-real-main-sub-20261028'
     AND a.enabled=false AND a.disabled_at IS NOT NULL
     AND b.execution_leg='PRIMARY' AND b.execution_role='PRIMARY'
     AND b.allocation_mode='DEDICATED_STRATEGY'
     AND b.binding_state='INACTIVE' AND b.effective_to IS NULL
   FOR UPDATE OF a,b;
  IF NOT FOUND OR (
      SELECT count(*)
        FROM strategy_entry.strategy_activations a
        JOIN strategy_entry.strategy_execution_bindings b
          ON b.strategy_activation_id=a.activation_id
       WHERE a.strategy_version='2.0-p90l90-real-main-sub-20261028'
         AND a.strategy_id IN (
           'p90l90_legacy_main_ltc_inj',
           'p90l90_legacy_sub_dot_arb_apt'
         )
         AND a.enabled=false
         AND b.binding_state='INACTIVE'
     ) <> 2
  THEN
    RAISE EXCEPTION 'P90_REAL_PREARM_EXACT_PAIR_NOT_INACTIVE';
  END IF;

  -- R1 was canonically RETIRED on 2026-10-26.  Its historical unified
  -- MAIN binding can still be ACTIVE even though the exact StrategyActivation
  -- is disabled.  DEDICATED_STRATEGY correctly forbids P90 MAIN from becoming
  -- ACTIVE until that stale execution-placement ownership is retired.
  --
  -- This is an explicit one-way replacement:
  -- BEFORE: retired disabled r1_five_symbols -> MAIN historical binding ACTIVE
  -- AFTER:  retired R1 binding INACTIVE/effective_to set, then exact P90 MAIN
  --         and SUB bindings become ACTIVE for SOURCE_ONLY PREARM.
  -- It never re-enables R1, never restores R1 on unprearm, never opens mainnet
  -- and is allowed only in the fully disarmed/flat state checked above.
  PERFORM 1
    FROM strategy_entry.strategy_activations a
    JOIN strategy_entry.strategy_execution_bindings b
      ON b.strategy_activation_id=a.activation_id
   WHERE a.activation_id='activation-be482d99252b4055b020a84fef8e2054'
     AND a.strategy_id='r1_five_symbols'
     AND a.strategy_version='1.0-five-symbols-20261021'
     AND a.enabled=false
     AND a.disabled_at IS NOT NULL
     AND b.strategy_execution_binding_id='binding-r1-unified-2c51489dae0a38fc'
     AND b.trading_account_id='ta-2122c81ecff14f3b85b102db605212f7'
     AND b.execution_leg='PRIMARY'
     AND b.execution_role='PRIMARY'
     AND b.allocation_mode='DEDICATED_STRATEGY'
     AND (
       (b.binding_state='ACTIVE' AND b.effective_to IS NULL)
       OR
       (b.binding_state='INACTIVE' AND b.effective_to IS NOT NULL)
     )
   FOR UPDATE OF a,b;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'P90_REAL_PREARM_RETIRED_R1_BINDING_NOT_EXACT';
  END IF;

  IF EXISTS (
      SELECT 1
        FROM strategy_entry.strategy_execution_bindings b
       WHERE b.binding_state='ACTIVE'
         AND b.allocation_mode='DEDICATED_STRATEGY'
         AND b.trading_account_id IN (
           'ta-2122c81ecff14f3b85b102db605212f7',
           'ta-853ac5fcd58185697396d33cd57459fa'
         )
         AND b.strategy_execution_binding_id
             <> 'binding-r1-unified-2c51489dae0a38fc'
     )
  THEN
    RAISE EXCEPTION 'P90_REAL_PREARM_UNEXPECTED_ACTIVE_ACCOUNT_OWNER';
  END IF;

  UPDATE strategy_entry.strategy_execution_bindings
     SET binding_state='INACTIVE',
         effective_to=p_at,
         operator=p_operator,
         source=p_source,
         reason='R1 RETIRED; replaced by P90 REAL v3 SOURCE_ONLY PREARM',
         updated_at=clock_timestamp()
   WHERE strategy_execution_binding_id='binding-r1-unified-2c51489dae0a38fc'
     AND strategy_activation_id='activation-be482d99252b4055b020a84fef8e2054'
     AND trading_account_id='ta-2122c81ecff14f3b85b102db605212f7'
     AND binding_state='ACTIVE'
     AND effective_to IS NULL;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  IF v_count NOT IN (0,1) THEN
    RAISE EXCEPTION 'P90_REAL_PREARM_RETIRED_R1_BINDING_COUNT:%',v_count;
  END IF;
  IF v_count=0 AND NOT EXISTS (
      SELECT 1
        FROM strategy_entry.strategy_execution_bindings
       WHERE strategy_execution_binding_id='binding-r1-unified-2c51489dae0a38fc'
         AND strategy_activation_id='activation-be482d99252b4055b020a84fef8e2054'
         AND trading_account_id='ta-2122c81ecff14f3b85b102db605212f7'
         AND binding_state='INACTIVE'
         AND effective_to IS NOT NULL
     )
  THEN
    RAISE EXCEPTION 'P90_REAL_PREARM_RETIRED_R1_BINDING_NOT_CLOSED';
  END IF;

  UPDATE strategy_entry.strategy_activations
     SET enabled=true,enabled_at=p_at,disabled_at=NULL,
         operator=p_operator,source=p_source,
         change_reason='P90 REAL v3 PREARM SOURCE_ONLY'
   WHERE strategy_version='2.0-p90l90-real-main-sub-20261028'
     AND strategy_id IN (
       'p90l90_legacy_main_ltc_inj',
       'p90l90_legacy_sub_dot_arb_apt'
     )
     AND enabled=false;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  IF v_count<>2 THEN
    RAISE EXCEPTION 'P90_REAL_PREARM_ACTIVATION_COUNT:%',v_count;
  END IF;

  UPDATE strategy_entry.strategy_execution_bindings b
     SET binding_state='ACTIVE',operator=p_operator,source=p_source,
         reason='P90 REAL v3 PREARM SOURCE_ONLY',
         updated_at=clock_timestamp()
   WHERE b.strategy_activation_id IN (
      'activation-p90l90-real-main-20261029-v3',
      'activation-p90l90-real-sub-20261029-v3'
   )
     AND b.binding_state='INACTIVE';
  GET DIAGNOSTICS v_count = ROW_COUNT;
  IF v_count<>2 THEN
    RAISE EXCEPTION 'P90_REAL_PREARM_BINDING_COUNT:%',v_count;
  END IF;
  RETURN 2;
END;
$function$;

CREATE OR REPLACE FUNCTION strategy_entry.unprearm_p90l90_real_v3(
    p_at timestamptz,
    p_operator text,
    p_source text
) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
DECLARE
  v_count integer;
BEGIN
  IF session_user <> 'cripta'
     OR current_setting('transaction_isolation') <> 'serializable'
     OR p_at IS NULL
     OR nullif(btrim(p_operator),'') IS NULL
     OR nullif(btrim(p_source),'') IS NULL
  THEN
    RAISE EXCEPTION 'P90_REAL_UNPREARM_CALLER_OR_ARGUMENT_INVALID';
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
     OR EXISTS (
      SELECT 1 FROM runtime.position_ownership
       WHERE state IN ('OPEN','RECONCILIATION_REQUIRED')
     )
  THEN
    RAISE EXCEPTION 'P90_REAL_UNPREARM_REQUIRES_DISARMED_FLAT_STATE';
  END IF;

  UPDATE strategy_entry.strategy_execution_bindings
     SET binding_state='INACTIVE',operator=p_operator,source=p_source,
         reason='P90 REAL v3 PREARM stopped by owner',
         updated_at=clock_timestamp()
   WHERE strategy_activation_id IN (
      'activation-p90l90-real-main-20261029-v3',
      'activation-p90l90-real-sub-20261029-v3'
   )
     AND binding_state='ACTIVE';
  GET DIAGNOSTICS v_count = ROW_COUNT;
  IF v_count<>2 THEN
    RAISE EXCEPTION 'P90_REAL_UNPREARM_BINDING_COUNT:%',v_count;
  END IF;

  UPDATE strategy_entry.strategy_activations
     SET enabled=false,disabled_at=p_at,
         operator=p_operator,source=p_source,
         change_reason='P90 REAL v3 PREARM stopped by owner'
   WHERE strategy_version='2.0-p90l90-real-main-sub-20261028'
     AND strategy_id IN (
       'p90l90_legacy_main_ltc_inj',
       'p90l90_legacy_sub_dot_arb_apt'
     )
     AND enabled=true;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  IF v_count<>2 THEN
    RAISE EXCEPTION 'P90_REAL_UNPREARM_ACTIVATION_COUNT:%',v_count;
  END IF;
  RETURN 2;
END;
$function$;

CREATE OR REPLACE FUNCTION control.arm_p90l90_real_v3_account_scope(
    p_account_live_arm_session_id text,
    p_live_arm_session_id text,
    p_strategy_execution_binding_id text,
    p_trading_account_id text,
    p_account_connector_id text,
    p_symbol text,
    p_release_commit text,
    p_owner_approved_at timestamptz,
    p_owner_approval_source text
) RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
DECLARE
    v_activation_id text;
    v_strategy_id text;
    v_strategy_version text;
    v_fingerprint text;
    v_account_kind text;
BEGIN
    IF session_user <> 'cripta'
       OR current_setting('transaction_isolation') <> 'serializable'
       OR p_account_live_arm_session_id IS NULL
       OR btrim(p_account_live_arm_session_id)=''
       OR p_live_arm_session_id IS NULL
       OR btrim(p_live_arm_session_id)=''
       OR p_release_commit !~ '^[0-9a-f]{40}$'
       OR p_owner_approved_at IS NULL
       OR nullif(btrim(p_owner_approval_source),'') IS NULL
    THEN
        RAISE EXCEPTION 'P90_ACCOUNT_ARM_CALLER_OR_ARGUMENT_INVALID';
    END IF;

    SELECT b.strategy_activation_id,a.strategy_id,a.strategy_version,
           a.strategy_config_fingerprint,ta.account_kind
      INTO v_activation_id,v_strategy_id,v_strategy_version,
           v_fingerprint,v_account_kind
      FROM strategy_entry.strategy_execution_bindings b
      JOIN strategy_entry.strategy_activations a
        ON a.activation_id=b.strategy_activation_id
      JOIN identity.trading_accounts ta
        ON ta.trading_account_id=b.trading_account_id
     WHERE b.strategy_execution_binding_id=p_strategy_execution_binding_id
       AND b.trading_account_id=p_trading_account_id
       AND b.binding_state='ACTIVE'
       AND b.execution_leg='PRIMARY'
       AND b.execution_role='PRIMARY'
       AND b.allocation_mode='DEDICATED_STRATEGY'
       AND b.effective_from<=p_owner_approved_at
       AND (b.effective_to IS NULL OR b.effective_to>p_owner_approved_at)
       AND a.enabled=true
       AND a.enabled_at<=p_owner_approved_at
       AND a.strategy_version='2.0-p90l90-real-main-sub-20261028'
       AND a.strategy_id IN (
          'p90l90_legacy_main_ltc_inj',
          'p90l90_legacy_sub_dot_arb_apt'
       )
       AND ta.status='ACTIVE' AND ta.environment='MAINNET'
       AND ta.venue_id='venue-bybit' AND ta.account_type='UNIFIED'
     -- Lock only the mutable Strategy rows owned by this narrow writer.
     -- Account identity/connector and LIVE-session rows are checked in the
     -- same SERIALIZABLE transaction without acquiring UPDATE privileges
     -- on independently owned identity/control tables.
     FOR UPDATE OF b,a;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'P90_ACCOUNT_ARM_BINDING_OR_ACTIVATION_NOT_EXACT';
    END IF;
    IF (
        v_strategy_id='p90l90_legacy_main_ltc_inj'
        AND (v_account_kind<>'MAIN' OR p_symbol NOT IN ('LTCUSDT','INJUSDT'))
    ) OR (
        v_strategy_id='p90l90_legacy_sub_dot_arb_apt'
        AND (v_account_kind<>'SUB'
             OR p_symbol NOT IN ('APTUSDT','ARBUSDT','DOTUSDT'))
    ) THEN
        RAISE EXCEPTION 'P90_ACCOUNT_ARM_ACCOUNT_KIND_OR_SYMBOL_MISMATCH';
    END IF;

    PERFORM 1
      FROM identity.account_connectors c
     WHERE c.account_connector_id=p_account_connector_id
       AND c.trading_account_id=p_trading_account_id
       AND c.connector_kind='CEX_PRIVATE_RUNTIME'
       AND c.adapter_id='BYBIT_V5_PRIVATE'
       AND c.status='ACTIVE' AND c.retired_at IS NULL
       AND nullif(btrim(c.credential_ref),'') IS NOT NULL
     ;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'P90_ACCOUNT_ARM_CONNECTOR_NOT_EXACT';
    END IF;

    PERFORM 1
      FROM control.live_arm_sessions s
     WHERE s.live_arm_session_id=p_live_arm_session_id
       AND s.strategy_id=v_strategy_id
       AND s.strategy_version=v_strategy_version
       AND s.strategy_config_fingerprint=v_fingerprint
       AND s.strategy_activation_id=v_activation_id
       AND s.symbol=p_symbol
       AND s.release_commit=p_release_commit
       AND s.state='ACTIVE'
       AND s.owner_approved_at<=p_owner_approved_at
       AND s.deactivated_at IS NULL
     ;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'P90_ACCOUNT_ARM_STRATEGY_SYMBOL_OWNER_ARM_MISSING';
    END IF;

    IF EXISTS (
      SELECT 1 FROM control.account_live_arm_sessions s
       WHERE s.strategy_execution_binding_id=p_strategy_execution_binding_id
         AND s.trading_account_id=p_trading_account_id
         AND s.account_connector_id=p_account_connector_id
         AND s.symbol=p_symbol AND s.state='ACTIVE'
    ) THEN
        RAISE EXCEPTION 'P90_ACCOUNT_ARM_EXACT_SCOPE_ALREADY_ACTIVE';
    END IF;

    INSERT INTO control.account_live_arm_sessions(
        account_live_arm_session_id,strategy_activation_id,
        strategy_execution_binding_id,trading_account_id,
        account_connector_id,strategy_id,strategy_version,
        strategy_config_fingerprint,symbol,release_commit,state,
        owner_approved_at,activated_at,deactivated_at,
        owner_approval_source
    ) VALUES(
        p_account_live_arm_session_id,v_activation_id,
        p_strategy_execution_binding_id,p_trading_account_id,
        p_account_connector_id,v_strategy_id,v_strategy_version,
        v_fingerprint,p_symbol,p_release_commit,'ACTIVE',
        p_owner_approved_at,p_owner_approved_at,NULL,
        p_owner_approval_source
    );
    RETURN p_account_live_arm_session_id;
END;
$function$;

CREATE OR REPLACE FUNCTION control.disarm_p90l90_real_v3_account_scopes(
    p_deactivated_at timestamptz
) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
DECLARE
    v_count integer;
BEGIN
    IF session_user <> 'cripta'
       OR current_setting('transaction_isolation') <> 'serializable'
       OR p_deactivated_at IS NULL
    THEN
        RAISE EXCEPTION 'P90_ACCOUNT_DISARM_CALLER_OR_TIME_INVALID';
    END IF;
    UPDATE control.account_live_arm_sessions
       SET state='DISARMED',deactivated_at=p_deactivated_at,
           updated_at=clock_timestamp()
     WHERE state='ACTIVE'
       AND strategy_version='2.0-p90l90-real-main-sub-20261028'
       AND strategy_id IN (
          'p90l90_legacy_main_ltc_inj',
          'p90l90_legacy_sub_dot_arb_apt'
       );
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$function$;

ALTER FUNCTION strategy_entry.prearm_p90l90_real_v3(
  timestamptz,text,text
) OWNER TO cripta_p90l90_real_v3_control;
ALTER FUNCTION strategy_entry.unprearm_p90l90_real_v3(
  timestamptz,text,text
) OWNER TO cripta_p90l90_real_v3_control;
ALTER FUNCTION control.arm_p90l90_real_v3_account_scope(
  text,text,text,text,text,text,text,timestamptz,text
) OWNER TO cripta_p90l90_real_v3_control;
ALTER FUNCTION control.disarm_p90l90_real_v3_account_scopes(
  timestamptz
) OWNER TO cripta_p90l90_real_v3_control;
REVOKE CREATE ON SCHEMA strategy_entry,control
  FROM cripta_p90l90_real_v3_control;

REVOKE ALL ON FUNCTION strategy_entry.prearm_p90l90_real_v3(
  timestamptz,text,text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION strategy_entry.unprearm_p90l90_real_v3(
  timestamptz,text,text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.arm_p90l90_real_v3_account_scope(
  text,text,text,text,text,text,text,timestamptz,text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.disarm_p90l90_real_v3_account_scopes(
  timestamptz
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION strategy_entry.prearm_p90l90_real_v3(
  timestamptz,text,text
) TO cripta;
GRANT EXECUTE ON FUNCTION strategy_entry.unprearm_p90l90_real_v3(
  timestamptz,text,text
) TO cripta;
GRANT EXECUTE ON FUNCTION control.arm_p90l90_real_v3_account_scope(
  text,text,text,text,text,text,text,timestamptz,text
) TO cripta;
GRANT EXECUTE ON FUNCTION control.disarm_p90l90_real_v3_account_scopes(
  timestamptz
) TO cripta;

DO $postcheck$
BEGIN
  IF pg_has_role('cripta','cripta_p90l90_real_v3_control','MEMBER')
     OR has_table_privilege(
       'cripta','strategy_entry.strategy_execution_bindings','UPDATE'
     )
     OR has_table_privilege(
       'cripta','control.account_live_arm_sessions','INSERT'
     )
     OR has_table_privilege(
       'cripta','control.account_live_arm_sessions','UPDATE'
     )
     OR has_schema_privilege(
       'cripta_p90l90_real_v3_control','strategy_entry','CREATE'
     )
     OR has_schema_privilege(
       'cripta_p90l90_real_v3_control','control','CREATE'
     )
     OR NOT has_function_privilege(
       'cripta',
       'strategy_entry.prearm_p90l90_real_v3(timestamptz,text,text)',
       'EXECUTE'
     )
     OR NOT has_function_privilege(
       'cripta',
       'strategy_entry.unprearm_p90l90_real_v3(timestamptz,text,text)',
       'EXECUTE'
     )
     OR NOT has_function_privilege(
       'cripta',
       'control.arm_p90l90_real_v3_account_scope(text,text,text,text,text,text,text,timestamptz,text)',
       'EXECUTE'
     )
  THEN
    RAISE EXCEPTION 'P90_REAL_CONTROL_POST_MIGRATION_PRIVILEGE_DRIFT';
  END IF;
END
$postcheck$;
COMMIT;
