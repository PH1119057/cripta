-- Issue #243, owner decision 2026-10-28. One common physical closed-OHLC source.
-- MarketSource + InstrumentIdentity, never Strategy or TradingAccount.
-- Frozen original P90L90 seed remains separate immutable audit evidence only.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE IF NOT EXISTS analytics.closed_candles (
    market_source_id text NOT NULL,
    venue_id text NOT NULL,
    instrument_id text NOT NULL,
    external_symbol text NOT NULL,
    product_category text NOT NULL CHECK (product_category IN ('SPOT','LINEAR')),
    timeframe text NOT NULL CHECK (timeframe IN
        ('1','3','5','15','30','60','120','240')),
    opened_at timestamptz NOT NULL,
    closed_at timestamptz NOT NULL,
    open numeric NOT NULL CHECK (open > 0),
    high numeric NOT NULL CHECK (high > 0),
    low numeric NOT NULL CHECK (low > 0),
    close numeric NOT NULL CHECK (close > 0),
    volume numeric NOT NULL CHECK (volume >= 0),
    source_transport text NOT NULL CHECK (source_transport IN
        ('BYBIT_REST_KLINE','BYBIT_WS_KLINE')),
    producer_release_commit text NOT NULL
        CHECK (producer_release_commit ~ '^[0-9a-f]{40}$'),
    observed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market_source_id, instrument_id, timeframe, closed_at),
    FOREIGN KEY (market_source_id, venue_id)
        REFERENCES identity.market_sources(market_source_id, venue_id),
    FOREIGN KEY (instrument_id, venue_id, product_category, external_symbol)
        REFERENCES identity.instruments(
            instrument_id, venue_id, product_category, external_symbol
        ),
    CHECK (opened_at < closed_at),
    CHECK (closed_at = opened_at + timeframe::integer * interval '1 minute'),
    CHECK (high >= greatest(open, close, low)),
    CHECK (low <= least(open, close, high)),
    CHECK (date_trunc('minute', closed_at) = closed_at),
    CHECK (date_trunc('minute', opened_at) = opened_at)
);
CREATE INDEX IF NOT EXISTS analytics_closed_candles_symbol_interval
    ON analytics.closed_candles(
        market_source_id, instrument_id, timeframe, closed_at DESC
    );

REVOKE ALL ON analytics.closed_candles FROM PUBLIC;
GRANT SELECT, INSERT ON analytics.closed_candles TO cripta;
REVOKE UPDATE, DELETE, TRUNCATE ON analytics.closed_candles FROM cripta;
DO $grants$
BEGIN
    IF NOT has_table_privilege('cripta','analytics.closed_candles','SELECT,INSERT')
      OR has_table_privilege('cripta','analytics.closed_candles','UPDATE')
      OR has_table_privilege('cripta','analytics.closed_candles','DELETE')
      OR has_table_privilege('cripta','analytics.closed_candles','TRUNCATE')
    THEN RAISE EXCEPTION 'CLOSED_CANDLE_OWNER_GRANTS_NOT_FAIL_CLOSED';
    END IF;
END $grants$;
COMMIT;
