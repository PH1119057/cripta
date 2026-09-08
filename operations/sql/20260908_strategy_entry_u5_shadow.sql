BEGIN;

ALTER TABLE strategy_entry.shadow_parity_runs
    ADD COLUMN IF NOT EXISTS entry_plan_fingerprint text,
    ADD COLUMN IF NOT EXISTS calibration_sha256 text,
    ADD COLUMN IF NOT EXISTS calibration_size integer,
    ADD COLUMN IF NOT EXISTS fact_source_id text,
    ADD COLUMN IF NOT EXISTS service_instance_id text;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM strategy_entry.shadow_parity_runs
        WHERE entry_plan_fingerprint IS NULL
           OR calibration_sha256 IS NULL
           OR calibration_size IS NULL
           OR fact_source_id IS NULL
           OR service_instance_id IS NULL
    ) THEN
        RAISE EXCEPTION 'existing shadow parity rows lack exact U5 identity; no heuristic backfill allowed';
    END IF;
END;
$$;

ALTER TABLE strategy_entry.shadow_parity_runs
    ALTER COLUMN entry_plan_fingerprint SET NOT NULL,
    ALTER COLUMN calibration_sha256 SET NOT NULL,
    ALTER COLUMN calibration_size SET NOT NULL,
    ALTER COLUMN fact_source_id SET NOT NULL,
    ALTER COLUMN service_instance_id SET NOT NULL,
    ALTER COLUMN finished_at DROP NOT NULL;

ALTER TABLE strategy_entry.shadow_parity_runs
    DROP CONSTRAINT IF EXISTS shadow_parity_runs_status_check,
    DROP CONSTRAINT IF EXISTS shadow_parity_runs_check;

ALTER TABLE strategy_entry.shadow_parity_runs
    ADD CONSTRAINT shadow_parity_runs_status_check CHECK (
        status IN ('WARMUP', 'PARITY_COMPARABLE', 'PASS', 'FAIL', 'NOT_COMPARABLE')
    ),
    ADD CONSTRAINT shadow_parity_runs_finished_check CHECK (
        finished_at IS NULL OR finished_at >= started_at
    ),
    ADD CONSTRAINT shadow_parity_runs_calibration_sha_check CHECK (
        calibration_sha256 ~ '^[0-9a-f]{64}$'
    ),
    ADD CONSTRAINT shadow_parity_runs_calibration_size_check CHECK (
        calibration_size > 0
    );

ALTER TABLE strategy_entry.shadow_parity_runs
    DROP CONSTRAINT IF EXISTS shadow_parity_runs_entry_plan_identity_fkey;
ALTER TABLE strategy_entry.shadow_parity_runs
    ADD CONSTRAINT shadow_parity_runs_entry_plan_identity_fkey
    FOREIGN KEY (
        entry_plan_fingerprint,
        strategy_id,
        strategy_version,
        strategy_config_fingerprint
    ) REFERENCES strategy_entry.entry_plans(
        entry_plan_fingerprint,
        strategy_id,
        strategy_version,
        strategy_config_fingerprint
    );

ALTER TABLE strategy_entry.shadow_parity_events
    ADD COLUMN IF NOT EXISTS category text,
    ADD COLUMN IF NOT EXISTS observed_at timestamptz,
    ADD COLUMN IF NOT EXISTS source_refs jsonb,
    ADD COLUMN IF NOT EXISTS strategy_config_fingerprint text,
    ADD COLUMN IF NOT EXISTS entry_plan_fingerprint text;

UPDATE strategy_entry.shadow_parity_events AS event
SET category = COALESCE(event.category, 'LEGACY_PARITY_EVENT'),
    observed_at = COALESCE(event.observed_at, event.event_at),
    source_refs = COALESCE(event.source_refs, '[]'::jsonb),
    strategy_config_fingerprint = COALESCE(
        event.strategy_config_fingerprint,
        run.strategy_config_fingerprint
    ),
    entry_plan_fingerprint = COALESCE(
        event.entry_plan_fingerprint,
        run.entry_plan_fingerprint
    )
FROM strategy_entry.shadow_parity_runs AS run
WHERE event.parity_run_id = run.parity_run_id
  AND (
      event.category IS NULL
      OR event.observed_at IS NULL
      OR event.source_refs IS NULL
      OR event.strategy_config_fingerprint IS NULL
      OR event.entry_plan_fingerprint IS NULL
  );

