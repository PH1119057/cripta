BEGIN;

-- R5 preparation only.
-- Keep all legacy PRIMARY KEY constraints in place. These account-scoped
-- UNIQUE indexes are additive prerequisites for the later R9/R10 cutover.
-- NULLS NOT DISTINCT preserves the one legacy-unknown physical identity while
-- allowing the same provider-local id to exist under different TradingAccounts.

SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '120s';

DO $$
DECLARE
    item record;
BEGIN
    FOR item IN
        SELECT * FROM (VALUES
            ('runtime.wallet_latest'::regclass, 'wallet_latest'),
            ('runtime.hot_positions'::regclass, 'hot_positions'),
            ('runtime.hot_orders'::regclass, 'hot_orders'),
            ('runtime.executions'::regclass, 'executions'),
            ('runtime.exchange_order_history'::regclass, 'exchange_order_history')
        ) AS v(relid,prefix)
    LOOP
        IF NOT EXISTS (
            SELECT 1
              FROM pg_constraint
             WHERE conrelid=item.relid
               AND conname=item.prefix || '_account_identity_consistency_check'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %s ADD CONSTRAINT %I CHECK ('
                || '(identity_status=''PROVEN'' '
                || 'AND trading_account_id IS NOT NULL '
                || 'AND account_connector_id IS NOT NULL) OR '
                || '(identity_status=''LEGACY_UNKNOWN'' '
                || 'AND trading_account_id IS NULL '
                || 'AND account_connector_id IS NULL)'
                || ') NOT VALID',
                item.relid,
                item.prefix || '_account_identity_consistency_check'
            );
        END IF;
    END LOOP;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_wallet_latest_trading_account_singleton
    ON runtime.wallet_latest(trading_account_id, singleton)
    NULLS NOT DISTINCT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_hot_positions_trading_account_slot
    ON runtime.hot_positions(trading_account_id, symbol, position_idx)
    NULLS NOT DISTINCT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_hot_orders_trading_account_order
    ON runtime.hot_orders(trading_account_id, order_id)
    NULLS NOT DISTINCT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_executions_trading_account_exec
    ON runtime.executions(trading_account_id, exec_id)
    NULLS NOT DISTINCT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_exchange_order_history_trading_account_order
    ON runtime.exchange_order_history(trading_account_id, order_id)
    NULLS NOT DISTINCT;

COMMIT;
