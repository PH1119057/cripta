-- Issue #816: R06 Exit must lock one immutable MarketPublication without
-- granting runtime role cripta UPDATE on the publication table.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

DO $role$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles
         WHERE rolname='cripta_p90_exit_publication_locker'
    ) THEN
        CREATE ROLE cripta_p90_exit_publication_locker
            NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOINHERIT NOREPLICATION NOBYPASSRLS;
    END IF;
END
$role$;

ALTER ROLE cripta_p90_exit_publication_locker
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;

REVOKE cripta_p90_exit_publication_locker FROM cripta;

GRANT USAGE ON SCHEMA analytics
    TO cripta_p90_exit_publication_locker;
GRANT SELECT, UPDATE ON analytics.market_publications
    TO cripta_p90_exit_publication_locker;

-- CREATE is required only for ownership transfer below and is revoked before
-- COMMIT. Runtime role cripta never inherits this role or table UPDATE.
GRANT CREATE ON SCHEMA analytics
    TO cripta_p90_exit_publication_locker;

CREATE OR REPLACE FUNCTION analytics.lock_p90_exit_market_publication(
    p_publication_id bigint
)
RETURNS TABLE(
    publication_id bigint,
    source_session_id text,
    producer_release_commit text,
    publication_status text
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
    SELECT p.publication_id,
           p.source_session_id,
           p.producer_release_commit,
           p.publication_status
      FROM analytics.market_publications AS p
     WHERE p.publication_id=p_publication_id
       AND p_publication_id > 0
     FOR SHARE OF p
$function$;

ALTER FUNCTION analytics.lock_p90_exit_market_publication(bigint)
    OWNER TO cripta_p90_exit_publication_locker;

REVOKE CREATE ON SCHEMA analytics
    FROM cripta_p90_exit_publication_locker;
REVOKE ALL ON FUNCTION analytics.lock_p90_exit_market_publication(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION analytics.lock_p90_exit_market_publication(bigint) TO cripta;

DO $verify$
DECLARE
    v_role record;
BEGIN
    SELECT rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,
           rolinherit,rolreplication,rolbypassrls
      INTO v_role
      FROM pg_roles
     WHERE rolname='cripta_p90_exit_publication_locker';

    IF v_role IS NULL
       OR v_role.rolcanlogin
       OR v_role.rolsuper
       OR v_role.rolcreatedb
       OR v_role.rolcreaterole
       OR v_role.rolinherit
       OR v_role.rolreplication
       OR v_role.rolbypassrls
    THEN
        RAISE EXCEPTION 'P90_EXIT_PUBLICATION_LOCK_OWNER_ROLE_DRIFT';
    END IF;

    IF pg_has_role(
        'cripta','cripta_p90_exit_publication_locker','MEMBER'
    ) THEN
        RAISE EXCEPTION
            'runtime role cripta must not inherit publication lock owner role';
    END IF;

    IF has_table_privilege(
        'cripta',
        'analytics.market_publications',
        'UPDATE'
    ) THEN
        RAISE EXCEPTION
            'runtime role cripta must not receive publication UPDATE';
    END IF;
    IF has_table_privilege(
        'cripta',
        'analytics.market_publications',
        'DELETE'
    ) OR has_table_privilege(
        'cripta',
        'analytics.market_publications',
        'TRUNCATE'
    ) THEN
        RAISE EXCEPTION
            'runtime role cripta publication immutability drift';
    END IF;

    IF NOT has_function_privilege(
        'cripta',
        'analytics.lock_p90_exit_market_publication(bigint)',
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION
            'runtime role cripta must execute publication lock helper';
    END IF;

    IF NOT has_table_privilege(
        'cripta_p90_exit_publication_locker',
        'analytics.market_publications',
        'SELECT'
    ) OR NOT has_table_privilege(
        'cripta_p90_exit_publication_locker',
        'analytics.market_publications',
        'UPDATE'
    ) THEN
        RAISE EXCEPTION 'P90_EXIT_PUBLICATION_LOCK_OWNER_PRIVILEGE_DRIFT';
    END IF;
END
$verify$;

COMMIT;
