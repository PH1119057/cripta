BEGIN;

SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '120s';

ALTER TABLE analytics.market_minutes
    ADD COLUMN IF NOT EXISTS flow_5m_prior_turnover_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_5m_acceleration_usd_per_min2 double precision,

    ADD COLUMN IF NOT EXISTS flow_1m_return_pct double precision,
    ADD COLUMN IF NOT EXISTS flow_1m_buy_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_1m_sell_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_1m_net_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_1m_turnover_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_1m_large_buy_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_1m_large_sell_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_1m_prior_turnover_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_1m_acceleration_usd_per_min2 double precision,

    ADD COLUMN IF NOT EXISTS flow_15m_return_pct double precision,
    ADD COLUMN IF NOT EXISTS flow_15m_buy_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_15m_sell_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_15m_net_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_15m_turnover_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_15m_large_buy_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_15m_large_sell_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_15m_prior_turnover_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_15m_acceleration_usd_per_min2 double precision,

    ADD COLUMN IF NOT EXISTS flow_30m_return_pct double precision,
    ADD COLUMN IF NOT EXISTS flow_30m_buy_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_30m_sell_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_30m_net_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_30m_turnover_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_30m_large_buy_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_30m_large_sell_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_30m_prior_turnover_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_30m_acceleration_usd_per_min2 double precision,

    ADD COLUMN IF NOT EXISTS flow_60m_return_pct double precision,
    ADD COLUMN IF NOT EXISTS flow_60m_buy_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_60m_sell_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_60m_net_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_60m_turnover_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_60m_large_buy_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_60m_large_sell_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_60m_prior_turnover_usd double precision,
    ADD COLUMN IF NOT EXISTS flow_60m_acceleration_usd_per_min2 double precision,

    ADD COLUMN IF NOT EXISTS best_bid double precision,
    ADD COLUMN IF NOT EXISTS best_ask double precision,
    ADD COLUMN IF NOT EXISTS bid_depth_5bps_usd double precision,
    ADD COLUMN IF NOT EXISTS ask_depth_5bps_usd double precision,
    ADD COLUMN IF NOT EXISTS bid_depth_10bps_usd double precision,
    ADD COLUMN IF NOT EXISTS ask_depth_10bps_usd double precision,
    ADD COLUMN IF NOT EXISTS bid_depth_25bps_usd double precision,
    ADD COLUMN IF NOT EXISTS ask_depth_25bps_usd double precision,
    ADD COLUMN IF NOT EXISTS bid_depth_50bps_usd double precision,
    ADD COLUMN IF NOT EXISTS ask_depth_50bps_usd double precision,
    ADD COLUMN IF NOT EXISTS bid_change_1m_pct double precision,
    ADD COLUMN IF NOT EXISTS ask_change_1m_pct double precision,
    ADD COLUMN IF NOT EXISTS imbalance_change_1m double precision,
    ADD COLUMN IF NOT EXISTS bid_change_5m_pct double precision,
    ADD COLUMN IF NOT EXISTS ask_change_5m_pct double precision,
    ADD COLUMN IF NOT EXISTS imbalance_change_5m double precision,
    ADD COLUMN IF NOT EXISTS bid_change_15m_pct double precision,
    ADD COLUMN IF NOT EXISTS ask_change_15m_pct double precision,
    ADD COLUMN IF NOT EXISTS imbalance_change_15m double precision,

    ADD COLUMN IF NOT EXISTS open_interest_value double precision,
    ADD COLUMN IF NOT EXISTS open_interest_change_15m_pct double precision,
    ADD COLUMN IF NOT EXISTS open_interest_change_30m_pct double precision,
    ADD COLUMN IF NOT EXISTS open_interest_change_60m_pct double precision,
    ADD COLUMN IF NOT EXISTS open_interest_speed_5m_pct_per_min double precision,
    ADD COLUMN IF NOT EXISTS open_interest_acceleration_5m_pct_per_min2 double precision,
    ADD COLUMN IF NOT EXISTS funding_rate_change_from_previous double precision;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conrelid='analytics.market_minutes'::regclass
           AND conname='market_minutes_r8c_linear_only'
    ) THEN
        ALTER TABLE analytics.market_minutes
            ADD CONSTRAINT market_minutes_r8c_linear_only
            CHECK (
                product_category='LINEAR'
                OR (
                    open_interest_value IS NULL
                    AND open_interest_change_15m_pct IS NULL
                    AND open_interest_change_30m_pct IS NULL
                    AND open_interest_change_60m_pct IS NULL
                    AND open_interest_speed_5m_pct_per_min IS NULL
                    AND open_interest_acceleration_5m_pct_per_min2 IS NULL
                    AND funding_rate_change_from_previous IS NULL
                )
            )
            NOT VALID;
    END IF;
END
$$;

COMMIT;
