-- Issue #415: narrow independent SUB WS technical recovery, not OWNER ARM.
-- The established owner-only DISARMED/flat recovery function is unchanged.
-- Executed once per exact verified release by postgres migration actor.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '20s';

-- A repeated INSERT while a transient latch already exists MUST advance its
-- blocking epoch. Otherwise a concurrent recovery can erase a newer fault.
-- No new UPDATE/DELETE grant is given to the private worker (cripta).
CREATE OR REPLACE FUNCTION control.refresh_account_entry_safety_block()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $latch$
BEGIN
  IF NEW.trading_account_id IS NULL
     OR NEW.account_connector_id IS NULL
     OR length(NEW.reason) NOT BETWEEN 1 AND 300
  THEN
    RAISE EXCEPTION 'ACCOUNT_ENTRY_SAFETY_LATCH_INVALID';
  END IF;

  UPDATE runtime.account_entry_safety_blocks AS old
     SET blocked_at = clock_timestamp(),
         reason = CASE
           WHEN old.reason = 'PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN'
            AND NEW.reason <> 'PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN'
           THEN NEW.reason
           ELSE old.reason
         END
   WHERE old.trading_account_id = NEW.trading_account_id
     AND old.account_connector_id = NEW.account_connector_id;
  IF FOUND THEN
    -- Original INSERT ... ON CONFLICT DO NOTHING remains an INSERT-only API.
    RETURN NULL;
  END IF;
  RETURN NEW;
END;
$latch$;

ALTER FUNCTION control.refresh_account_entry_safety_block()
OWNER TO cripta_account_entry_safety_recovery;
REVOKE ALL ON FUNCTION control.refresh_account_entry_safety_block() FROM PUBLIC;

CREATE OR REPLACE TRIGGER trg_refresh_account_entry_safety_block
BEFORE INSERT ON runtime.account_entry_safety_blocks
FOR EACH ROW EXECUTE FUNCTION control.refresh_account_entry_safety_block();

