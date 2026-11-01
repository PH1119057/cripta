-- Owner decision 2026-11-01: exact subordinate account-allocation exception for
-- the owner-controlled full-contour diagnostic only.
--
-- This migration does NOT create a Strategy, binding, execution permission,
-- LIVE arm or Exchange mutation.  It only extends the binding registry's
-- allocation vocabulary and keeps ordinary DEDICATED_STRATEGY exclusivity.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

ALTER TABLE strategy_entry.strategy_execution_bindings
  DROP CONSTRAINT IF EXISTS strategy_execution_bindings_allocation_mode_check;

ALTER TABLE strategy_entry.strategy_execution_bindings
  ADD CONSTRAINT strategy_execution_bindings_allocation_mode_check
  CHECK (allocation_mode IN ('DEDICATED_STRATEGY','SUBORDINATE_DIAGNOSTIC'))
  NOT VALID;

ALTER TABLE strategy_entry.strategy_execution_bindings
  VALIDATE CONSTRAINT strategy_execution_bindings_allocation_mode_check;

CREATE UNIQUE INDEX IF NOT EXISTS
  ux_strategy_execution_bindings_subordinate_diagnostic_account
ON strategy_entry.strategy_execution_bindings(trading_account_id)
WHERE binding_state='ACTIVE'
  AND allocation_mode='SUBORDINATE_DIAGNOSTIC';

COMMIT;
