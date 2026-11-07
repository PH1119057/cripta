-- TC4.17 / ARCH59: owner Control recovery only, never a runtime DELETE grant.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

CREATE TABLE IF NOT EXISTS control.account_mutation_recovery_events (
    event_id text PRIMARY KEY,
    request_id text NOT NULL UNIQUE,
    operator text NOT NULL,
    trading_account_id text NOT NULL,
    account_connector_id text NOT NULL,
    release_commit text NOT NULL,
    strategy_position_id text NOT NULL,
    blocked_at timestamptz NOT NULL,
    recovered_at timestamptz NOT NULL,
    evidence jsonb NOT NULL CHECK(jsonb_typeof(evidence)='object')
);
REVOKE ALL ON control.account_mutation_recovery_events FROM PUBLIC;
GRANT SELECT ON control.account_mutation_recovery_events TO cripta;
GRANT INSERT,SELECT ON control.account_mutation_recovery_events
    TO cripta_account_entry_safety_recovery;
GRANT SELECT ON runtime.position_ownership,runtime.executions,
                runtime.exchange_order_history,runtime.capital_reservations
    TO cripta_account_entry_safety_recovery;

CREATE OR REPLACE FUNCTION control.reject_mutation_recovery_history_change()
RETURNS trigger LANGUAGE plpgsql AS $immutable$
BEGIN
  RAISE EXCEPTION 'MUTATION_RECOVERY_AUDIT_IMMUTABLE';
END;
$immutable$;
DROP TRIGGER IF EXISTS mutation_recovery_immutable ON control.account_mutation_recovery_events;
CREATE TRIGGER mutation_recovery_immutable BEFORE UPDATE OR DELETE
ON control.account_mutation_recovery_events FOR EACH ROW
EXECUTE FUNCTION control.reject_mutation_recovery_history_change();
DROP TRIGGER IF EXISTS mutation_recovery_no_truncate ON control.account_mutation_recovery_events;
CREATE TRIGGER mutation_recovery_no_truncate BEFORE TRUNCATE
ON control.account_mutation_recovery_events FOR EACH STATEMENT
EXECUTE FUNCTION control.reject_mutation_recovery_history_change();

