-- #470/1: transport-only durable PG-delivery cursor for native MarketSource.
-- Original producer journal stays immutable; this is NOT a P90 Entry/Exit
-- checkpoint, a Strategy permission, nor continuity across source GAPs.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

CREATE TABLE IF NOT EXISTS analytics.native_public_trade_observer_delivery (
    consumer_id text NOT NULL
      CHECK (consumer_id = 'UNIVERSAL_ENTRY_OBSERVER_NATIVE_PG_V1'),
    source_session_id text NOT NULL REFERENCES
      analytics.native_public_trade_source_sessions(source_session_id),
    producer_release_commit text NOT NULL
      CHECK (producer_release_commit ~ '^[0-9a-f]{40}$'),
    frame_number bigint NOT NULL CHECK (frame_number >= 0),
    frame_index integer NOT NULL CHECK (frame_index >= 0),
    last_exec_id text,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (consumer_id,source_session_id)
);

REVOKE ALL ON analytics.native_public_trade_observer_delivery FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON
    analytics.native_public_trade_observer_delivery TO cripta;
REVOKE DELETE,TRUNCATE ON
    analytics.native_public_trade_observer_delivery FROM cripta;

DO $check$
BEGIN
    IF NOT has_table_privilege(
        'cripta','analytics.native_public_trade_observer_delivery','SELECT'
    ) OR NOT has_table_privilege(
        'cripta','analytics.native_public_trade_observer_delivery','INSERT'
    ) OR NOT has_table_privilege(
        'cripta','analytics.native_public_trade_observer_delivery','UPDATE'
    ) OR has_table_privilege(
        'cripta','analytics.native_public_trade_observer_delivery','DELETE'
    ) THEN
        RAISE EXCEPTION 'NATIVE_OBSERVER_DELIVERY_ACL_DRIFT';
    END IF;
END $check$;
COMMIT;
