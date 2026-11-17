-- ARCH §59.1 / TC §4.17.1 / Issue #511.
-- Exact recovery for a durable account latch created by a PROVEN pre-send
-- Entry denial. This control never mutates Exchange, opens a financial gate,
-- grants execution permission, or creates LIVE arm sessions.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

CREATE TABLE IF NOT EXISTS control.pre_send_account_recovery_events (
    event_id text PRIMARY KEY,
    request_id text NOT NULL UNIQUE,
    operator text NOT NULL,
    trading_account_id text NOT NULL,
    account_connector_id text NOT NULL,
    release_commit text NOT NULL,
    command_id text NOT NULL,
    reservation_id text NOT NULL,
    blocked_at timestamptz NOT NULL,
    recovered_at timestamptz NOT NULL,
    evidence jsonb NOT NULL CHECK(jsonb_typeof(evidence)='object')
);
REVOKE ALL ON control.pre_send_account_recovery_events FROM PUBLIC;
GRANT SELECT ON control.pre_send_account_recovery_events TO cripta;
GRANT INSERT,SELECT ON control.pre_send_account_recovery_events
    TO cripta_account_entry_safety_recovery;

CREATE OR REPLACE FUNCTION control.reject_pre_send_recovery_history_change()
RETURNS trigger LANGUAGE plpgsql AS $immutable$
BEGIN
  RAISE EXCEPTION 'PRE_SEND_RECOVERY_AUDIT_IMMUTABLE';
END;
$immutable$;
DROP TRIGGER IF EXISTS pre_send_recovery_immutable
    ON control.pre_send_account_recovery_events;
CREATE TRIGGER pre_send_recovery_immutable BEFORE UPDATE OR DELETE
ON control.pre_send_account_recovery_events FOR EACH ROW
EXECUTE FUNCTION control.reject_pre_send_recovery_history_change();
DROP TRIGGER IF EXISTS pre_send_recovery_no_truncate
    ON control.pre_send_account_recovery_events;
CREATE TRIGGER pre_send_recovery_no_truncate BEFORE TRUNCATE
ON control.pre_send_account_recovery_events FOR EACH STATEMENT
EXECUTE FUNCTION control.reject_pre_send_recovery_history_change();

GRANT SELECT ON runtime.account_entry_safety_blocks,
                runtime.account_state_generations,
                runtime.trade_commands,
                runtime.capital_reservations,
                runtime.position_ownership,
                runtime.executions,
                runtime.exchange_order_history,
                runtime.hot_orders,
                runtime.lifecycle_faults,
                control.execution_gates,
                control.live_arm_sessions,
                control.account_live_arm_sessions,
                strategy_entry.execution_permissions,
                identity.trading_accounts,
                identity.account_connectors
    TO cripta_account_entry_safety_recovery;

