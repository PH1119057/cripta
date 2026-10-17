BEGIN;

SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

WITH symbols(symbol) AS (
    SELECT unnest(ARRAY['AAVEUSDT','ADAUSDT','APTUSDT','ARBUSDT','AVAXUSDT','BCHUSDT','BNBUSDT','BTCUSDT','DOTUSDT','ETHUSDT','HBARUSDT','INJUSDT','LINKUSDT','LTCUSDT','OPUSDT','SOLUSDT','SUIUSDT','TRXUSDT','UNIUSDT','XRPUSDT']::text[])
), expected AS (
    SELECT
        'inst-bybit-spot-' || lower(symbol) AS instrument_id,
        'venue-bybit'::text AS venue_id,
        'SPOT'::text AS product_category,
        symbol AS external_symbol,
        regexp_replace(symbol, 'USDT$', '') AS base_asset,
        'USDT'::text AS quote_asset,
        jsonb_build_object(
            'bybit_category','spot',
            'identity_revision','R7a'
        ) AS metadata
    FROM symbols
    UNION ALL
    SELECT
        'inst-bybit-linear-' || lower(symbol),
        'venue-bybit',
        'LINEAR',
        symbol,
        regexp_replace(symbol, 'USDT$', ''),
        'USDT',
        jsonb_build_object(
            'bybit_category','linear',
            'contract_type','LinearPerpetual',
            'settle_asset','USDT',
            'identity_revision','R7a'
        )
    FROM symbols
)
INSERT INTO identity.instruments(
    instrument_id,venue_id,product_category,external_symbol,base_asset,quote_asset,
    status,metadata)
SELECT instrument_id,venue_id,product_category,external_symbol,base_asset,quote_asset,
       'ACTIVE',metadata
FROM expected
ON CONFLICT(instrument_id) DO NOTHING;

DO $$
DECLARE
    invalid_count integer;
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM identity.market_sources
         WHERE market_source_id='ms-bybit-mainnet-public-v1'
           AND venue_id='venue-bybit'
           AND provider_ref='BYBIT_PUBLIC_V5'
           AND environment='MAINNET'
           AND market_scope='SPOT+LINEAR'
           AND status='ACTIVE'
    ) THEN
        RAISE EXCEPTION 'R7A_MARKET_SOURCE_IDENTITY_MISMATCH';
    END IF;

    WITH symbols(symbol) AS (
        SELECT unnest(ARRAY['AAVEUSDT','ADAUSDT','APTUSDT','ARBUSDT','AVAXUSDT','BCHUSDT','BNBUSDT','BTCUSDT','DOTUSDT','ETHUSDT','HBARUSDT','INJUSDT','LINKUSDT','LTCUSDT','OPUSDT','SOLUSDT','SUIUSDT','TRXUSDT','UNIUSDT','XRPUSDT']::text[])
    ), expected AS (
        SELECT 'inst-bybit-spot-' || lower(symbol) AS instrument_id,
               'SPOT'::text AS product_category, symbol AS external_symbol
        FROM symbols
        UNION ALL
        SELECT 'inst-bybit-linear-' || lower(symbol),
               'LINEAR', symbol
        FROM symbols
    )
    SELECT count(*) INTO invalid_count
      FROM expected e
      LEFT JOIN identity.instruments i USING(instrument_id)
     WHERE i.instrument_id IS NULL
        OR i.venue_id <> 'venue-bybit'
        OR i.product_category <> e.product_category
        OR i.external_symbol <> e.external_symbol
        OR i.status <> 'ACTIVE';

    IF invalid_count <> 0 THEN
        RAISE EXCEPTION 'R7A_INSTRUMENT_IDENTITY_MISMATCH count=%', invalid_count;
    END IF;
END
$$;

ALTER TABLE mayak_v2.coin_minutes
    ADD COLUMN IF NOT EXISTS market_source_id text,
    ADD COLUMN IF NOT EXISTS spot_instrument_id text,
    ADD COLUMN IF NOT EXISTS linear_instrument_id text;

