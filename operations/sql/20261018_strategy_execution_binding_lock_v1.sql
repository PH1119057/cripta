BEGIN;

SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

-- Runtime role cripta must be able to hold a transaction-scoped row lock on the
-- exact StrategyExecutionBinding admission lineage without receiving UPDATE on
-- the control tables themselves. The function owner is deliberately a NOLOGIN,
-- non-superuser role that is not granted to cripta.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname='cripta_entry_binding_locker'
    ) THEN
        CREATE ROLE cripta_entry_binding_locker
            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOINHERIT NOREPLICATION NOBYPASSRLS;
    END IF;
END;
$$;

ALTER ROLE cripta_entry_binding_locker
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;

REVOKE cripta_entry_binding_locker FROM cripta;

GRANT USAGE ON SCHEMA strategy_entry, identity
    TO cripta_entry_binding_locker;
GRANT SELECT, UPDATE ON
    strategy_entry.strategy_execution_bindings,
    strategy_entry.strategy_activations,
    identity.trading_accounts
TO cripta_entry_binding_locker;

-- CREATE is needed only for the ownership transfer below and is removed before
-- COMMIT. The role remains unable to create arbitrary schema objects at runtime.
GRANT CREATE ON SCHEMA strategy_entry TO cripta_entry_binding_locker;

CREATE OR REPLACE FUNCTION strategy_entry.lock_strategy_execution_binding(
    p_strategy_execution_binding_id text
)
RETURNS TABLE(
    strategy_activation_id text,
    execution_leg text,
    trading_account_id text,
    execution_role text,
    allocation_mode text,
    binding_state text,
    effective_from timestamptz,
    effective_to timestamptz,
    trading_account_status text,
    environment text,
    activation_enabled boolean
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
    SELECT b.strategy_activation_id,
           b.execution_leg,
           b.trading_account_id,
           b.execution_role,
           b.allocation_mode,
           b.binding_state,
           b.effective_from,
           b.effective_to,
           ta.status,
           ta.environment,
           a.enabled
      FROM strategy_entry.strategy_execution_bindings AS b
      JOIN identity.trading_accounts AS ta
        ON ta.trading_account_id=b.trading_account_id
      JOIN strategy_entry.strategy_activations AS a
        ON a.activation_id=b.strategy_activation_id
     WHERE b.strategy_execution_binding_id=p_strategy_execution_binding_id
     FOR SHARE OF b, ta, a
$function$;

ALTER FUNCTION strategy_entry.lock_strategy_execution_binding(text)
    OWNER TO cripta_entry_binding_locker;

REVOKE CREATE ON SCHEMA strategy_entry FROM cripta_entry_binding_locker;
REVOKE ALL ON FUNCTION strategy_entry.lock_strategy_execution_binding(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION strategy_entry.lock_strategy_execution_binding(text) TO cripta;

DO $$
BEGIN
    IF pg_has_role('cripta','cripta_entry_binding_locker','MEMBER') THEN
        RAISE EXCEPTION 'runtime role cripta must not inherit the binding-lock owner role';
    END IF;
    IF has_table_privilege(
        'cripta',
        'strategy_entry.strategy_execution_bindings',
        'UPDATE'
    ) THEN
        RAISE EXCEPTION 'runtime role cripta must not receive binding UPDATE';
    END IF;
    IF NOT has_function_privilege(
        'cripta',
        'strategy_entry.lock_strategy_execution_binding(text)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION 'runtime role cripta must be able to execute binding lock helper';
    END IF;
END;
$$;

COMMIT;