CREATE OR REPLACE FUNCTION control.recover_p90_pre_send_account_safety(
    p_account text,
    p_connector text,
    p_release text,
    p_command text,
    p_generation text,
    p_signed_uid text,
    p_blocked_at timestamptz,
    p_proof_at timestamptz,
    p_operator text,
    p_request text
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path=pg_catalog,pg_temp
AS $recover$
DECLARE
  v_latch record;
  v_command record;
  v_generation record;
  v_reservation record;
  v_now timestamptz := clock_timestamp();
  v_event text;
  v_expected_strategy text;
  v_expected_binding text;
  v_reservation_id text;
  v_candidate_count integer;
BEGIN
  IF session_user <> 'cripta'
     OR current_setting('transaction_isolation') <> 'serializable'
     OR p_release !~ '^[0-9a-f]{40}$'
     OR p_command !~ '^[A-Za-z0-9_-]{8,128}$'
     OR p_generation IS NULL OR btrim(p_generation)=''
     OR p_signed_uid !~ '^[0-9]+$'
     OR p_blocked_at IS NULL OR p_proof_at IS NULL
     OR p_operator IS DISTINCT FROM 'owner'
     OR p_request !~ '^[A-Za-z0-9_-]{8,128}$'
     OR p_proof_at < v_now-interval '15 seconds'
     OR p_proof_at > v_now+interval '1 second'
  THEN
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_SCOPE_INVALID';
  END IF;

  IF (p_account,p_connector) = (
      'ta-2122c81ecff14f3b85b102db605212f7',
      'ac-ed73de20410548d6841aeafa5f196f70'
  ) THEN
    v_expected_strategy := 'p90l90_legacy_main_ltc_inj';
    v_expected_binding := 'binding-p90l90-real-main-20261029-v3';
  ELSIF (p_account,p_connector) = (
      'ta-853ac5fcd58185697396d33cd57459fa',
      'ac-ec57819f875f1bfd929a382d6dc8a125'
  ) THEN
    v_expected_strategy := 'p90l90_legacy_sub_dot_arb_apt';
    v_expected_binding := 'binding-p90l90-real-sub-20261029-v3';
  ELSE
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_IDENTITY_SCOPE_INVALID';
  END IF;

  IF NOT EXISTS (
    SELECT 1
      FROM identity.trading_accounts a
      JOIN identity.account_connectors c
        ON c.trading_account_id=a.trading_account_id
     WHERE a.trading_account_id=p_account
       AND a.external_account_ref=p_signed_uid
       AND a.status='ACTIVE'
       AND a.environment='MAINNET'
       AND a.venue_id='venue-bybit'
       AND a.account_type='UNIFIED'
       AND c.account_connector_id=p_connector
       AND c.status='ACTIVE'
       AND c.retired_at IS NULL
       AND c.adapter_id='BYBIT_V5_PRIVATE'
       AND c.connector_kind='CEX_PRIVATE_RUNTIME'
  ) THEN
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_IDENTITY_UNPROVEN';
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(p_account,0));
  PERFORM pg_advisory_xact_lock(hashtext(p_account),hashtext(p_connector));

  v_now := clock_timestamp();
  IF p_proof_at < v_now-interval '15 seconds'
     OR p_proof_at > v_now+interval '1 second'
  THEN
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_PROOF_EXPIRED';
  END IF;

  SELECT * INTO v_latch
    FROM runtime.account_entry_safety_blocks
   WHERE trading_account_id=p_account
     AND account_connector_id=p_connector
   FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('status','ALREADY_CLEAR','account',p_account);
  END IF;
  IF v_latch.reason IS DISTINCT FROM 'PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED'
     OR v_latch.blocked_at IS DISTINCT FROM p_blocked_at
     OR v_latch.blocked_at > p_proof_at
  THEN
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_LATCH_EPOCH_CHANGED';
  END IF;

  IF EXISTS (
    SELECT 1 FROM control.pre_send_account_recovery_events
     WHERE request_id=p_request
  ) THEN
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_REQUEST_REPLAY';
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
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_NOT_DISARMED';
  END IF;

  SELECT * INTO v_generation
    FROM runtime.account_state_generations
   WHERE generation_id=p_generation
     AND trading_account_id=p_account
     AND account_connector_id=p_connector
     AND account_ref='BYBIT:UNIFIED'
     AND identity_status='PROVEN'
     AND state='COMPLETE'
     AND positions_count=0
     AND active_orders_count=0
     AND completed_at BETWEEN v_now-interval '15 seconds' AND v_now+interval '1 second';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_FRESH_FLAT_REQUIRED';
  END IF;

  SELECT count(*) INTO v_candidate_count
    FROM runtime.trade_commands c
   WHERE c.trading_account_id=p_account
     AND c.identity_status='PROVEN'
     AND c.command_type='entry'
     AND c.state='failed'
     AND c.error='PRE_MUTATION_SAFETY_BLOCK:PreMutationSafetyBlock'
     AND c.payload_json::jsonb->>'strategy_id'=v_expected_strategy
     AND c.payload_json::jsonb->>'strategy_version'
           ='2.0-p90l90-real-main-sub-20261028'
     AND c.strategy_execution_binding_id=v_expected_binding
     AND to_timestamp(c.finished_at_epoch_ms/1000.0)
           BETWEEN p_blocked_at-interval '3 seconds'
               AND p_blocked_at+interval '1 second';
  IF v_candidate_count <> 1 THEN
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_COMMAND_EPOCH_AMBIGUOUS';
  END IF;

  SELECT * INTO v_command
    FROM runtime.trade_commands c
   WHERE c.command_id=p_command
     AND c.trading_account_id=p_account
     AND c.identity_status='PROVEN'
     AND c.command_type='entry'
     AND c.state='failed'
     AND c.error='PRE_MUTATION_SAFETY_BLOCK:PreMutationSafetyBlock'
     AND c.payload_json::jsonb->>'strategy_id'=v_expected_strategy
     AND c.payload_json::jsonb->>'strategy_version'
           ='2.0-p90l90-real-main-sub-20261028'
     AND c.strategy_execution_binding_id=v_expected_binding
     AND to_timestamp(c.finished_at_epoch_ms/1000.0)
           BETWEEN p_blocked_at-interval '3 seconds'
               AND p_blocked_at+interval '1 second'
     AND coalesce(c.result_json::jsonb,'{}'::jsonb)='{}'::jsonb;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_COMMAND_UNPROVEN';
  END IF;

  v_reservation_id := NULLIF(v_command.payload_json::jsonb->>'capital_reservation_id','');
  IF v_reservation_id IS NULL THEN
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_RESERVATION_REF_MISSING';
  END IF;

  SELECT * INTO v_reservation
    FROM runtime.capital_reservations r
   WHERE r.reservation_id=v_reservation_id
     AND r.trading_account_id=p_account
     AND r.identity_status='PROVEN'
     AND r.strategy_id=v_expected_strategy
     AND r.strategy_version='2.0-p90l90-real-main-sub-20261028'
     AND r.strategy_execution_binding_id=v_expected_binding
     AND r.state='RELEASED'
     AND r.state_reason='ENTRY_NO_MUTATION:'||v_command.error
     AND r.exchange_commitment_ref IS NULL
     AND r.strategy_position_id IS NULL
     AND r.updated_at BETWEEN p_blocked_at-interval '3 seconds'
                          AND p_blocked_at+interval '1 second';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_NO_MUTATION_RESERVATION_UNPROVEN';
  END IF;

  IF EXISTS (
       SELECT 1 FROM runtime.executions e
        WHERE e.trading_account_id=p_account
          AND e.identity_status='PROVEN'
          AND e.order_link_id IN (p_command,left(p_command,36))
     )
     OR EXISTS (
       SELECT 1 FROM runtime.exchange_order_history h
        WHERE h.trading_account_id=p_account
          AND h.identity_status='PROVEN'
          AND h.order_link_id IN (p_command,left(p_command,36))
     )
     OR EXISTS (
       SELECT 1 FROM runtime.hot_orders h
        WHERE h.trading_account_id=p_account
          AND h.identity_status='PROVEN'
          AND h.order_link_id IN (p_command,left(p_command,36))
     )
     OR EXISTS (
       SELECT 1 FROM runtime.position_ownership p
        WHERE p.trading_account_id=p_account
          AND p.entry_command_id=p_command
     )
  THEN
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_EXCHANGE_EFFECT_OBSERVED';
  END IF;

  IF EXISTS (
       SELECT 1 FROM runtime.position_ownership p
        WHERE p.trading_account_id=p_account AND p.state<>'CLOSED'
     )
     OR EXISTS (
       SELECT 1 FROM runtime.trade_commands c
        WHERE c.trading_account_id=p_account
          AND c.state NOT IN ('completed','failed','cancelled','rejected')
     )
     OR EXISTS (
       SELECT 1 FROM runtime.capital_reservations r
        WHERE r.trading_account_id=p_account
          AND r.state IN ('RESERVED','DISPATCHED','PENDING_EXCHANGE_REFLECTION',
                          'RECONCILIATION_REQUIRED')
     )
  THEN
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_CURRENT_AMBIGUITY';
  END IF;

  IF EXISTS (
    SELECT 1
      FROM runtime.lifecycle_faults f
      LEFT JOIN runtime.position_ownership p
        ON p.position_id=NULLIF(f.exact_ids->>'strategy_position_id','')
     WHERE f.state='OPEN'
       AND f.severity='CRITICAL'
       AND (
         NULLIF(f.exact_ids->>'trading_account_id','') IS NULL
         OR f.exact_ids->>'trading_account_id'=p_account
       )
       AND p.state IS DISTINCT FROM 'CLOSED'
  ) THEN
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_CURRENT_FAULT';
  END IF;

  v_now := clock_timestamp();
  IF p_proof_at < v_now-interval '15 seconds'
  THEN
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_PROOF_EXPIRED';
  END IF;

  v_event := 'pre-send-recovery-' ||
    md5(p_account||p_request||p_command||p_blocked_at::text);
  INSERT INTO control.pre_send_account_recovery_events(
      event_id,request_id,operator,trading_account_id,account_connector_id,
      release_commit,command_id,reservation_id,blocked_at,recovered_at,evidence)
  VALUES(
      v_event,p_request,p_operator,p_account,p_connector,p_release,p_command,
      v_reservation_id,p_blocked_at,v_now,
      jsonb_build_object(
        'old_latch',to_jsonb(v_latch),
        'proof_source','bybit:signed-flat-linear-usdt:exact-account',
        'proof_observed_at',p_proof_at,
        'generation_id',p_generation,
        'signed_uid',p_signed_uid,
        'command_error',v_command.error,
        'command_finished_at_epoch_ms',v_command.finished_at_epoch_ms,
        'reservation_state',v_reservation.state,
        'reservation_reason',v_reservation.state_reason,
        'exchange_commitment_ref',v_reservation.exchange_commitment_ref,
        'exchange_effect_observed',false
      )
  );

  DELETE FROM runtime.account_entry_safety_blocks
   WHERE trading_account_id=p_account
     AND account_connector_id=p_connector
     AND blocked_at=p_blocked_at
     AND reason='PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'PRE_SEND_RECOVERY_LATCH_EPOCH_CHANGED';
  END IF;

  RETURN jsonb_build_object(
      'status','RECOVERED',
      'event_id',v_event,
      'account',p_account,
      'command_id',p_command
  );
END;
$recover$;

ALTER FUNCTION control.recover_p90_pre_send_account_safety(
  text,text,text,text,text,text,timestamptz,timestamptz,text,text
) OWNER TO cripta_account_entry_safety_recovery;
REVOKE ALL ON FUNCTION control.recover_p90_pre_send_account_safety(
  text,text,text,text,text,text,timestamptz,timestamptz,text,text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.recover_p90_pre_send_account_safety(
  text,text,text,text,text,text,timestamptz,timestamptz,text,text
) TO cripta;

COMMIT;
