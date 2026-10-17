BEGIN;

SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '120s';

CREATE SCHEMA IF NOT EXISTS analytics AUTHORIZATION postgres;
GRANT USAGE ON SCHEMA analytics TO cripta;

CREATE UNIQUE INDEX IF NOT EXISTS identity_market_sources_venue_pair
    ON identity.market_sources(market_source_id, venue_id);
CREATE UNIQUE INDEX IF NOT EXISTS identity_instruments_venue_product_symbol
    ON identity.instruments(
        instrument_id, venue_id, product_category, external_symbol
    );

CREATE TABLE IF NOT EXISTS analytics.market_minutes (
    observed_minute timestamptz NOT NULL,
    source_observed_at timestamptz NOT NULL,
    venue_id text NOT NULL,
    market_source_id text NOT NULL,
    instrument_id text NOT NULL,
    external_symbol text NOT NULL,
    product_category text NOT NULL CHECK (product_category IN ('SPOT','LINEAR')),
    return_5m_pct double precision,
    mid_price double precision,
    buy_usd double precision,
    sell_usd double precision,
    net_usd double precision,
    turnover_usd double precision,
    large_buy_usd double precision,
    large_sell_usd double precision,
    bid_usd double precision,
    ask_usd double precision,
    bid_change_pct double precision,
    ask_change_pct double precision,
    open_interest double precision,
    open_interest_change_pct double precision,
    funding_rate double precision,
    last_price double precision,
    mark_price double precision,
    index_price double precision,
    long_ratio double precision,
    short_ratio double precision,
    trade_quality text,
    trade_age_seconds double precision,
    book_quality text,
    book_age_seconds double precision,
    ticker_quality text,
    ticker_age_seconds double precision,
    transport_quality text,
    instrument_availability text,
    source_snapshot_id bigint NOT NULL,
    architecture_version text NOT NULL,
    engine_version text NOT NULL,
    feature_version text NOT NULL,
    config_fingerprint text NOT NULL,
    schema_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market_source_id, instrument_id, observed_minute),
    FOREIGN KEY (market_source_id, venue_id)
        REFERENCES identity.market_sources(market_source_id, venue_id),
    FOREIGN KEY (
        instrument_id, venue_id, product_category, external_symbol
    )
        REFERENCES identity.instruments(
            instrument_id, venue_id, product_category, external_symbol
        ),
    CHECK (observed_minute = date_trunc('minute', observed_minute)),
    CHECK (date_trunc('minute', source_observed_at) = observed_minute),
    CHECK (
        product_category = 'LINEAR'
        OR (
            open_interest IS NULL
            AND open_interest_change_pct IS NULL
            AND funding_rate IS NULL
            AND last_price IS NULL
            AND mark_price IS NULL
            AND index_price IS NULL
            AND long_ratio IS NULL
            AND short_ratio IS NULL
            AND ticker_quality IS NULL
            AND ticker_age_seconds IS NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS analytics_market_minutes_instrument_at
    ON analytics.market_minutes(instrument_id, observed_minute DESC);

CREATE INDEX IF NOT EXISTS analytics_market_minutes_source_at
    ON analytics.market_minutes(market_source_id, observed_minute DESC);

REVOKE ALL ON analytics.market_minutes FROM PUBLIC;
GRANT SELECT, INSERT ON analytics.market_minutes TO cripta;

COMMIT;
