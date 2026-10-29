-- P90 REAL v3: narrow account-scoped OWNER ARM mutation rail.
-- Runtime keeps SELECT-only access to control.account_live_arm_sessions.
-- Only this SECURITY DEFINER function may create/retire exact P90 v3 account
-- arm rows, and only when a matching release-bound Strategy/symbol LIVE-arm
-- session already exists. It never opens the global mainnet gate.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

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
SET search_path = pg_catalog
AS $$
DECLARE
    v_activation_id text;
    v_strategy_id text;
    v_strategy_version text;
    v_fingerprint text;
    v_account_kind text;
    v_existing text;
BEGIN
    IF session_user <> 'cripta' THEN
        RAISE EXCEPTION 'P90_ACCOUNT_ARM_CALLER_NOT_CRIPTA';
    END IF;
    IF p_account_live_arm_session_id IS NULL
       OR btrim(p_account_live_arm_session_id) = ''
       OR p_live_arm_session_id IS NULL
       OR btrim(p_live_arm_session_id) = ''
       OR p_release_commit !~ '^[0-9a-f]{40}$'
       OR p_owner_approved_at IS NULL
       OR p_owner_approval_source IS NULL
       OR btrim(p_owner_approval_source) = '' THEN
        RAISE EXCEPTION 'P90_ACCOUNT_ARM_ARGUMENT_INVALID';
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
       AND ta.status='ACTIVE'
       AND ta.environment='MAINNET'
       AND ta.venue_id='venue-bybit'
       AND ta.account_type='UNIFIED'
     FOR UPDATE OF b,a,ta;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'P90_ACCOUNT_ARM_BINDING_OR_ACTIVATION_NOT_EXACT';
    END IF;
    IF (
        v_strategy_id='p90l90_legacy_main_ltc_inj'
        AND (v_account_kind<>'MAIN' OR p_symbol NOT IN ('LTCUSDT','INJUSDT'))
    ) OR (
        v_strategy_id='p90l90_legacy_sub_dot_arb_apt'
        AND (v_account_kind<>'SUB' OR p_symbol NOT IN ('APTUSDT','ARBUSDT','DOTUSDT'))
    ) THEN
        RAISE EXCEPTION 'P90_ACCOUNT_ARM_ACCOUNT_KIND_OR_SYMBOL_MISMATCH';
    END IF;

    PERFORM 1
      FROM identity.account_connectors c
     WHERE c.account_connector_id=p_account_connector_id
       AND c.trading_account_id=p_trading_account_id
       AND c.connector_kind='CEX_PRIVATE_RUNTIME'
       AND c.adapter_id='BYBIT_V5_PRIVATE'
       AND c.status='ACTIVE'
       AND c.retired_at IS NULL
       AND nullif(btrim(c.credential_ref),'') IS NOT NULL
     FOR SHARE;
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
     FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'P90_ACCOUNT_ARM_STRATEGY_SYMBOL_OWNER_ARM_MISSING';
    END IF;

    SELECT s.account_live_arm_session_id
      INTO v_existing
      FROM control.account_live_arm_sessions s
     WHERE s.strategy_execution_binding_id=p_strategy_execution_binding_id
       AND s.trading_account_id=p_trading_account_id
       AND s.account_connector_id=p_account_connector_id
       AND s.symbol=p_symbol
       AND s.state='ACTIVE'
     FOR UPDATE;
    IF FOUND THEN
        RAISE EXCEPTION 'P90_ACCOUNT_ARM_EXACT_SCOPE_ALREADY_ACTIVE:%',v_existing;
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
$$;

CREATE OR REPLACE FUNCTION control.disarm_p90l90_real_v3_account_scopes(
    p_deactivated_at timestamptz
) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_count integer;
BEGIN
    IF session_user <> 'cripta' OR p_deactivated_at IS NULL THEN
        RAISE EXCEPTION 'P90_ACCOUNT_DISARM_CALLER_OR_TIME_INVALID';
    END IF;
    UPDATE control.account_live_arm_sessions
       SET state='DISARMED',
           deactivated_at=p_deactivated_at,
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
$$;

REVOKE ALL ON FUNCTION control.arm_p90l90_real_v3_account_scope(
    text,text,text,text,text,text,text,timestamptz,text
) FROM PUBLIC;
REVOKE ALL ON FUNCTION control.disarm_p90l90_real_v3_account_scopes(
    timestamptz
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.arm_p90l90_real_v3_account_scope(
    text,text,text,text,text,text,text,timestamptz,text
) TO cripta;
GRANT EXECUTE ON FUNCTION control.disarm_p90l90_real_v3_account_scopes(
    timestamptz
) TO cripta;

COMMIT;
