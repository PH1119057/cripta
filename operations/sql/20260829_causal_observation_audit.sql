BEGIN;

CREATE TABLE IF NOT EXISTS runtime.trade_settings_history(
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    changed_at_epoch_ms BIGINT NOT NULL,
    old_settings JSONB NOT NULL,
    new_settings JSONB NOT NULL,
    source TEXT NOT NULL,
    origin TEXT NOT NULL,
    settings_version TEXT NOT NULL
);

ALTER TABLE runtime.entry_decisions
    ADD COLUMN IF NOT EXISTS entry_policy TEXT NOT NULL DEFAULT 'base_entry_v1',
    ADD COLUMN IF NOT EXISTS policy_version TEXT NOT NULL DEFAULT 'entry-policy-v1',
    ADD COLUMN IF NOT EXISTS settings_version TEXT NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS mayak_snapshot_id BIGINT,
    ADD COLUMN IF NOT EXISTS mayak_snapshot_time TIMESTAMPTZ;

ALTER TABLE mayak_v2.snapshots
    ADD COLUMN IF NOT EXISTS snapshot_kind TEXT NOT NULL DEFAULT 'LEGACY',
    ADD COLUMN IF NOT EXISTS regular_minute TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS mayak_v2_one_regular_per_minute
    ON mayak_v2.snapshots(regular_minute)
    WHERE snapshot_kind='REGULAR';

ALTER TABLE mayak_v2.events
    ADD COLUMN IF NOT EXISTS link_quality TEXT NOT NULL DEFAULT 'LEGACY_UNVERIFIED',
    ADD COLUMN IF NOT EXISTS link_provenance JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMIT;