CREATE OR REPLACE FUNCTION control.recover_closed_p90_mutation_safety(
    p_account text,p_connector text,p_release text,p_position text,
    p_generation text,p_signed_uid text,p_blocked_at timestamptz,
    p_proof_at timestamptz,p_operator text,p_request text
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,pg_temp AS $recover$
DECLARE
  v_latch record;
  v_position record;
  v_generation record;
  v_now timestamptz := clock_timestamp();
  v_count integer;
  v_rows integer;
  v_qty numeric;
  v_tp_order_id text;
  v_tp_order_count integer;
  v_close_proof_mode text := 'BOT_COMMAND';
  v_event text;
BEGIN
  IF session_user <> 'cripta'
     OR current_setting('transaction_isolation') <> 'serializable'
     OR p_release !~ '^[0-9a-f]{40}$'
     OR p_request !~ '^[A-Za-z0-9_-]{8,128}$'
     OR length(btrim(p_operator)) NOT BETWEEN 1 AND 160
     OR p_blocked_at IS NULL OR p_proof_at IS NULL
     OR p_signed_uid !~ '^[0-9]+$'
     OR p_proof_at < v_now-interval '15 seconds'
     OR p_proof_at > v_now+interval '1 second'
  THEN RAISE EXCEPTION 'MUTATION_RECOVERY_SCOPE_INVALID'; END IF;
  IF (p_account,p_connector) NOT IN (
      ('ta-2122c81ecff14f3b85b102db605212f7','ac-ed73de20410548d6841aeafa5f196f70'),
      ('ta-853ac5fcd58185697396d33cd57459fa','ac-ec57819f875f1bfd929a382d6dc8a125')
  ) OR NOT EXISTS (
    SELECT 1 FROM identity.trading_accounts a JOIN identity.account_connectors c
      ON c.trading_account_id=a.trading_account_id
     WHERE a.trading_account_id=p_account AND a.external_account_ref=p_signed_uid
       AND a.status='ACTIVE' AND a.environment='MAINNET' AND a.venue_id='venue-bybit'
       AND a.account_type='UNIFIED' AND c.account_connector_id=p_connector
       AND c.status='ACTIVE' AND c.retired_at IS NULL
       AND c.adapter_id='BYBIT_V5_PRIVATE' AND c.connector_kind='CEX_PRIVATE_RUNTIME'
  ) THEN RAISE EXCEPTION 'MUTATION_RECOVERY_IDENTITY_UNPROVEN'; END IF;

  -- Same account/mutation epoch locks as the existing state and latch owners.
  PERFORM pg_advisory_xact_lock(hashtextextended(p_account,0));
  PERFORM pg_advisory_xact_lock(hashtext(p_account),hashtext(p_connector));
  v_now := clock_timestamp();
  IF p_proof_at < v_now-interval '15 seconds' OR p_proof_at > v_now+interval '1 second'
  THEN RAISE EXCEPTION 'MUTATION_RECOVERY_PROOF_EXPIRED'; END IF;
  SELECT * INTO v_latch FROM runtime.account_entry_safety_blocks
    WHERE trading_account_id=p_account AND account_connector_id=p_connector FOR UPDATE;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('status','ALREADY_CLEAR','account',p_account);
  END IF;
  IF v_latch.reason IS DISTINCT FROM 'PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED'
     OR v_latch.blocked_at IS DISTINCT FROM p_blocked_at
     OR v_latch.blocked_at > p_proof_at
  THEN RAISE EXCEPTION 'MUTATION_RECOVERY_LATCH_EPOCH_CHANGED'; END IF;
  IF EXISTS(SELECT 1 FROM control.account_mutation_recovery_events WHERE request_id=p_request)
  THEN RAISE EXCEPTION 'MUTATION_RECOVERY_REQUEST_REPLAY'; END IF;
  IF EXISTS(SELECT 1 FROM control.execution_gates
             WHERE mode IN ('mainnet','testnet') AND enabled<>0)
     OR EXISTS(SELECT 1 FROM strategy_entry.execution_permissions WHERE enabled=true)
     OR EXISTS(SELECT 1 FROM control.live_arm_sessions WHERE state='ACTIVE')
     OR EXISTS(SELECT 1 FROM control.account_live_arm_sessions WHERE state='ACTIVE')
  THEN RAISE EXCEPTION 'MUTATION_RECOVERY_NOT_DISARMED'; END IF;

  SELECT * INTO v_generation FROM runtime.account_state_generations
    WHERE generation_id=p_generation AND trading_account_id=p_account
      AND account_connector_id=p_connector AND account_ref='BYBIT:UNIFIED'
      AND identity_status='PROVEN' AND state='COMPLETE'
      AND positions_count=0 AND active_orders_count=0
      AND completed_at BETWEEN v_now-interval '15 seconds' AND v_now+interval '1 second';
  IF NOT FOUND THEN RAISE EXCEPTION 'MUTATION_RECOVERY_FRESH_FLAT_REQUIRED'; END IF;
  IF EXISTS(SELECT 1 FROM runtime.position_ownership
             WHERE trading_account_id=p_account AND state<>'CLOSED')
     OR EXISTS(SELECT 1 FROM runtime.trade_commands
                WHERE trading_account_id=p_account
                  AND state NOT IN ('completed','failed','cancelled','rejected'))
     OR EXISTS(SELECT 1 FROM runtime.capital_reservations
                WHERE trading_account_id=p_account
                  AND state IN ('RESERVED','DISPATCHED','PENDING_EXCHANGE_REFLECTION',
                                'RECONCILIATION_REQUIRED'))
  THEN RAISE EXCEPTION 'MUTATION_RECOVERY_CURRENT_AMBIGUITY'; END IF;
  -- Exactly the current-vs-CLOSED predicate from account_entry_readiness.
  IF EXISTS(SELECT 1 FROM runtime.lifecycle_faults f
      LEFT JOIN runtime.position_ownership p
        ON p.position_id=NULLIF(f.exact_ids->>'strategy_position_id','')
      WHERE f.state='OPEN' AND f.severity='CRITICAL'
        AND (NULLIF(f.exact_ids->>'trading_account_id','') IS NULL
             OR f.exact_ids->>'trading_account_id'=p_account)
        AND p.state IS DISTINCT FROM 'CLOSED')
  THEN RAISE EXCEPTION 'MUTATION_RECOVERY_CURRENT_FAULT'; END IF;

  SELECT p.* INTO v_position FROM runtime.position_ownership p
    JOIN runtime.trade_commands c ON c.command_id=p.entry_command_id
      AND c.trading_account_id=p.trading_account_id AND c.identity_status='PROVEN'
      AND c.state='completed' AND c.command_type='entry'
   WHERE p.position_id=p_position AND p.trading_account_id=p_account
     AND p.identity_status='PROVEN' AND p.state='CLOSED' AND p.close_link_status='EXACT'
     AND p.bot_instance_id='universal-entry'
     AND p.strategy_version='2.0-p90l90-real-main-sub-20261028'
     AND p.fill_at<=p_blocked_at AND p.closed_at>=p_blocked_at
     AND p.closed_at<=v_generation.completed_at AND p.actual_qty>0;
  IF NOT FOUND THEN RAISE EXCEPTION 'MUTATION_RECOVERY_CLOSED_LINEAGE_UNPROVEN'; END IF;
  IF jsonb_typeof(v_position.execution_ids) IS DISTINCT FROM 'array'
     OR jsonb_typeof(v_position.exit_execution_ids) IS DISTINCT FROM 'array'
     OR coalesce(jsonb_array_length(v_position.execution_ids),0)=0
     OR coalesce(jsonb_array_length(v_position.exit_execution_ids),0)=0
  THEN RAISE EXCEPTION 'MUTATION_RECOVERY_EXECUTIONS_MISSING'; END IF;

  SELECT count(DISTINCT e.exec_id),sum(e.exec_qty::numeric) INTO v_count,v_qty
    FROM runtime.executions e
    WHERE e.exec_id=ANY(ARRAY(SELECT jsonb_array_elements_text(v_position.execution_ids)))
      AND e.trading_account_id=p_account AND e.account_connector_id=p_connector
      AND e.identity_status='PROVEN' AND e.symbol=v_position.symbol
      AND e.side=v_position.side AND e.exec_qty::numeric>0
      AND e.order_link_id=left(v_position.entry_command_id,36)
      AND v_position.exchange_order_ids ? e.order_id;
  IF v_count<>jsonb_array_length(v_position.execution_ids)
     OR v_qty IS DISTINCT FROM v_position.actual_qty
  THEN RAISE EXCEPTION 'MUTATION_RECOVERY_ENTRY_EXECUTIONS_UNPROVEN'; END IF;
  SELECT count(DISTINCT e.exec_id),sum(e.exec_qty::numeric) INTO v_count,v_qty
    FROM runtime.executions e JOIN runtime.trade_commands c
      ON c.command_id=e.order_link_id AND c.trading_account_id=e.trading_account_id
      AND c.identity_status='PROVEN' AND c.state='completed'
      AND c.command_type IN ('close','strategy_exit')
      AND c.payload_json::jsonb->>'strategy_position_id'=p_position
    WHERE e.exec_id=ANY(ARRAY(SELECT jsonb_array_elements_text(v_position.exit_execution_ids)))
      AND e.trading_account_id=p_account AND e.account_connector_id=p_connector
      AND e.identity_status='PROVEN' AND e.symbol=v_position.symbol
      AND e.side=CASE WHEN v_position.side='Buy' THEN 'Sell' ELSE 'Buy' END
      AND e.exec_qty::numeric>0 AND v_position.exit_order_ids ? e.order_id;
  IF v_count<>jsonb_array_length(v_position.exit_execution_ids)
     OR v_qty IS DISTINCT FROM v_position.actual_qty
  THEN
    -- A Bybit-resident initial TakeProfit has no bot close command/orderLinkId.
    -- Do not manufacture one; accept only independently PROVEN exchange order
    -- history plus the exact full native execution IDs and original TP price.
    IF jsonb_typeof(v_position.exit_order_ids) IS DISTINCT FROM 'array'
       OR jsonb_array_length(v_position.exit_order_ids)<>1
       OR jsonb_typeof(v_position.initial_protection_evidence) IS DISTINCT FROM 'object'
       OR NULLIF(v_position.initial_protection_evidence->>'takeProfit','') IS NULL
       OR v_position.position_idx IS NULL
       OR EXISTS (
         SELECT 1 FROM runtime.trade_commands c
         WHERE c.trading_account_id=p_account AND c.state='completed'
           AND c.command_type IN ('close','strategy_exit')
           AND c.payload_json::jsonb->>'strategy_position_id'=p_position
       )
    THEN RAISE EXCEPTION 'MUTATION_RECOVERY_NATIVE_TP_ORIGIN_UNPROVEN'; END IF;

    SELECT count(*),min(h.order_id) INTO v_tp_order_count,v_tp_order_id
      FROM runtime.exchange_order_history h
     WHERE h.order_id=v_position.exit_order_ids->>0
       AND h.trading_account_id=p_account AND h.account_connector_id=p_connector
       AND h.identity_status='PROVEN' AND h.order_status='Filled'
       AND h.symbol=v_position.symbol
       AND h.side=CASE WHEN v_position.side='Buy' THEN 'Sell' ELSE 'Buy' END
       AND coalesce(h.order_link_id,'')=''
       AND h.payload_json::jsonb->>'stopOrderType'='TakeProfit'
       AND h.payload_json::jsonb->>'createType'='CreateByTakeProfit'
       AND h.payload_json::jsonb->>'orderType'='Market'
       AND h.payload_json::jsonb->>'reduceOnly'='true'
       AND h.payload_json::jsonb->>'closeOnTrigger'='true'
       AND h.payload_json::jsonb->>'positionIdx'=v_position.position_idx::text
       AND h.payload_json::jsonb->>'triggerPrice'
             =v_position.initial_protection_evidence->>'takeProfit'
       AND (h.payload_json::jsonb->>'cumExecQty')::numeric=v_position.actual_qty
       AND (h.payload_json::jsonb->>'qty')::numeric=v_position.actual_qty;
    IF v_tp_order_count<>1 THEN
      RAISE EXCEPTION 'MUTATION_RECOVERY_NATIVE_TP_ORDER_UNPROVEN';
    END IF;

    SELECT count(*),count(DISTINCT e.exec_id),sum(e.exec_qty::numeric)
      INTO v_rows,v_count,v_qty
      FROM runtime.executions e
     WHERE e.exec_id=ANY(ARRAY(SELECT jsonb_array_elements_text(v_position.exit_execution_ids)))
       AND e.trading_account_id=p_account AND e.account_connector_id=p_connector
       AND e.identity_status='PROVEN' AND e.symbol=v_position.symbol
       AND e.side=CASE WHEN v_position.side='Buy' THEN 'Sell' ELSE 'Buy' END
       AND e.exec_qty::numeric>0 AND e.order_id=v_tp_order_id
       AND coalesce(e.order_link_id,'')=''
       AND e.payload_json::jsonb->>'execType'='Trade'
       AND e.payload_json::jsonb->>'stopOrderType'='TakeProfit'
       AND e.payload_json::jsonb->>'createType'='CreateByTakeProfit'
       AND e.payload_json::jsonb->>'orderId'=v_tp_order_id
       AND coalesce(e.payload_json::jsonb->>'orderLinkId','')='';
    IF v_count<>jsonb_array_length(v_position.exit_execution_ids)
       OR v_rows<>v_count
       OR v_qty IS DISTINCT FROM v_position.actual_qty
    THEN RAISE EXCEPTION 'MUTATION_RECOVERY_NATIVE_TP_EXECUTIONS_UNPROVEN'; END IF;
    v_close_proof_mode := 'EXCHANGE_NATIVE_TAKE_PROFIT';
  END IF;

  v_now := clock_timestamp();
  IF p_proof_at < v_now-interval '15 seconds'
  THEN RAISE EXCEPTION 'MUTATION_RECOVERY_PROOF_EXPIRED'; END IF;
  v_event := 'mutation-recovery-' || md5(p_account||p_request||p_blocked_at::text);
  INSERT INTO control.account_mutation_recovery_events(
      event_id,request_id,operator,trading_account_id,account_connector_id,
      release_commit,strategy_position_id,blocked_at,recovered_at,evidence)
    VALUES(v_event,p_request,p_operator,p_account,p_connector,p_release,p_position,
      p_blocked_at,v_now,jsonb_build_object(
        'old_latch',to_jsonb(v_latch),'signed_uid',p_signed_uid,
        'proof_source','bybit:signed-flat-linear-usdt:exact-account',
        'proof_observed_at',p_proof_at,'generation_id',p_generation,
        'entry_command_id',v_position.entry_command_id,
        'entry_execution_ids',v_position.execution_ids,
        'exit_execution_ids',v_position.exit_execution_ids,
        'exit_order_ids',v_position.exit_order_ids,
        'close_proof_mode',v_close_proof_mode,
        'native_tp_order_id',v_tp_order_id,
        'actual_qty',v_position.actual_qty,'closed_at',v_position.closed_at));
  DELETE FROM runtime.account_entry_safety_blocks
    WHERE trading_account_id=p_account AND account_connector_id=p_connector
      AND blocked_at=p_blocked_at AND reason='PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED';
  IF NOT FOUND THEN RAISE EXCEPTION 'MUTATION_RECOVERY_LATCH_EPOCH_CHANGED'; END IF;
  RETURN jsonb_build_object('status','RECOVERED','event_id',v_event,'account',p_account);
END;
$recover$;
ALTER FUNCTION control.recover_closed_p90_mutation_safety(
  text,text,text,text,text,text,timestamptz,timestamptz,text,text
) OWNER TO cripta_account_entry_safety_recovery;
REVOKE ALL ON FUNCTION control.recover_closed_p90_mutation_safety(
  text,text,text,text,text,text,timestamptz,timestamptz,text,text
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION control.recover_closed_p90_mutation_safety(
  text,text,text,text,text,text,timestamptz,timestamptz,text,text
) TO cripta;
COMMIT;