ALTER TABLE strategy_entry.shadow_parity_events
    ALTER COLUMN category SET NOT NULL,
    ALTER COLUMN observed_at SET NOT NULL,
    ALTER COLUMN source_refs SET NOT NULL,
    ALTER COLUMN strategy_config_fingerprint SET NOT NULL,
    ALTER COLUMN entry_plan_fingerprint SET NOT NULL;

ALTER TABLE strategy_entry.shadow_parity_events
    ADD CONSTRAINT shadow_parity_events_source_refs_check CHECK (
        jsonb_typeof(source_refs) = 'array'
    );

CREATE OR REPLACE FUNCTION strategy_entry.guard_shadow_parity_run_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status IN ('PASS', 'FAIL', 'NOT_COMPARABLE') THEN
        RAISE EXCEPTION 'final shadow parity run is immutable';
    END IF;

    IF ROW(
        OLD.parity_run_id,
        OLD.strategy_id,
        OLD.strategy_version,
        OLD.strategy_config_fingerprint,
        OLD.entry_plan_fingerprint,
        OLD.calibration_sha256,
        OLD.calibration_size,
        OLD.baseline_source_commit,
        OLD.universal_source_commit,
        OLD.fact_source_id,
        OLD.service_instance_id,
        OLD.started_at,
        OLD.created_at
    ) IS DISTINCT FROM ROW(
        NEW.parity_run_id,
        NEW.strategy_id,
        NEW.strategy_version,
        NEW.strategy_config_fingerprint,
        NEW.entry_plan_fingerprint,
        NEW.calibration_sha256,
        NEW.calibration_size,
        NEW.baseline_source_commit,
        NEW.universal_source_commit,
        NEW.fact_source_id,
        NEW.service_instance_id,
        NEW.started_at,
        NEW.created_at
    ) THEN
        RAISE EXCEPTION 'shadow parity run identity is immutable';
    END IF;

    IF OLD.status <> NEW.status AND NOT (
        (OLD.status = 'WARMUP' AND NEW.status IN ('PARITY_COMPARABLE', 'NOT_COMPARABLE'))
        OR
        (OLD.status = 'PARITY_COMPARABLE' AND NEW.status IN ('PASS', 'FAIL', 'NOT_COMPARABLE'))
    ) THEN
        RAISE EXCEPTION 'invalid shadow parity run status transition % -> %', OLD.status, NEW.status;
    END IF;

    IF NEW.status IN ('WARMUP', 'PARITY_COMPARABLE') AND NEW.finished_at IS NOT NULL THEN
        RAISE EXCEPTION 'active shadow parity run cannot have finished_at';
    END IF;
    IF NEW.status IN ('PASS', 'FAIL', 'NOT_COMPARABLE') AND NEW.finished_at IS NULL THEN
        RAISE EXCEPTION 'final shadow parity run requires finished_at';
    END IF;
    IF jsonb_typeof(NEW.summary) <> 'object' THEN
        RAISE EXCEPTION 'shadow parity run summary must be an object';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS shadow_parity_runs_immutable
    ON strategy_entry.shadow_parity_runs;
DROP TRIGGER IF EXISTS shadow_parity_runs_guard_update
    ON strategy_entry.shadow_parity_runs;
CREATE TRIGGER shadow_parity_runs_guard_update
BEFORE UPDATE ON strategy_entry.shadow_parity_runs
FOR EACH ROW EXECUTE FUNCTION strategy_entry.guard_shadow_parity_run_update();

DROP TRIGGER IF EXISTS shadow_parity_runs_delete_immutable
    ON strategy_entry.shadow_parity_runs;
CREATE TRIGGER shadow_parity_runs_delete_immutable
BEFORE DELETE ON strategy_entry.shadow_parity_runs
FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();

DROP TRIGGER IF EXISTS shadow_parity_events_immutable
    ON strategy_entry.shadow_parity_events;
CREATE TRIGGER shadow_parity_events_immutable
BEFORE UPDATE OR DELETE ON strategy_entry.shadow_parity_events
FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();

REVOKE UPDATE ON strategy_entry.shadow_parity_runs FROM cripta;
GRANT UPDATE (status, finished_at, summary)
ON strategy_entry.shadow_parity_runs TO cripta;
REVOKE DELETE ON strategy_entry.shadow_parity_runs FROM cripta;
REVOKE UPDATE, DELETE ON strategy_entry.shadow_parity_events FROM cripta;

COMMIT;
