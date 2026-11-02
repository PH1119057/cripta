-- Owner-approved canonical recovery for a P90 account-level private-WS
-- Entry safety latch.  This control never opens a gate, creates permission,
-- creates LIVE arms, or mutates Exchange.  It can delete only the exact
-- PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN latch after independently obtained
-- signed-flat evidence has been bound to a fresh COMPLETE account generation.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

DO $role$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname='cripta_account_entry_safety_recovery'
  ) THEN
    CREATE ROLE cripta_account_entry_safety_recovery NOLOGIN;
  END IF;
END;
$role$;

GRANT USAGE ON SCHEMA runtime,control,strategy_entry,identity
  TO cripta_account_entry_safety_recovery;
GRANT SELECT ON runtime.account_entry_safety_blocks,
                runtime.account_state_generations,
                runtime.trade_commands,
                runtime.lifecycle_faults,
                control.execution_gates,
                control.live_arm_sessions,
                control.account_live_arm_sessions,
                strategy_entry.execution_permissions,
                identity.trading_accounts,
                identity.account_connectors
  TO cripta_account_entry_safety_recovery;
GRANT DELETE ON runtime.account_entry_safety_blocks
  TO cripta_account_entry_safety_recovery;
-- PostgreSQL SELECT ... FOR UPDATE requires UPDATE privilege on the
-- locked table even if the function never executes an UPDATE statement.
-- The owner role is NOLOGIN and the callable SECURITY DEFINER function
-- remains narrowly scoped to deleting the exact approved safety latch.
GRANT UPDATE ON runtime.account_entry_safety_blocks
  TO cripta_account_entry_safety_recovery;

CREATE OR REPLACE FUNCTION control.recover_p90_account_ws_safety(
    p_account_id text,
    p_connector_id text,
    p_release_commit text,
    p_generation_id text,
    p_signed_uid text,
    p_recovered_at timestamptz
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $recover$
DECLARE
  v_reason text;
  v_blocked_at timestamptz;
  v_completed_at timestamptz;
  v_positions integer;
  v_orders integer;
  v_identity text;
BEGIN
  IF session_user <> 'cripta'
     OR current_setting('transaction_isolation') <> 'serializable'
     OR p_release_commit !~ '^[0-9a-f]{40}$'
     OR p_generation_id IS NULL OR btrim(p_generation_id)=''
     OR p_signed_uid !~ '^[0-9]+$'
     OR p_recovered_at IS NULL
  THEN
    RAISE EXCEPTION 'P90_ACCOUNT_WS_RECOVERY_SCOPE_INVALID';
  END IF;

  IF (p_account_id,p_connector_id) NOT IN (
      ('ta-2122c81ecff14f3b85b102db605212f7',
       'ac-ed73de20410548d6841aeafa5f196f70'),
      ('ta-853ac5fcd58185697396d33cd57459fa',
       'ac-ec57819f875f1bfd929a382d6dc8a125')
  ) THEN
    RAISE EXCEPTION 'P90_ACCOUNT_WS_RECOVERY_IDENTITY_DRIFT';
  END IF;

  IF EXISTS (
       SELECT 1 FROM control.execution_gates
        WHERE mode IN ('mainnet','testnet') AND enabled<>0
     )
     OR EXISTS (
       SELECT 1 FROM strategy_entry.execution_permissions WHERE enabled=true
     )
     OR EXISTS (
       SELECT 1 FROM control.live_arm_sessions WHERE state='ACTIVE'
     )
     OR EXISTS (
       SELECT 1 FROM control.account_live_arm_sessions WHERE state='ACTIVE'
     )
  THEN
    RAISE EXCEPTION 'P90_ACCOUNT_WS_RECOVERY_NOT_DISARMED';
  END IF;

  SELECT reason,blocked_at
    INTO v_reason,v_blocked_at
    FROM runtime.account_entry_safety_blocks
   WHERE trading_account_id=p_account_id
     AND account_connector_id=p_connector_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RETURN p_account_id;
  END IF;
  IF v_reason IS DISTINCT FROM 'PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN'
     OR v_blocked_at > p_recovered_at
  THEN
    RAISE EXCEPTION 'P90_ACCOUNT_WS_RECOVERY_REASON_NOT_APPROVED';
  END IF;

  SELECT a.external_account_ref
    INTO v_identity
    FROM identity.trading_accounts a
    JOIN identity.account_connectors c
      ON c.trading_account_id=a.trading_account_id
   WHERE a.trading_account_id=p_account_id
     AND a.environment='MAINNET'
     AND a.status='ACTIVE'
     AND a.account_type='UNIFIED'
     AND a.venue_id='venue-bybit'
     AND c.account_connector_id=p_connector_id
     AND c.status='ACTIVE'
     AND c.retired_at IS NULL
     AND c.adapter_id='BYBIT_V5_PRIVATE'
     AND c.connector_kind='CEX_PRIVATE_RUNTIME';
  IF NOT FOUND OR v_identity IS DISTINCT FROM p_signed_uid THEN
    RAISE EXCEPTION 'P90_ACCOUNT_WS_RECOVERY_SIGNED_UID_MISMATCH';
  END IF;

  SELECT completed_at,positions_count,active_orders_count
    INTO v_completed_at,v_positions,v_orders
    FROM runtime.account_state_generations
   WHERE generation_id=p_generation_id
     AND trading_account_id=p_account_id
     AND account_connector_id=p_connector_id
     AND account_ref='BYBIT:UNIFIED'
     AND identity_status='PROVEN'
     AND state='COMPLETE'
     AND completed_at IS NOT NULL;
  IF NOT FOUND
     OR v_completed_at < p_recovered_at - interval '15 seconds'
     OR v_completed_at > p_recovered_at + interval '1 second'
     OR coalesce(v_positions,-1) <> 0
     OR coalesce(v_orders,-1) <> 0
  THEN
    RAISE EXCEPTION 'P90_ACCOUNT_WS_RECOVERY_GENERATION_NOT_PROVEN';
  END IF;

  IF EXISTS (
    SELECT 1 FROM runtime.trade_commands
     WHERE trading_account_id=p_account_id
       AND state NOT IN ('completed','failed','cancelled','rejected')
  ) THEN
    RAISE EXCEPTION 'P90_ACCOUNT_WS_RECOVERY_PENDING_COMMAND';
  END IF;

  IF EXISTS (
    SELECT 1 FROM runtime.lifecycle_faults
     WHERE state='OPEN' AND severity='CRITICAL'
       AND (
         NULLIF(exact_ids->>'trading_account_id','') IS NULL
         OR exact_ids->>'trading_account_id'=p_account_id
       )
  ) THEN
    RAISE EXCEPTION 'P90_ACCOUNT_WS_RECOVERY_CRITICAL_FAULT';
  END IF;

  DELETE FROM runtime.account_entry_safety_blocks
   WHERE trading_account_id=p_account_id
     AND account_connector_id=p_connector_id
     AND reason='PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'P90_ACCOUNT_WS_RECOVERY_DELETE_FAILED';
  END IF;

  RETURN p_account_id;
END;
$recover$;

ALTER FUNCTION control.recover_p90_account_ws_safety(
  text,text,text,text,text,timestamptz
) OWNER TO cripta_account_entry_safety_recovery;

REVOKE ALL ON FUNCTION control.recover_p90_account_ws_safety(
  text,text,text,text,text,timestamptz
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.recover_p90_account_ws_safety(
  text,text,text,text,text,timestamptz
) TO cripta;

COMMIT;
