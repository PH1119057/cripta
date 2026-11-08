-- #470: original Bybit WebSocket frame order before independent P90 consumption.
-- The existing per-symbol cursor is ONLY the tail, never full event-order audit.
-- Producer inserts this index in the SAME transaction as immutable trade+journal
-- and cursor. No Strategy/Entry/Exit or Gate/OWNER ARM write is authorized.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '20s';

CREATE TABLE IF NOT EXISTS analytics.native_public_trade_source_events (
    source_session_id text NOT NULL REFERENCES
        analytics.native_public_trade_source_sessions(source_session_id),
    frame_number bigint NOT NULL CHECK (frame_number > 0),
    frame_index integer NOT NULL CHECK (frame_index >= 0),
    market_source_id text NOT NULL,
    instrument_id text NOT NULL,
    external_symbol text NOT NULL CHECK (length(external_symbol) BETWEEN 1 AND 64),
    exchange_sequence bigint NOT NULL CHECK (exchange_sequence >= 0),
    exec_id text NOT NULL CHECK (length(exec_id) BETWEEN 1 AND 128),
    event_at timestamptz NOT NULL,
    accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (source_session_id,frame_number,frame_index),
    UNIQUE (
        source_session_id,market_source_id,instrument_id,
        exchange_sequence,exec_id
    ),
    FOREIGN KEY (
        market_source_id,instrument_id,exchange_sequence,exec_id
    ) REFERENCES analytics.native_public_trade_events(
        market_source_id,instrument_id,exchange_sequence,exec_id
    )
);

CREATE INDEX IF NOT EXISTS native_source_events_symbol_order_idx
ON analytics.native_public_trade_source_events(
    source_session_id,external_symbol,frame_number,frame_index
);

REVOKE ALL ON analytics.native_public_trade_source_events FROM PUBLIC;
GRANT SELECT,INSERT ON analytics.native_public_trade_source_events TO cripta;
REVOKE UPDATE,DELETE,TRUNCATE ON analytics.native_public_trade_source_events
FROM cripta;

DO $native$
BEGIN
    IF NOT has_table_privilege(
        'cripta','analytics.native_public_trade_source_events','INSERT'
    ) OR NOT has_table_privilege(
        'cripta','analytics.native_public_trade_source_events','SELECT'
    ) OR has_table_privilege(
        'cripta','analytics.native_public_trade_source_events','UPDATE'
    ) OR has_table_privilege(
        'cripta','analytics.native_public_trade_source_events','DELETE'
    ) THEN
        RAISE EXCEPTION 'NATIVE_SOURCE_EVENT_ORDER_PRIVILEGE_DRIFT';
    END IF;
END
$native$;
COMMIT;
