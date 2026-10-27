-- One-time owner-approved passive account assignment rail for P90L90 (#243).
-- Applied by postgres as schema/migration actor via exact GitHub release.
-- Runtime application DB role cripta gets EXECUTE only, never arbitrary INSERT
-- into strategy_execution_bindings. No LIVE/arms/credential mutation.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '20s';

DO $migration$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='cripta_p90l90_passive_binding') THEN
        CREATE ROLE cripta_p90l90_passive_binding
            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOINHERIT NOREPLICATION NOBYPASSRLS;
    END IF;
END
$migration$;

ALTER ROLE cripta_p90l90_passive_binding
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
REVOKE cripta_p90l90_passive_binding FROM cripta;

GRANT USAGE ON SCHEMA strategy_entry, identity, control
    TO cripta_p90l90_passive_binding;
GRANT SELECT ON
    strategy_entry.strategy_activations,
    strategy_entry.strategy_cards,
    strategy_entry.strategy_execution_bindings,
    strategy_entry.execution_permissions,
    identity.trading_accounts,
    identity.account_connectors,
    control.execution_gates,
    control.live_arm_sessions
    TO cripta_p90l90_passive_binding;
GRANT INSERT ON strategy_entry.strategy_execution_bindings
    TO cripta_p90l90_passive_binding;

-- Ownership transfer needs CREATE temporarily for the new NOLOGIN owner.
-- It is REVOKED in the same migration before COMMIT.
GRANT CREATE ON SCHEMA strategy_entry TO cripta_p90l90_passive_binding;