CREATE TABLE IF NOT EXISTS runtime.account_ws_technical_resume_events (
    event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trading_account_id text NOT NULL,
    account_connector_id text NOT NULL,
    release_commit text NOT NULL,
    generation_id text NOT NULL,
    reconnect_event_id bigint NOT NULL,
    blocked_at timestamptz NOT NULL,
    proof_observed_at timestamptz NOT NULL,
    recovered_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

GRANT SELECT ON runtime.connection_events,
                runtime.entry_consumer_health,
                runtime.position_ownership
TO cripta_account_entry_safety_recovery;
GRANT INSERT ON runtime.account_ws_technical_resume_events
TO cripta_account_entry_safety_recovery;
GRANT USAGE, SELECT ON SEQUENCE
  runtime.account_ws_technical_resume_events_event_id_seq
TO cripta_account_entry_safety_recovery;

CREATE OR REPLACE FUNCTION control.clear_sub_ws_transient_latch(
    p_account_id text,
    p_connector_id text,
    p_release_commit text,
    p_generation_id text,
    p_signed_uid text,
    p_reconnect_event_id bigint,
    p_expected_blocked_at timestamptz,
    p_proof_observed_at timestamptz
)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $resume$
DECLARE
  v_blocked_at timestamptz;
  v_reason text;
  v_registered_uid text;
  v_generation_completed_at timestamptz;
  v_positions integer;
  v_orders integer;
  v_event_at_ms bigint;
BEGIN
  -- This is a technical account-scope control, not a new OWNER authorization.
  IF session_user <> 'cripta'
    OR current_setting('transaction_isolation') <> 'serializable'
    OR (p_account_id,p_connector_id) <>
      ('ta-853ac5fcd58185697396d33cd57459fa',
       'ac-ec57819f875f1bfd929a382d6dc8a125')
    OR p_release_commit !~ '^[0-9a-f]{40}$'
    OR p_signed_uid !~ '^[0-9]+$'
    OR p_generation_id IS NULL OR btrim(p_generation_id) = ''
    OR p_reconnect_event_id IS NULL OR p_reconnect_event_id <= 0
    OR p_expected_blocked_at IS NULL OR p_proof_observed_at IS NULL
  THEN
    RAISE EXCEPTION 'SUB_WS_TECHNICAL_RESUME_SCOPE_UNVERIFIED';
  END IF;

  -- Lock the exact block before reviewing proof; the BEFORE INSERT trigger
  -- also UPDATE-locks this row on every subsequent disconnect.
  SELECT blocked_at,reason INTO v_blocked_at,v_reason
    FROM runtime.account_entry_safety_blocks
   WHERE trading_account_id=p_account_id
     AND account_connector_id=p_connector_id
   FOR UPDATE;
  IF NOT FOUND OR v_reason IS DISTINCT FROM
    'PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN'
    OR v_blocked_at IS DISTINCT FROM p_expected_blocked_at
  THEN
    RAISE EXCEPTION 'SUB_WS_TECHNICAL_RESUME_LATCH_CHANGED';
  END IF;

  IF p_proof_observed_at > clock_timestamp()
     OR p_proof_observed_at < clock_timestamp() - interval '15 seconds'
     OR p_proof_observed_at < v_blocked_at
  THEN
    RAISE EXCEPTION 'SUB_WS_TECHNICAL_RESUME_PROOF_STALE';
  END IF;

  -- At most same-exact-release owner authorization may be present.
  -- DISARMED remains DISARMED; this function never writes gate/arms/rights.
  IF EXISTS (
    SELECT 1 FROM control.live_arm_sessions
     WHERE state='ACTIVE' AND release_commit <> p_release_commit
  ) OR EXISTS (
    SELECT 1 FROM control.account_live_arm_sessions
     WHERE state='ACTIVE' AND release_commit <> p_release_commit
  ) OR (
    EXISTS (SELECT 1 FROM control.execution_gates
             WHERE mode='mainnet' AND enabled<>0)
    AND NOT EXISTS (
      SELECT 1 FROM control.account_live_arm_sessions
       WHERE state='ACTIVE' AND trading_account_id=p_account_id
         AND account_connector_id=p_connector_id
         AND release_commit=p_release_commit
    )
  ) THEN
    RAISE EXCEPTION 'SUB_WS_TECHNICAL_RESUME_OWNER_SCOPE_DRIFT';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM runtime.entry_consumer_health
     WHERE trading_account_id=p_account_id
       AND account_connector_id=p_connector_id
       AND release_commit=p_release_commit
       AND observed_at >= clock_timestamp() - interval '8 seconds'
       AND observed_at <= clock_timestamp()
  ) THEN
    RAISE EXCEPTION 'SUB_WS_TECHNICAL_RESUME_LOADED_RELEASE_UNVERIFIED';
  END IF;

  SELECT a.external_account_ref INTO v_registered_uid
    FROM identity.trading_accounts a
    JOIN identity.account_connectors c
      ON c.trading_account_id=a.trading_account_id
   WHERE a.trading_account_id=p_account_id
     AND c.account_connector_id=p_connector_id
     AND a.environment='MAINNET' AND a.status='ACTIVE'
     AND a.account_type='UNIFIED' AND a.venue_id='venue-bybit'
     AND c.status='ACTIVE' AND c.retired_at IS NULL
     AND c.adapter_id='BYBIT_V5_PRIVATE'
     AND c.connector_kind='CEX_PRIVATE_RUNTIME';
  IF NOT FOUND OR v_registered_uid IS DISTINCT FROM p_signed_uid THEN
    RAISE EXCEPTION 'SUB_WS_TECHNICAL_RESUME_SIGNED_UID_MISMATCH';
  END IF;

  SELECT completed_at,positions_count,active_orders_count
    INTO v_generation_completed_at,v_positions,v_orders
    FROM runtime.account_state_generations
   WHERE generation_id=p_generation_id
     AND trading_account_id=p_account_id
     AND account_connector_id=p_connector_id
     AND account_ref='BYBIT:UNIFIED'
     AND identity_status='PROVEN' AND state='COMPLETE'
     AND completed_at IS NOT NULL;
  IF NOT FOUND OR v_generation_completed_at < v_blocked_at
     OR v_generation_completed_at < clock_timestamp() - interval '15 seconds'
     OR v_generation_completed_at > p_proof_observed_at + interval '1 second'
     OR v_positions IS DISTINCT FROM 0 OR v_orders IS DISTINCT FROM 0
  THEN
    RAISE EXCEPTION 'SUB_WS_TECHNICAL_RESUME_FLAT_GENERATION_UNPROVEN';
  END IF;

  SELECT at_epoch_ms INTO v_event_at_ms
    FROM runtime.connection_events
   WHERE id=p_reconnect_event_id
     AND trading_account_id=p_account_id
     AND account_connector_id=p_connector_id
     AND identity_status='PROVEN' AND channel='private'
     AND event='reconnect_verified';
  IF NOT FOUND OR to_timestamp(v_event_at_ms/1000.0) < v_blocked_at
     OR to_timestamp(v_event_at_ms/1000.0) > p_proof_observed_at
  THEN
    RAISE EXCEPTION 'SUB_WS_TECHNICAL_RESUME_RECONNECT_UNVERIFIED';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM runtime.connection_events
     WHERE trading_account_id=p_account_id
       AND account_connector_id=p_connector_id
       AND channel='private'
       AND event='subscriptions_confirmed'
       AND at_epoch_ms >= extract(epoch from v_blocked_at)*1000
       AND at_epoch_ms <= v_event_at_ms
  ) OR EXISTS (
    SELECT 1 FROM runtime.connection_events
     WHERE trading_account_id=p_account_id
       AND account_connector_id=p_connector_id
       AND channel='private' AND event='reconnecting'
       AND id > p_reconnect_event_id
  ) THEN
    RAISE EXCEPTION 'SUB_WS_TECHNICAL_RESUME_SESSION_NOT_CURRENT';
  END IF;

  IF EXISTS (
    SELECT 1 FROM runtime.trade_commands
     WHERE trading_account_id=p_account_id
       AND state NOT IN ('completed','failed','cancelled','rejected')
  ) OR EXISTS (
    SELECT 1 FROM runtime.position_ownership
     WHERE trading_account_id=p_account_id
       AND (state='OPEN' OR close_link_status='UNRESOLVED_EXACT_LINK')
  ) THEN
    RAISE EXCEPTION 'SUB_WS_TECHNICAL_RESUME_PENDING_EXPOSURE';
  END IF;

  -- Global critical/unknown exposure is a hard gate, even for a flat SUB.
  IF EXISTS (
    SELECT 1 FROM runtime.lifecycle_faults
     WHERE state='OPEN' AND severity='CRITICAL'
  ) THEN
    RAISE EXCEPTION 'SUB_WS_TECHNICAL_RESUME_CRITICAL_LIFECYCLE_FAULT';
  END IF;

  DELETE FROM runtime.account_entry_safety_blocks
   WHERE trading_account_id=p_account_id
     AND account_connector_id=p_connector_id
     AND blocked_at=v_blocked_at
     AND reason='PRIVATE_ACCOUNT_WS_RECOVERY_UNPROVEN';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'SUB_WS_TECHNICAL_RESUME_CONCURRENT_RELATCH';
  END IF;

  INSERT INTO runtime.account_ws_technical_resume_events(
      trading_account_id,account_connector_id,release_commit,generation_id,
      reconnect_event_id,blocked_at,proof_observed_at)
  VALUES(
      p_account_id,p_connector_id,p_release_commit,p_generation_id,
      p_reconnect_event_id,v_blocked_at,p_proof_observed_at);

  RETURN p_account_id;
END;
$resume$;

ALTER FUNCTION control.clear_sub_ws_transient_latch(
  text,text,text,text,text,bigint,timestamptz,timestamptz
) OWNER TO cripta_account_entry_safety_recovery;
REVOKE ALL ON FUNCTION control.clear_sub_ws_transient_latch(
  text,text,text,text,text,bigint,timestamptz,timestamptz
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.clear_sub_ws_transient_latch(
  text,text,text,text,text,bigint,timestamptz,timestamptz
) TO cripta;

COMMIT;
