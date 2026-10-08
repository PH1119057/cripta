BEGIN;

-- Independent operator-safety incident. It is NOT a position lifecycle fault.
CREATE TABLE IF NOT EXISTS control.release_arm_incidents (
    incident_id text PRIMARY KEY,
    release_commit text NOT NULL,
    reason text NOT NULL,
    affected jsonb NOT NULL,
    state text NOT NULL,
    detected_at timestamptz NOT NULL,
    last_checked_at timestamptz NOT NULL,
    resolved_at timestamptz,
    CHECK (state IN ('OPEN','RESOLVED')),
    CHECK (jsonb_typeof(affected)='object'),
    CHECK ((state='OPEN' AND resolved_at IS NULL) OR
           (state='RESOLVED' AND resolved_at IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS
    ux_release_arm_incidents_one_open_release
ON control.release_arm_incidents(release_commit)
WHERE state='OPEN';

REVOKE ALL ON control.release_arm_incidents FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON control.release_arm_incidents TO cripta;
REVOKE DELETE ON control.release_arm_incidents FROM cripta;

COMMIT;