-- The authoring actor can invoke this one deliberately constrained function;
-- the function's NOLOGIN owner holds INSERT, not the runtime role.
CREATE OR REPLACE FUNCTION strategy_entry.register_p90l90_inactive_binding(
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
    v_symbol_count integer;
BEGIN
    IF session_user <> 'cripta' THEN
        RAISE EXCEPTION 'PASSIVE_BINDING_ACTOR_MISMATCH';
    END IF;
    IF current_setting('transaction_isolation') <> 'serializable' THEN
        RAISE EXCEPTION 'PASSIVE_BINDING_SERIALIZABLE_REQUIRED';
    END IF;
    IF p_binding_id IS NULL OR p_activation_id IS NULL OR
       p_trading_account_id IS NULL THEN
        RAISE EXCEPTION 'PASSIVE_BINDING_NULL_INPUT';
    END IF;
    SELECT a.strategy_id INTO v_strategy_id
      FROM strategy_entry.strategy_activations a
      JOIN strategy_entry.strategy_cards c
        ON c.strategy_id=a.strategy_id
       AND c.strategy_version=a.strategy_version
       AND c.strategy_config_fingerprint=a.strategy_config_fingerprint
     WHERE a.activation_id=p_activation_id
       AND a.enabled=false AND a.disabled_at IS NOT NULL
       AND a.strategy_version='1.0-p90l90-legacy-passive-20261027';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'PASSIVE_BINDING_ACTIVATION_NOT_EXACT';
    END IF;

    IF v_strategy_id='p90l90_legacy_main_ltc_inj' THEN
        v_expected_account:='ta-2122c81ecff14f3b85b102db605212f7';
        v_expected_activation:='activation-p90l90-legacy-main-20261027-v1';
        v_expected_binding:='binding-p90l90-legacy-main-20261027-v1';
        v_expected_kind:='MAIN';
        v_expected_connector:='ac-ed73de20410548d6841aeafa5f196f70';
        SELECT count(*) INTO v_symbol_count
          FROM strategy_entry.strategy_cards c
         WHERE c.strategy_id=v_strategy_id
           AND c.card_json->'symbols' = '["INJUSDT","LTCUSDT"]'::jsonb;
    ELSIF v_strategy_id='p90l90_legacy_sub_dot_arb_apt' THEN
        v_expected_account:='ta-853ac5fcd58185697396d33cd57459fa';
        v_expected_activation:='activation-p90l90-legacy-sub-20261027-v1';
        v_expected_binding:='binding-p90l90-legacy-sub-20261027-v1';
        v_expected_kind:='SUB';
        v_expected_connector:='ac-ec57819f875f1bfd929a382d6dc8a125';
        SELECT count(*) INTO v_symbol_count
          FROM strategy_entry.strategy_cards c
         WHERE c.strategy_id=v_strategy_id
           AND c.card_json->'symbols' = '["APTUSDT","ARBUSDT","DOTUSDT"]'::jsonb;
    ELSE
        RAISE EXCEPTION 'PASSIVE_BINDING_UNKNOWN_STRATEGY';
    END IF;

    IF p_binding_id<>v_expected_binding OR
       p_activation_id<>v_expected_activation OR
       p_trading_account_id<>v_expected_account OR
       v_symbol_count<>1 THEN
        RAISE EXCEPTION 'PASSIVE_BINDING_EXACT_IDENTITY_MISMATCH';
    END IF;

    IF EXISTS (
        SELECT 1 FROM control.execution_gates
         WHERE mode IN ('mainnet','testnet') AND enabled<>0
    ) OR
    (SELECT count(*) FROM control.execution_gates
      WHERE mode IN ('mainnet','testnet'))<>2 OR
    EXISTS (SELECT 1 FROM strategy_entry.execution_permissions WHERE enabled=true) OR
    EXISTS (SELECT 1 FROM strategy_entry.strategy_activations WHERE enabled=true) OR
    EXISTS (SELECT 1 FROM control.live_arm_sessions WHERE state='ACTIVE')
    THEN
        RAISE EXCEPTION 'PASSIVE_BINDING_TRADING_NOT_DISARMED';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM identity.trading_accounts t
          JOIN identity.account_connectors k
            ON k.trading_account_id=t.trading_account_id
         WHERE t.trading_account_id=v_expected_account
           AND t.account_kind=v_expected_kind
           AND t.status='ACTIVE'
           AND t.environment='MAINNET'
           AND t.account_type='UNIFIED'
           AND k.account_connector_id=v_expected_connector
           AND k.status='ACTIVE' AND k.retired_at IS NULL
           AND k.connector_kind='CEX_PRIVATE_RUNTIME'
           AND k.adapter_id='BYBIT_V5_PRIVATE'
    ) THEN
        RAISE EXCEPTION 'PASSIVE_BINDING_ACCOUNT_CONNECTOR_MISMATCH';
    END IF;
    IF (SELECT count(*) FROM identity.account_connectors
         WHERE trading_account_id=v_expected_account AND status='ACTIVE'
           AND retired_at IS NULL AND connector_kind='CEX_PRIVATE_RUNTIME'
           AND adapter_id='BYBIT_V5_PRIVATE')<>1
    THEN
        RAISE EXCEPTION 'PASSIVE_BINDING_ACCOUNT_CONNECTOR_AMBIGUOUS';
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
        'OWNER_APPROVED_PASSIVE_P90L90_ISSUE_243',
        'PASSIVE_SEPARATE_ACCOUNT_ASSIGNMENT_NO_REAL_ADMISSION'
    );
    RETURN p_binding_id;
END;
$function$;

ALTER FUNCTION strategy_entry.register_p90l90_inactive_binding(text,text,text)
    OWNER TO cripta_p90l90_passive_binding;
REVOKE CREATE ON SCHEMA strategy_entry FROM cripta_p90l90_passive_binding;
REVOKE ALL ON FUNCTION strategy_entry.register_p90l90_inactive_binding(text,text,text)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION strategy_entry.register_p90l90_inactive_binding(text,text,text)
    TO cripta;

DO $verify$
BEGIN
    IF pg_has_role('cripta','cripta_p90l90_passive_binding','MEMBER') OR
       has_table_privilege('cripta',
           'strategy_entry.strategy_execution_bindings','INSERT') OR
       NOT has_function_privilege('cripta',
           'strategy_entry.register_p90l90_inactive_binding(text,text,text)',
           'EXECUTE') THEN
        RAISE EXCEPTION 'PASSIVE_BINDING_PRIVILEGE_INVARIANT';
    END IF;
END;
$verify$;
COMMIT;
