BEGIN;

-- Owner-approved 2026-10-04 scoped R1 MICRO_LIVE waiver.
-- This changes only the durable evidence vocabulary; it does not arm trading.

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
        'OWNER_WAIVED_FOR_R1_MICRO_LIVE'
    ));

COMMIT;
