-- Owner decision 2026-11-01T10:05:57Z: deterministic full-contour REAL
-- diagnostic v2.  This migration changes only the bounded diagnostic control
-- functions.  It does not activate a Strategy, open the mainnet gate, create
-- execution permission/LIVE arms, or mutate Exchange.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

GRANT UPDATE ON strategy_entry.strategy_execution_bindings
  TO cripta_owner_full_contour_binding;
GRANT SELECT ON control.owner_full_contour_diagnostic_runs
  TO cripta_owner_full_contour_binding;

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
  v_old_binding text;
  v_parent_strategy text;
  v_parent_activation text;
  v_parent_binding text;
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
     AND a.strategy_version='2.0-owner-full-contour-e2e-20261101'
     AND a.enabled=true AND a.disabled_at IS NULL;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_V2_ENABLED_IMMUTABLE_CARD_REQUIRED';
  END IF;

  IF v_strategy='owner_full_contour_diag_main' THEN
    v_account:='ta-2122c81ecff14f3b85b102db605212f7';
    v_connector:='ac-ed73de20410548d6841aeafa5f196f70';
    v_activation:='activation-owner-full-contour-main-v2-20261101';
    v_binding:='binding-owner-full-contour-main-v2-20261101';
    v_old_binding:='binding-owner-full-contour-main-20261101';
    v_parent_strategy:='p90l90_legacy_main_ltc_inj';
    v_parent_activation:='activation-p90l90-real-main-20261029-v3';
    v_parent_binding:='binding-p90l90-real-main-20261029-v3';
    v_symbols:='["INJUSDT","LTCUSDT"]'::jsonb;
  ELSIF v_strategy='owner_full_contour_diag_sub' THEN
    v_account:='ta-853ac5fcd58185697396d33cd57459fa';
    v_connector:='ac-ec57819f875f1bfd929a382d6dc8a125';
    v_activation:='activation-owner-full-contour-sub-v2-20261101';
    v_binding:='binding-owner-full-contour-sub-v2-20261101';
    v_old_binding:='binding-owner-full-contour-sub-20261101';
    v_parent_strategy:='p90l90_legacy_sub_dot_arb_apt';
    v_parent_activation:='activation-p90l90-real-sub-20261029-v3';
    v_parent_binding:='binding-p90l90-real-sub-20261029-v3';
    v_symbols:='["APTUSDT","ARBUSDT","DOTUSDT"]'::jsonb;
  ELSE
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_STRATEGY_NOT_APPROVED';
  END IF;

  IF (p_binding_id,p_activation_id,p_account_id)
      IS DISTINCT FROM (v_binding,v_activation,v_account)
     OR v_card->'symbols' IS DISTINCT FROM v_symbols
  THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_V2_ACCOUNT_SCOPE_DRIFT';
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
     OR NOT (
       (v_card->'exit_policy'->>'payload_json')::jsonb->'rules'
       @> '[{"rule_id":"OWNER_DIAGNOSTIC_FORCE_CLOSE"}]'::jsonb
     )
  THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_V2_POLICY_NOT_EXACT';
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
       SELECT 1 FROM control.owner_full_contour_diagnostic_runs
        WHERE state IN (
          'REQUESTED','MATERIALIZED','DISPATCHED','POSITION_OPEN',
          'RECONCILIATION_REQUIRED'
        )
     )
  THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_V2_REGISTRATION_REQUIRES_IDLE_DISARMED_STATE';
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
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_V2_PLAN_MISSING';
  END IF;

  IF NOT EXISTS (
    SELECT 1
      FROM strategy_entry.strategy_execution_bindings b
      JOIN strategy_entry.strategy_activations a
        ON a.activation_id=b.strategy_activation_id
     WHERE b.strategy_execution_binding_id=v_parent_binding
       AND b.strategy_activation_id=v_parent_activation
       AND b.trading_account_id=v_account
       AND b.execution_leg='PRIMARY'
       AND b.execution_role='PRIMARY'
       AND b.allocation_mode='DEDICATED_STRATEGY'
       AND b.binding_state='ACTIVE'
       AND a.strategy_id=v_parent_strategy
       AND a.strategy_version='2.0-p90l90-real-main-sub-20261028'
       AND a.enabled=true
       AND a.disabled_at IS NULL
  ) THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_PARENT_P90_BINDING_NOT_ACTIVE';
  END IF;

  IF EXISTS (
    SELECT 1 FROM strategy_entry.strategy_execution_bindings
     WHERE strategy_activation_id=v_activation
        OR strategy_execution_binding_id=v_binding
  ) THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_V2_BINDING_ALREADY_EXISTS';
  END IF;

  UPDATE strategy_entry.strategy_execution_bindings
     SET binding_state='INACTIVE',
         effective_to=transaction_timestamp(),
         operator='owner',
         source='OWNER_FULL_CONTOUR_DIAGNOSTIC_V2_20261101',
         reason='SUPERSEDED_BY_DETERMINISTIC_FULL_CONTOUR_V2'
   WHERE strategy_execution_binding_id=v_old_binding
     AND trading_account_id=v_account
     AND allocation_mode='SUBORDINATE_DIAGNOSTIC'
     AND binding_state='ACTIVE';

  IF NOT FOUND AND NOT EXISTS (
      SELECT 1 FROM strategy_entry.strategy_execution_bindings
       WHERE strategy_execution_binding_id=v_old_binding
         AND trading_account_id=v_account
         AND allocation_mode='SUBORDINATE_DIAGNOSTIC'
         AND binding_state='INACTIVE'
  ) AND EXISTS (
      SELECT 1 FROM strategy_entry.strategy_cards
       WHERE strategy_id=v_strategy
         AND strategy_version='1.0-owner-full-contour-20261101'
  ) THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_V1_BINDING_NOT_EXACT';
  END IF;

  INSERT INTO strategy_entry.strategy_execution_bindings(
    strategy_execution_binding_id,strategy_activation_id,
    execution_leg,execution_role,trading_account_id,
    allocation_mode,binding_state,effective_from,effective_to,
    operator,source,reason
  ) VALUES (
    v_binding,v_activation,'PRIMARY','PRIMARY',v_account,
    'SUBORDINATE_DIAGNOSTIC','ACTIVE',transaction_timestamp(),NULL,
    'owner','OWNER_FULL_CONTOUR_DIAGNOSTIC_V2_20261101',
    'DETERMINISTIC_E2E_DIAGNOSTIC_CAPABILITY_EXECUTION_PERMISSION_OFF'
  );
  RETURN v_binding;
