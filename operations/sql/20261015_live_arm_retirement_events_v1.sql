BEGIN;
CREATE TABLE IF NOT EXISTS control.live_arm_retirement_events (
    event_id text PRIMARY KEY,
    live_arm_session_id text NOT NULL UNIQUE
        REFERENCES control.live_arm_sessions(live_arm_session_id),
    previous_release_commit text NOT NULL,
    loaded_commit text NOT NULL,
    reason text NOT NULL,
    retired_at timestamptz NOT NULL,
    evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence)='object')
);
REVOKE ALL ON control.live_arm_retirement_events FROM PUBLIC;
GRANT SELECT,INSERT ON control.live_arm_retirement_events TO cripta;
COMMIT;
