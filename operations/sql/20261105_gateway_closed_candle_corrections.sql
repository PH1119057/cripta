-- ARCH58 / owner-approved gateway-ready release. postgres migration only.
-- Filesystem writes NONE. DB writes: additive table/view + ONE exact repair.
-- Originals, causal checkpoints, orders, arms and gates are never modified.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE IF NOT EXISTS analytics.closed_candle_corrections (
    correction_id text PRIMARY KEY,
    market_source_id text NOT NULL,
    instrument_id text NOT NULL,
    timeframe text NOT NULL,
    closed_at timestamptz NOT NULL,
    original_record jsonb NOT NULL,
    open numeric NOT NULL CHECK (open > 0),
    high numeric NOT NULL CHECK (high > 0),
    low numeric NOT NULL CHECK (low > 0),
    close numeric NOT NULL CHECK (close > 0),
    volume numeric NOT NULL CHECK (volume >= 0),
    evidence jsonb NOT NULL,
    approved_source text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (market_source_id,instrument_id,timeframe,closed_at),
    FOREIGN KEY (market_source_id,instrument_id,timeframe,closed_at)
        REFERENCES analytics.closed_candles,
    CHECK (high >= greatest(open,close,low)),
    CHECK (low <= least(open,close,high))
);
REVOKE ALL ON analytics.closed_candle_corrections FROM PUBLIC,cripta;
GRANT SELECT ON analytics.closed_candle_corrections TO cripta;

CREATE OR REPLACE VIEW analytics.closed_candles_effective AS
SELECT c.market_source_id,c.venue_id,c.instrument_id,c.external_symbol,
       c.product_category,c.timeframe,c.opened_at,c.closed_at,
       coalesce(r.open,c.open) AS open,coalesce(r.high,c.high) AS high,
       coalesce(r.low,c.low) AS low,coalesce(r.close,c.close) AS close,
       coalesce(r.volume,c.volume) AS volume,c.source_transport,
       c.producer_release_commit,c.observed_at,c.created_at,r.correction_id
  FROM analytics.closed_candles c
  LEFT JOIN analytics.closed_candle_corrections r
    USING (market_source_id,instrument_id,timeframe,closed_at);
REVOKE ALL ON analytics.closed_candles_effective FROM PUBLIC,cripta;
GRANT SELECT ON analytics.closed_candles_effective TO cripta;

DO $repair$
DECLARE original analytics.closed_candles%ROWTYPE;
BEGIN
    SELECT * INTO original FROM analytics.closed_candles
     WHERE market_source_id='ms-bybit-mainnet-public-v1'
       AND instrument_id='inst-bybit-linear-aptusdt' AND timeframe='5'
       AND closed_at='2026-11-04T22:05:00Z';
    -- A new/disposable database does not fabricate a missing source record.
    IF NOT FOUND THEN RETURN; END IF;
    IF original.opened_at <> '2026-11-04T22:00:00Z'
       OR original.open <> .6391 OR original.high <> .6395
       OR original.low <> .638 OR original.close <> .6393
       OR original.volume <> 16987.93
       OR original.observed_at <> '2026-11-04T22:05:00.172217Z'
       OR original.producer_release_commit <> '768b2423e89f161b2195526f34e5320dc0687a84'
       OR original.source_transport <> 'BYBIT_REST_KLINE'
    THEN RAISE EXCEPTION 'CANDLE_REPAIR_ORIGINAL_PRECONDITION_DRIFT'; END IF;
    IF EXISTS (SELECT 1 FROM analytics.closed_candle_corrections
                WHERE correction_id='apt-5m-20261104T220500-finality-v1'
                  AND original_record=to_jsonb(original)
                  AND open=.6391 AND high=.6395 AND low=.638
                  AND close=.6394 AND volume=17006.02)
    THEN RETURN; END IF;
    IF EXISTS (SELECT 1 FROM control.execution_gates WHERE mode='mainnet' AND enabled<>0)
       OR EXISTS (SELECT 1 FROM runtime.position_ownership
                   WHERE state IN ('OPEN','RECONCILIATION_REQUIRED'))
       OR EXISTS (SELECT 1 FROM runtime.trade_commands WHERE state IN ('queued','running'))
    THEN RAISE EXCEPTION 'CANDLE_REPAIR_REQUIRES_DISARMED_FLAT_RUNTIME'; END IF;
    INSERT INTO analytics.closed_candle_corrections
        (correction_id,market_source_id,instrument_id,timeframe,closed_at,
         original_record,open,high,low,close,volume,evidence,approved_source)
    VALUES ('apt-5m-20261104T220500-finality-v1',original.market_source_id,
            original.instrument_id,original.timeframe,original.closed_at,
            to_jsonb(original),.6391,.6395,.638,.6394,17006.02,
            '{"provider":"BYBIT_REST_KLINE","category":"linear","symbol":"APTUSDT",
              "interval":"5","start":1793829600000,"end":1793829899999,
              "server_confirmations_ms":[1793834968459,1793834969978],
              "same_confirmed_row":["1793829600000","0.6391","0.6395","0.638","0.6394","17006.02","10863.389367"],
              "reason":"boundary-time provisional REST fact persisted as final"}'::jsonb,
            'owner-20261105-gateway-plan:docs-443db72f');
END $repair$;

DO $grants$
BEGIN
    IF NOT has_table_privilege('cripta','analytics.closed_candles_effective','SELECT')
       OR has_table_privilege('cripta','analytics.closed_candle_corrections','INSERT')
       OR has_table_privilege('cripta','analytics.closed_candle_corrections','UPDATE')
       OR has_table_privilege('cripta','analytics.closed_candle_corrections','DELETE')
       OR has_table_privilege('cripta','analytics.closed_candles','UPDATE')
       OR has_table_privilege('cripta','analytics.closed_candles','DELETE')
    THEN RAISE EXCEPTION 'CANDLE_REPAIR_GRANTS_NOT_FAIL_CLOSED'; END IF;
END $grants$;
COMMIT;
