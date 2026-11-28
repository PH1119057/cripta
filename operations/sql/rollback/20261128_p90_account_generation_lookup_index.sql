-- #865: dedicated, additive PostgreSQL 16 migration; NOT a global installer step.
-- Requires approved postgres migration rail, exclusive release/migration ownership,
-- fresh backup/rollback, disk+WAL budget and applicable C0/C1/C2 gates.
-- Execute as postgres with psql -X -v ON_ERROR_STOP=1, never in BEGIN or -1.
-- No table data, grants, service, strategy, account authorization or Exchange changes.
\set ON_ERROR_STOP on
SET search_path = pg_catalog, public;
SET lock_timeout = '3s';
SET statement_timeout = '120s';

-- Explicit rollback only; refuse a conflicting same-name index.
-- An invalid index left by an interrupted concurrent build can be removed
-- only if its exact physical definition matches this migration.
DO $guard$
DECLARE
    target oid := to_regclass('runtime.account_state_generations');
    candidate oid := to_regclass('runtime.ix_account_state_generations_proven_terminal_account_started');
    matches boolean;
BEGIN
    IF current_user <> 'postgres' OR current_database() <> 'cripta'
       OR current_setting('server_version_num')::integer / 10000 <> 16
       OR NOT EXISTS (
           SELECT 1 FROM pg_class
           WHERE oid = target AND relkind = 'r'
             AND pg_get_userbyid(relowner) = 'postgres'
       ) THEN
        RAISE EXCEPTION 'P90_GENERATION_INDEX_MIGRATION_ACTOR_OR_TABLE_UNVERIFIED';
    END IF;
    IF candidate IS NULL THEN
        RETURN;
    END IF;
    SELECT i.indrelid = target AND NOT i.indisunique
           AND pg_get_indexdef(i.indexrelid) = $definition$CREATE INDEX ix_account_state_generations_proven_terminal_account_started ON runtime.account_state_generations USING btree (trading_account_id, account_connector_id, started_at DESC, generation_id DESC) WHERE ((identity_status = 'PROVEN'::text) AND (state = ANY (ARRAY['COMPLETE'::text, 'FAILED'::text])))$definition$
           
      INTO matches
      FROM pg_index i WHERE i.indexrelid = candidate;
    IF matches IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'P90_GENERATION_INDEX_DEFINITION_OR_STATE_UNVERIFIED';
    END IF;
END
$guard$;
DROP INDEX CONCURRENTLY IF EXISTS runtime.ix_account_state_generations_proven_terminal_account_started;
DO $guard$
BEGIN
    IF to_regclass('runtime.ix_account_state_generations_proven_terminal_account_started') IS NOT NULL THEN
        RAISE EXCEPTION 'P90_GENERATION_INDEX_ROLLBACK_UNVERIFIED';
    END IF;
END
$guard$;
