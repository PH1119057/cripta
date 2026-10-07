BEGIN;

-- Stage 7C.3: keep durable LIVE-arm evidence vocabulary aligned with
-- TRADING_CONTOUR §4.7 / live_arm_readiness.REQUIRED_LIVE_ARM_CHECKS.
-- This migration does not arm trading or alter evidence rows.

ALTER TABLE control.live_arm_evidence
    DROP CONSTRAINT IF EXISTS live_arm_evidence_check_code_check;

ALTER TABLE control.live_arm_evidence
    ADD CONSTRAINT live_arm_evidence_check_code_check
    CHECK (check_code IN (
        'CANON_CURRENT',
        'REMOTE_COMMIT_VERIFIED',
        'SOURCE_LIVE_IDENTITY',
        'TESTS',
        'LIVE_EQUIVALENCE',
        'PAPER_REAL_DECISION_PARITY',
        'EXCHANGE_ACCOUNT_IDENTITY',
        'POSITION_MODE_FRESH',
        'POSITION_IDX_EXPECTED',
        'PHYSICAL_SLOT_CLAIM_CONTRACT',
        'CAPITAL_RESERVATION_CONTRACT',
        'EXACT_STRATEGY_ACTIVATION',
        'ENTRY_PLAN_EXECUTABLE',
        'EXIT_PLAN_EXECUTABLE',
        'INITIAL_PROTECTION_EXECUTABLE',
        'TERMINAL_LOSS_CONTAINMENT_PATH',
        'EMERGENCY_POLICY_SUPPORTED',
        'LIFECYCLE_SUPERVISOR_BEHAVIOR',
        'CRITICAL_FAULT_DELIVERY',
        'RECONCILIATION_PATH',
        'MAINNET_GATE_EXPLICIT_OWNER_APPROVAL',
        'MICRO_LIVE_LIMITS',
        'ROLLBACK_OR_KILL_PATH'
    ));

COMMIT;