END;
$function$;

ALTER FUNCTION strategy_entry.register_owner_full_contour_binding(text,text,text)
  OWNER TO cripta_owner_full_contour_binding;

CREATE OR REPLACE FUNCTION control.arm_owner_full_contour_account_scope(
    p_session_id text,
    p_live_session_id text,
    p_binding_id text,
    p_account_id text,
    p_connector_id text,
    p_strategy_id text,
    p_strategy_fingerprint text,
    p_activation_id text,
    p_symbol text,
    p_release_commit text,
    p_approved_at timestamptz,
    p_source text
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $account_arm$
DECLARE
  v_expected_strategy text;
  v_expected_account text;
  v_expected_connector text;
  v_expected_activation text;
  v_expected_binding text;
  v_parent_strategy text;
  v_parent_activation text;
  v_parent_binding text;
BEGIN
  IF session_user <> 'cripta'
     OR p_source NOT LIKE 'dashboard:owner-full-contour-diagnostic:%'
     OR p_release_commit !~ '^[0-9a-f]{40}$'
     OR p_symbol NOT IN ('LTCUSDT','INJUSDT','APTUSDT','ARBUSDT','DOTUSDT')
  THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_ACCOUNT_ARM_SCOPE_INVALID';
  END IF;

  IF p_symbol IN ('LTCUSDT','INJUSDT') THEN
    v_expected_strategy:='owner_full_contour_diag_main';
    v_expected_account:='ta-2122c81ecff14f3b85b102db605212f7';
    v_expected_connector:='ac-ed73de20410548d6841aeafa5f196f70';
    v_expected_activation:='activation-owner-full-contour-main-v2-20261101';
    v_expected_binding:='binding-owner-full-contour-main-v2-20261101';
    v_parent_strategy:='p90l90_legacy_main_ltc_inj';
    v_parent_activation:='activation-p90l90-real-main-20261029-v3';
    v_parent_binding:='binding-p90l90-real-main-20261029-v3';
  ELSE
    v_expected_strategy:='owner_full_contour_diag_sub';
    v_expected_account:='ta-853ac5fcd58185697396d33cd57459fa';
    v_expected_connector:='ac-ec57819f875f1bfd929a382d6dc8a125';
    v_expected_activation:='activation-owner-full-contour-sub-v2-20261101';
    v_expected_binding:='binding-owner-full-contour-sub-v2-20261101';
    v_parent_strategy:='p90l90_legacy_sub_dot_arb_apt';
    v_parent_activation:='activation-p90l90-real-sub-20261029-v3';
    v_parent_binding:='binding-p90l90-real-sub-20261029-v3';
  END IF;

  IF (p_strategy_id,p_account_id,p_connector_id,p_activation_id,p_binding_id)
       IS DISTINCT FROM
     (v_expected_strategy,v_expected_account,v_expected_connector,
      v_expected_activation,v_expected_binding)
  THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_ACCOUNT_ARM_IDENTITY_DRIFT';
  END IF;

  IF NOT EXISTS (
      SELECT 1
        FROM strategy_entry.strategy_activations a
        JOIN strategy_entry.strategy_execution_bindings b
          ON b.strategy_activation_id=a.activation_id
       WHERE a.activation_id=p_activation_id
         AND a.strategy_id=p_strategy_id
         AND a.strategy_version='2.0-owner-full-contour-e2e-20261101'
         AND a.strategy_config_fingerprint=p_strategy_fingerprint
         AND a.enabled=true AND a.disabled_at IS NULL
         AND b.strategy_execution_binding_id=p_binding_id
         AND b.trading_account_id=p_account_id
         AND b.binding_state='ACTIVE'
         AND b.execution_leg='PRIMARY'
         AND b.execution_role='PRIMARY'
         AND b.allocation_mode='SUBORDINATE_DIAGNOSTIC'
  ) THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_ACCOUNT_ARM_BINDING_NOT_READY';
  END IF;

  IF NOT EXISTS (
      SELECT 1
        FROM control.live_arm_sessions s
       WHERE s.live_arm_session_id=p_live_session_id
         AND s.strategy_id=p_strategy_id
         AND s.strategy_version='2.0-owner-full-contour-e2e-20261101'
         AND s.strategy_config_fingerprint=p_strategy_fingerprint
         AND s.strategy_activation_id=p_activation_id
         AND s.symbol=p_symbol
         AND s.release_commit=p_release_commit
         AND s.state='ACTIVE'
         AND s.deactivated_at IS NULL
  ) THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_GENERIC_ARM_NOT_ACTIVE';
  END IF;

  IF NOT EXISTS (
      SELECT 1
        FROM strategy_entry.strategy_execution_bindings b
        JOIN strategy_entry.strategy_activations a
          ON a.activation_id=b.strategy_activation_id
       WHERE b.strategy_execution_binding_id=v_parent_binding
         AND b.strategy_activation_id=v_parent_activation
         AND b.trading_account_id=p_account_id
         AND b.execution_leg='PRIMARY'
         AND b.execution_role='PRIMARY'
         AND b.allocation_mode='DEDICATED_STRATEGY'
         AND b.binding_state='ACTIVE'
         AND a.strategy_id=v_parent_strategy
         AND a.strategy_version='2.0-p90l90-real-main-sub-20261028'
         AND a.enabled=true AND a.disabled_at IS NULL
  ) THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_PARENT_P90_BINDING_NOT_ACTIVE';
  END IF;

  IF EXISTS (
      SELECT 1 FROM strategy_entry.execution_permissions
       WHERE strategy_id=v_parent_strategy
         AND strategy_version='2.0-p90l90-real-main-sub-20261028'
         AND enabled=true
  ) OR EXISTS (
      SELECT 1 FROM control.live_arm_sessions
       WHERE strategy_id=v_parent_strategy
         AND strategy_version='2.0-p90l90-real-main-sub-20261028'
         AND state='ACTIVE'
  ) OR EXISTS (
      SELECT 1 FROM control.account_live_arm_sessions
       WHERE strategy_id=v_parent_strategy
         AND strategy_version='2.0-p90l90-real-main-sub-20261028'
         AND state='ACTIVE' AND deactivated_at IS NULL
  ) THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_PARENT_P90_RIGHTS_MUST_BE_OFF';
  END IF;

  IF (SELECT enabled FROM control.execution_gates WHERE mode='mainnet')
       IS DISTINCT FROM false
     OR (SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true)
       <> 1
     OR NOT EXISTS (
       SELECT 1 FROM strategy_entry.execution_permissions
        WHERE strategy_id=p_strategy_id
          AND strategy_version='2.0-owner-full-contour-e2e-20261101'
          AND strategy_config_fingerprint=p_strategy_fingerprint
          AND enabled=true
     )
     OR (SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE')
       <> 1
     OR (SELECT count(*) FROM control.account_live_arm_sessions WHERE state='ACTIVE')
       <> 0
  THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_DIAGNOSTIC_ONLY_ARM_SCOPE_NOT_EXACT';
  END IF;

  IF EXISTS (
      SELECT 1
        FROM runtime.trade_commands c
       WHERE c.trading_account_id=p_account_id
         AND c.symbol=p_symbol
         AND c.command_type='entry'
         AND c.state IN ('queued','running')
         AND c.identity_status='PROVEN'
  ) THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_SYMBOL_ENTRY_MUTATION_PENDING';
  END IF;

  INSERT INTO control.account_live_arm_sessions(
    account_live_arm_session_id,
    strategy_activation_id,strategy_execution_binding_id,
    trading_account_id,account_connector_id,
    strategy_id,strategy_version,strategy_config_fingerprint,
    symbol,release_commit,state,owner_approved_at,activated_at,
    deactivated_at,owner_approval_source
  ) VALUES(
    p_session_id,
    p_activation_id,p_binding_id,p_account_id,p_connector_id,
    p_strategy_id,'2.0-owner-full-contour-e2e-20261101',
    p_strategy_fingerprint,p_symbol,p_release_commit,'ACTIVE',
    p_approved_at,p_approved_at,NULL,p_source
  );
  RETURN p_session_id;
END;
$account_arm$;

ALTER FUNCTION control.arm_owner_full_contour_account_scope(
  text,text,text,text,text,text,text,text,text,text,timestamptz,text
) OWNER TO cripta_owner_full_contour_binding;

CREATE OR REPLACE FUNCTION control.disarm_owner_full_contour_account_scope(
    p_session_id text,
    p_deactivated_at timestamptz,
    p_source text
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $account_disarm$
BEGIN
  IF session_user <> 'cripta'
     OR p_source NOT LIKE 'owner_full_contour_real_diagnostic:%'
  THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_ACCOUNT_DISARM_SCOPE_INVALID';
  END IF;
  UPDATE control.account_live_arm_sessions
     SET state='DISARMED',deactivated_at=p_deactivated_at,
         updated_at=clock_timestamp()
   WHERE account_live_arm_session_id=p_session_id
     AND strategy_version='2.0-owner-full-contour-e2e-20261101'
     AND state='ACTIVE';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'OWNER_FULL_CONTOUR_ACCOUNT_SESSION_NOT_ACTIVE';
  END IF;
  RETURN p_session_id;
END;
$account_disarm$;

ALTER FUNCTION control.disarm_owner_full_contour_account_scope(
  text,timestamptz,text
) OWNER TO cripta_owner_full_contour_binding;

REVOKE ALL ON FUNCTION
  strategy_entry.register_owner_full_contour_binding(text,text,text)
  FROM PUBLIC;
REVOKE ALL ON FUNCTION
  control.arm_owner_full_contour_account_scope(
    text,text,text,text,text,text,text,text,text,text,timestamptz,text
  ) FROM PUBLIC;
REVOKE ALL ON FUNCTION
  control.disarm_owner_full_contour_account_scope(text,timestamptz,text)
  FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
  strategy_entry.register_owner_full_contour_binding(text,text,text)
  TO cripta;
GRANT EXECUTE ON FUNCTION
  control.arm_owner_full_contour_account_scope(
    text,text,text,text,text,text,text,text,text,text,timestamptz,text
  ) TO cripta;
GRANT EXECUTE ON FUNCTION
  control.disarm_owner_full_contour_account_scope(text,timestamptz,text)
  TO cripta;

COMMIT;