ALTER TABLE mayak_v2.liquidations
    ADD COLUMN IF NOT EXISTS market_source_id text,
    ADD COLUMN IF NOT EXISTS instrument_id text;

ALTER TABLE mayak_v2.coin_market_contexts
    ADD COLUMN IF NOT EXISTS market_source_id text,
    ADD COLUMN IF NOT EXISTS spot_instrument_id text,
    ADD COLUMN IF NOT EXISTS linear_instrument_id text;

ALTER TABLE mayak_v2.shared_market_contexts
    ADD COLUMN IF NOT EXISTS market_source_id text;

ALTER TABLE dispatcher_v2.global_market_contexts
    ADD COLUMN IF NOT EXISTS market_source_id text;

ALTER TABLE dispatcher_v2.coin_market_contexts
    ADD COLUMN IF NOT EXISTS market_source_id text,
    ADD COLUMN IF NOT EXISTS spot_instrument_id text,
    ADD COLUMN IF NOT EXISTS linear_instrument_id text;

DO $$
DECLARE
    item record;
BEGIN
    FOR item IN
        SELECT * FROM (VALUES
            ('mayak_v2.coin_minutes'::regclass, 'coin_minutes', 'market_source_id'),
            ('mayak_v2.liquidations'::regclass, 'liquidations', 'market_source_id'),
            ('mayak_v2.coin_market_contexts'::regclass, 'coin_market_contexts', 'market_source_id'),
            ('mayak_v2.shared_market_contexts'::regclass, 'shared_market_contexts', 'market_source_id'),
            ('dispatcher_v2.global_market_contexts'::regclass, 'global_market_contexts', 'market_source_id'),
            ('dispatcher_v2.coin_market_contexts'::regclass, 'dispatcher_coin_market_contexts', 'market_source_id')
        ) AS v(relid,prefix,column_name)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid=item.relid
               AND conname=item.prefix || '_market_source_id_fkey'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %s ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES identity.market_sources(market_source_id) NOT VALID',
                item.relid,
                item.prefix || '_market_source_id_fkey',
                item.column_name
            );
        END IF;
    END LOOP;
END
$$;

DO $$
DECLARE
    item record;
BEGIN
    FOR item IN
        SELECT * FROM (VALUES
            ('mayak_v2.coin_minutes'::regclass, 'coin_minutes', 'spot_instrument_id', 'linear_instrument_id'),
            ('mayak_v2.coin_market_contexts'::regclass, 'coin_market_contexts', 'spot_instrument_id', 'linear_instrument_id'),
            ('dispatcher_v2.coin_market_contexts'::regclass, 'dispatcher_coin_market_contexts', 'spot_instrument_id', 'linear_instrument_id')
        ) AS v(relid,prefix,spot_column,linear_column)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid=item.relid
               AND conname=item.prefix || '_spot_instrument_id_fkey'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %s ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES identity.instruments(instrument_id) NOT VALID',
                item.relid,
                item.prefix || '_spot_instrument_id_fkey',
                item.spot_column
            );
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid=item.relid
               AND conname=item.prefix || '_linear_instrument_id_fkey'
        ) THEN
            EXECUTE format(
                'ALTER TABLE %s ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES identity.instruments(instrument_id) NOT VALID',
                item.relid,
                item.prefix || '_linear_instrument_id_fkey',
                item.linear_column
            );
        END IF;
    END LOOP;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid='mayak_v2.liquidations'::regclass
           AND conname='liquidations_instrument_id_fkey'
    ) THEN
        ALTER TABLE mayak_v2.liquidations
            ADD CONSTRAINT liquidations_instrument_id_fkey
            FOREIGN KEY (instrument_id)
            REFERENCES identity.instruments(instrument_id)
            NOT VALID;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS mayak_v2_coin_market_identity_at
    ON mayak_v2.coin_market_contexts(
        market_source_id,linear_instrument_id,observed_at DESC
    );

CREATE INDEX IF NOT EXISTS dispatcher_v2_coin_market_identity_at
    ON dispatcher_v2.coin_market_contexts(
        market_source_id,linear_instrument_id,observed_at DESC
    );

COMMIT;
