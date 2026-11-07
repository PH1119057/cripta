-- #470: common exact native publicTrade evidence, independent of Strategy/Account.
-- This table is inert until the reviewed MarketSource producer is connected.
-- PostgreSQL migration actor: postgres; operational producer DB role: cripta.
-- Existing P90 rolling same-sequence witness and causal checkpoints stay intact.
-- No automatic retention purge: live position/Exit source pins require proof.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '20s';

CREATE TABLE IF NOT EXISTS analytics.native_public_trade_events (
    market_source_id text NOT NULL CHECK(length(market_source_id) BETWEEN 1 AND 128),
    instrument_id text NOT NULL CHECK(length(instrument_id) BETWEEN 1 AND 128),
    external_symbol text NOT NULL CHECK(length(external_symbol) BETWEEN 1 AND 64),
    exchange_sequence bigint NOT NULL CHECK(exchange_sequence >= 0),
    exec_id text NOT NULL CHECK(length(exec_id) BETWEEN 1 AND 128),
    event_at timestamptz NOT NULL,
    price numeric NOT NULL CHECK(price > 0),
    quantity numeric NOT NULL CHECK(quantity > 0),
    taker_side text NOT NULL CHECK(taker_side IN ('Buy', 'Sell')),
    source_transport text NOT NULL CHECK(length(source_transport) BETWEEN 1 AND 128),
    producer_release_commit text NOT NULL
        CHECK(producer_release_commit ~ '^[0-9a-f]{40}$'),
    ingested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (market_source_id, instrument_id, exchange_sequence, exec_id)
);

CREATE INDEX IF NOT EXISTS native_public_trade_events_replay_idx
    ON analytics.native_public_trade_events
    (market_source_id, instrument_id, event_at, exchange_sequence, exec_id);

REVOKE ALL ON analytics.native_public_trade_events FROM PUBLIC;
GRANT SELECT, INSERT ON analytics.native_public_trade_events TO cripta;
REVOKE UPDATE, DELETE, TRUNCATE ON analytics.native_public_trade_events FROM cripta;

DO $verify$
BEGIN
    IF NOT has_table_privilege('cripta','analytics.native_public_trade_events','SELECT,INSERT')
       OR has_table_privilege('cripta','analytics.native_public_trade_events','UPDATE')
       OR has_table_privilege('cripta','analytics.native_public_trade_events','DELETE')
       OR has_table_privilege('cripta','analytics.native_public_trade_events','TRUNCATE')
    THEN
        RAISE EXCEPTION 'NATIVE_PUBLIC_TRADE_APPEND_ONLY_PERMISSIONS_INVALID';
    END IF;
END
$verify$;
COMMIT;
