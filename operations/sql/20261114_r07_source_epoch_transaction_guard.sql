-- Issue #470 / R07. DB-enforced native source epoch fence, NOT a financial ARM.
-- Apply only by authorized PostgreSQL migration actor (postgres) after
-- verified GitHub release/backup/installed producer safety. Source sync != SQL.
-- A transaction-scoped guard is intentionally DIFFERENT from the producer's
-- lifetime session-level advisory lock. No new runtime grants/roles.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '15s';

CREATE OR REPLACE FUNCTION analytics.r07_source_epoch_tx_guard_v1()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $r07_guard$
DECLARE
    source_id text;
BEGIN
    IF TG_OP = 'UPDATE'
       AND OLD.source_session_id IS DISTINCT FROM NEW.source_session_id THEN
        RAISE EXCEPTION 'R07_SOURCE_SESSION_IDENTITY_IMMUTABLE';
    END IF;
    IF TG_OP = 'UPDATE'
       AND OLD.market_source_id IS DISTINCT FROM NEW.market_source_id THEN
        RAISE EXCEPTION 'R07_SOURCE_MARKET_IDENTITY_IMMUTABLE';
    END IF;
    IF TG_OP = 'DELETE' THEN
        source_id := OLD.market_source_id;
    ELSE
        source_id := NEW.market_source_id;
    END IF;
    IF source_id IS NULL OR length(source_id) < 1 THEN
        RAISE EXCEPTION 'R07_SOURCE_MARKET_IDENTITY_MISSING';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
            'CRIPTA:R07:SOURCE_EPOCH_TRANSITION:V1:' || source_id, 0
        )
    );
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$r07_guard$;

DO $r07_install$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger
         WHERE tgrelid='analytics.native_public_trade_source_sessions'::regclass
           AND tgname='r07_source_epoch_guard_v1'
    ) THEN
        IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_trigger t
             WHERE t.tgrelid='analytics.native_public_trade_source_sessions'::regclass
               AND t.tgname='r07_source_epoch_guard_v1'
               AND t.tgenabled='O' AND t.tgisinternal=false AND t.tgtype=31
               AND t.tgfoid='analytics.r07_source_epoch_tx_guard_v1()'::regprocedure
        ) THEN
            RAISE EXCEPTION 'R07_SOURCE_EPOCH_TRIGGER_ABI_DRIFT';
        END IF;
    ELSE
        EXECUTE 'CREATE TRIGGER r07_source_epoch_guard_v1
                 BEFORE INSERT OR UPDATE OF source_session_id, state,
                   market_source_id, producer_release_commit OR DELETE
                 ON analytics.native_public_trade_source_sessions
                 FOR EACH ROW EXECUTE FUNCTION analytics.r07_source_epoch_tx_guard_v1()';
    END IF;
    IF NOT has_table_privilege(
        'cripta', 'analytics.native_public_trade_source_sessions', 'SELECT'
    ) OR NOT has_table_privilege(
        'cripta', 'analytics.native_public_trade_source_sessions', 'UPDATE'
    ) THEN
        RAISE EXCEPTION 'R07_SOURCE_SESSION_EXISTING_RUNTIME_ACL_UNVERIFIED';
    END IF;
END
$r07_install$;

COMMENT ON FUNCTION analytics.r07_source_epoch_tx_guard_v1() IS
'R07 single-MarketSource transaction epoch fence; no LIVE/Entry permissions; source producer keeps existing UPDATE';
COMMENT ON TRIGGER r07_source_epoch_guard_v1
ON analytics.native_public_trade_source_sessions IS
'R07 DB-side xact exclusive epoch lock; Consumer must hold matching shared lock through dispatch commit';
COMMIT;
