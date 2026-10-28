-- Issue #243 owner decision: preserve original P90L90 market data while
-- separating immutable source lineage from each exact executable release.
-- Applied only by postgres migration actor under verified release installer.
-- Additive DDL. NEVER UPDATE/DELETE/TRUNCATE historical candles/checkpoints.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30s';

ALTER TABLE strategy_entry.p90l90_causal_closed_bars
    ADD COLUMN IF NOT EXISTS producer_release_commit text;
ALTER TABLE strategy_entry.p90l90_causal_transport_checkpoints
    ADD COLUMN IF NOT EXISTS last_writer_release_commit text;

DO $constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid='strategy_entry.p90l90_causal_closed_bars'::regclass
           AND conname='p90l90_bar_producer_release_exact'
    ) THEN
        ALTER TABLE strategy_entry.p90l90_causal_closed_bars
            ADD CONSTRAINT p90l90_bar_producer_release_exact
                CHECK (producer_release_commit IS NULL OR
                       (producer_release_commit ~ '^[0-9a-f]{40}$'
                        AND producer_release_commit=source_commit));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid='strategy_entry.p90l90_causal_transport_checkpoints'::regclass
           AND conname='p90l90_checkpoint_writer_release_exact'
    ) THEN
        ALTER TABLE strategy_entry.p90l90_causal_transport_checkpoints
            ADD CONSTRAINT p90l90_checkpoint_writer_release_exact
                CHECK (last_writer_release_commit IS NULL OR
                       last_writer_release_commit ~ '^[0-9a-f]{40}$');
    END IF;
END $constraints$;

CREATE TABLE IF NOT EXISTS strategy_entry.p90l90_data_lineage_compatibility (
    data_lineage_id text NOT NULL
        CHECK (data_lineage_id ~ '^p90l90-causal-v1-[0-9a-f]{64}$'),
    loaded_commit text NOT NULL CHECK (loaded_commit ~ '^[0-9a-f]{40}$'),
    origin_commit text NOT NULL CHECK (origin_commit ~ '^[0-9a-f]{40}$'),
    archive_sha256 text NOT NULL CHECK (archive_sha256 ~ '^[0-9a-f]{64}$'),
    seed_checkpoint_sha256 text NOT NULL
        CHECK (seed_checkpoint_sha256 ~ '^[0-9a-f]{64}$'),
    original_bar_count integer NOT NULL CHECK (original_bar_count>0),
    contract_version text NOT NULL CHECK (contract_version='P90L90_CAUSAL_LINEAGE_V1'),
    approved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (data_lineage_id,loaded_commit)
);
REVOKE ALL ON strategy_entry.p90l90_data_lineage_compatibility FROM PUBLIC;
GRANT SELECT,INSERT ON strategy_entry.p90l90_data_lineage_compatibility TO cripta;
REVOKE UPDATE,DELETE,TRUNCATE ON
    strategy_entry.p90l90_data_lineage_compatibility FROM cripta;

DO $permissions$
BEGIN
    IF NOT has_table_privilege(
        'cripta','strategy_entry.p90l90_data_lineage_compatibility','SELECT,INSERT'
    ) OR has_table_privilege(
        'cripta','strategy_entry.p90l90_data_lineage_compatibility','UPDATE'
    ) OR has_table_privilege(
        'cripta','strategy_entry.p90l90_data_lineage_compatibility','DELETE'
    ) OR NOT has_table_privilege(
        'cripta','strategy_entry.p90l90_causal_closed_bars','SELECT,INSERT'
    ) OR has_table_privilege(
        'cripta','strategy_entry.p90l90_causal_closed_bars','UPDATE'
    ) THEN
        RAISE EXCEPTION 'P90_LINEAGE_ACTOR_GRANTS_NOT_FAIL_CLOSED';
    END IF;
END $permissions$;
COMMIT;
