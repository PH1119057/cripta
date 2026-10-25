-- R10 account-scoped physical-key cutover candidate.
-- All current private writers must already use exact TradingAccount conflict targets.
-- This migration is executed ONLY by the quiesced, rollback-protected installer.
-- It never copies, updates, reattributes or deletes historical trades or facts.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '120s';

DO $$
DECLARE
    item record;
    pk record;
    account_unique regclass;
    idx_valid boolean;
    invalid_rows bigint;
BEGIN
    FOR item IN
        SELECT * FROM (VALUES
          ('runtime.wallet_latest'::regclass, 'wallet_latest_pkey',
           'runtime.ux_wallet_latest_trading_account_singleton',
           ARRAY['trading_account_id','singleton']::text[]),
          ('runtime.hot_positions'::regclass, 'hot_positions_pkey',
           'runtime.ux_hot_positions_trading_account_slot',
           ARRAY['trading_account_id','symbol','position_idx']::text[]),
          ('runtime.hot_orders'::regclass, 'hot_orders_pkey',
           'runtime.ux_hot_orders_trading_account_order',
           ARRAY['trading_account_id','order_id']::text[]),
          ('runtime.executions'::regclass, 'executions_pkey',
           'runtime.ux_executions_trading_account_exec',
           ARRAY['trading_account_id','exec_id']::text[]),
          ('runtime.exchange_order_history'::regclass, 'exchange_order_history_pkey',
           'runtime.ux_exchange_order_history_trading_account_order',
           ARRAY['trading_account_id','order_id']::text[])
        ) AS t(relid,old_pk,account_index,expected_columns)
    LOOP
        account_unique := to_regclass(item.account_index);
        SELECT COALESCE(
            ix.indrelid=item.relid
            AND ix.indisunique AND ix.indisvalid AND ix.indisready
            AND ix.indnullsnotdistinct
            AND ix.indnkeyatts=cardinality(item.expected_columns)
            AND (
                SELECT array_agg(att.attname ORDER BY keycol.ordinality)
                  FROM unnest(ix.indkey) WITH ORDINALITY
                       AS keycol(attnum,ordinality)
                  JOIN pg_attribute att
                    ON att.attrelid=ix.indrelid AND att.attnum=keycol.attnum
                 WHERE keycol.ordinality<=ix.indnkeyatts
            )=item.expected_columns,
            false
        )
        INTO idx_valid
        FROM pg_index ix
        WHERE ix.indexrelid=account_unique;
        IF NOT COALESCE(idx_valid,false) THEN
            RAISE EXCEPTION 'ACCOUNT_SCOPED_UNIQUE_INDEX_UNPROVEN: %',item.account_index;
        END IF;

        EXECUTE format(
            'SELECT count(*) FROM %s WHERE NOT ('
            || '(identity_status=''PROVEN'' AND trading_account_id IS NOT NULL AND account_connector_id IS NOT NULL)'
            || ' OR (identity_status=''LEGACY_UNKNOWN'' AND trading_account_id IS NULL AND account_connector_id IS NULL))',
            item.relid
        ) INTO invalid_rows;
        IF invalid_rows<>0 THEN
            RAISE EXCEPTION 'ACCOUNT_IDENTITY_PROVENANCE_INCONSISTENT: % count=%',item.relid,invalid_rows;
        END IF;

        SELECT c.conname,pg_get_constraintdef(c.oid) AS definition,c.conindid
          INTO pk
          FROM pg_constraint c
         WHERE c.conrelid=item.relid AND c.contype='p';
        IF FOUND THEN
            IF pk.conname<>item.old_pk OR
               pk.definition= '' OR
               pk.definition IS NULL THEN
                RAISE EXCEPTION 'UNEXPECTED_LEGACY_PRIMARY_KEY: %',item.relid;
            END IF;
            IF EXISTS (
                SELECT 1 FROM pg_constraint fk
                 WHERE fk.contype='f' AND fk.conindid=pk.conindid
            ) THEN
                RAISE EXCEPTION 'LEGACY_PRIMARY_KEY_STILL_REFERENCED: %',item.relid;
            END IF;
            EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',item.relid,item.old_pk);
        END IF;

        IF EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid=item.relid AND contype='p'
        ) THEN
            RAISE EXCEPTION 'GLOBAL_PRIMARY_KEY_NOT_RETIRED: %',item.relid;
        END IF;
    END LOOP;
END
$$;
COMMIT;
