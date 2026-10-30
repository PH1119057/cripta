BEGIN;

-- OWNER DECISION 2026-10-30:
-- exact P90 REAL v3 portal-only MICRO_LIVE may arm before OWNER_WEBHOOK exists.
-- This expands only durable evidence vocabulary. It does not arm trading,
-- open the mainnet gate, grant execution permission or suppress fault records.

ALTER TABLE control.live_arm_evidence
    DROP CONSTRAINT IF EXISTS live_arm_evidence_status_check;

ALTER TABLE control.live_arm_evidence
    ADD CONSTRAINT live_arm_evidence_status_check
    CHECK (status IN (
        'PASS',
        'FAIL',
        'UNKNOWN',
        'STALE',
        'NOT_CHECKED_HERE',
        'OWNER_WAIVED_FOR_R1_MICRO_LIVE',
        'OWNER_WAIVED_FOR_P90_PORTAL_MICRO_LIVE'
    ));

COMMIT;
