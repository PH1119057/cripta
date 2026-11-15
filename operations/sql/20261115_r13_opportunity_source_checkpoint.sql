-- R13 #470: durable source identity for counterfactual Opportunity replay.
-- Source-only migration.  No market writer, Strategy permission or trading right.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

CREATE SCHEMA IF NOT EXISTS monitoring;

CREATE TABLE IF NOT EXISTS monitoring.opportunity_source_checkpoints (
    signal_id text PRIMARY KEY,
    market_source_id text NOT NULL
      CHECK (market_source_id='BYBIT_PUBLIC_V5_MAINNET'),
    source_session_id text NOT NULL,
    producer_release_commit text NOT NULL
      CHECK (producer_release_commit ~ '^[0-9a-f]{40}$'),
    frame_number bigint NOT NULL CHECK (frame_number >= 0),
    frame_index integer NOT NULL CHECK (frame_index >= 0),
    source_quality text NOT NULL CHECK (source_quality IN ('CURRENT','GAP')),
    last_event_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

REVOKE ALL ON monitoring.opportunity_source_checkpoints FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON monitoring.opportunity_source_checkpoints TO cripta;
REVOKE DELETE,TRUNCATE ON monitoring.opportunity_source_checkpoints FROM cripta;

DO $check$
BEGIN
    IF NOT has_table_privilege(
        'cripta','monitoring.opportunity_source_checkpoints','SELECT'
    ) OR NOT has_table_privilege(
        'cripta','monitoring.opportunity_source_checkpoints','INSERT'
    ) OR NOT has_table_privilege(
        'cripta','monitoring.opportunity_source_checkpoints','UPDATE'
    ) OR has_table_privilege(
        'cripta','monitoring.opportunity_source_checkpoints','DELETE'
    ) THEN
        RAISE EXCEPTION 'R13_OPPORTUNITY_CHECKPOINT_ACL_DRIFT';
    END IF;
END $check$;
COMMIT;
