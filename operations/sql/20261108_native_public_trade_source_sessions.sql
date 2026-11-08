-- #514. One physical independent native publicTrade producer, never an Entry owner.
-- Append-only trade bytes remain in analytics.native_public_trade_events.
-- This metadata records the separate authentic WebSocket session and the
-- last accepted source cursor in the SAME transaction as accepted trade rows.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '20s';

CREATE TABLE IF NOT EXISTS analytics.native_public_trade_source_sessions (
    source_session_id text PRIMARY KEY
        CHECK (source_session_id ~ '^[0-9a-f]{32}$'),
    market_source_id text NOT NULL
        CHECK (length(market_source_id) BETWEEN 1 AND 128),
    producer_release_commit text NOT NULL
        CHECK (producer_release_commit ~ '^[0-9a-f]{40}$'),
    state text NOT NULL CHECK (state IN ('ACTIVE', 'GAP')),
    subscribed_at timestamptz NOT NULL,
    last_accepted_at timestamptz,
    ended_at timestamptz,
    reason text CHECK (reason IS NULL OR length(reason) BETWEEN 1 AND 128)
);

CREATE INDEX IF NOT EXISTS native_public_trade_source_session_time_idx
    ON analytics.native_public_trade_source_sessions(subscribed_at DESC);

CREATE TABLE IF NOT EXISTS analytics.native_public_trade_source_cursors (
    source_session_id text NOT NULL
        REFERENCES analytics.native_public_trade_source_sessions(source_session_id),
    market_source_id text NOT NULL,
    instrument_id text NOT NULL,
    external_symbol text NOT NULL,
    frame_number bigint NOT NULL CHECK (frame_number > 0),
    frame_index integer NOT NULL CHECK (frame_index >= 0),
    exchange_sequence bigint NOT NULL CHECK (exchange_sequence >= 0),
    exec_id text NOT NULL CHECK(length(exec_id) BETWEEN 1 AND 128),
    event_at timestamptz NOT NULL,
    PRIMARY KEY (source_session_id,external_symbol)
);

REVOKE ALL ON analytics.native_public_trade_source_sessions FROM PUBLIC;
REVOKE ALL ON analytics.native_public_trade_source_cursors FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON analytics.native_public_trade_source_sessions
    TO cripta;
GRANT SELECT,INSERT,UPDATE ON analytics.native_public_trade_source_cursors
    TO cripta;
REVOKE DELETE,TRUNCATE ON analytics.native_public_trade_source_sessions
    FROM cripta;
REVOKE DELETE,TRUNCATE ON analytics.native_public_trade_source_cursors
    FROM cripta;

DO $guard$
BEGIN
    IF has_table_privilege('cripta',
       'analytics.native_public_trade_source_sessions','DELETE')
       OR has_table_privilege('cripta',
       'analytics.native_public_trade_source_cursors','DELETE')
       OR has_table_privilege('cripta',
       'analytics.native_public_trade_events','UPDATE')
       OR has_table_privilege('cripta',
       'analytics.native_public_trade_events','DELETE')
    THEN
       RAISE EXCEPTION 'NATIVE_SOURCE_APPEND_ONLY_PRIVILEGE_DRIFT';
    END IF;
END
$guard$;
COMMIT;
