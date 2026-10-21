-- One-time DB migrator-only binding for a unified R1 Strategy, NOT an
-- automatically applied schema migration. Run only after the monitored
-- five-to-one Strategy activation transition and exact-account preflight.
-- This does not open mainnet, switch execution permissions or create OWNER ARM.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

DO $r1_binding$
DECLARE
    v_activation text;
    v_account text;
    v_account_count integer;
    v_connector_count integer;
    v_activation_count integer;
    v_active_bindings integer;
    v_enabled_permissions integer;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM control.execution_gates
        WHERE mode='mainnet' AND enabled=false
    ) THEN
        RAISE EXCEPTION 'R1_BINDING_BLOCKED: mainnet new Entry not OFF';
    END IF;

    SELECT count(*), min(activation_id)
      INTO v_activation_count,v_activation
      FROM strategy_entry.strategy_activations
     WHERE enabled=true;
    IF v_activation_count <> 1 OR NOT EXISTS (
        SELECT 1 FROM strategy_entry.strategy_activations
         WHERE activation_id=v_activation AND strategy_id='r1_five_symbols'
           AND strategy_version='1.0-five-symbols-20261021'
           AND enabled=true
    ) THEN
        RAISE EXCEPTION 'R1_BINDING_BLOCKED: exact unified Strategy not sole active owner';
    END IF;

    SELECT count(*), min(trading_account_id)
      INTO v_account_count,v_account
      FROM identity.trading_accounts
     WHERE status='ACTIVE' AND environment='MAINNET';
    IF v_account_count <> 1 THEN
        RAISE EXCEPTION 'R1_BINDING_BLOCKED: MAINNET TradingAccount identity not unique';
    END IF;
    SELECT count(*) INTO v_connector_count
      FROM identity.account_connectors
     WHERE trading_account_id=v_account AND status='ACTIVE';
    IF v_connector_count <> 1 THEN
        RAISE EXCEPTION 'R1_BINDING_BLOCKED: exact account connector not unique';
    END IF;

    SELECT count(*) INTO v_active_bindings
      FROM strategy_entry.strategy_execution_bindings
     WHERE binding_state='ACTIVE';
    IF v_active_bindings <> 0 THEN
        RAISE EXCEPTION 'R1_BINDING_BLOCKED: unexpected active binding already exists';
    END IF;

    SELECT count(*) INTO v_enabled_permissions
      FROM strategy_entry.execution_permissions
     WHERE enabled=true;
    IF v_enabled_permissions <> 0 THEN
        RAISE EXCEPTION 'R1_BINDING_BLOCKED: execution permissions must remain OFF';
    END IF;

    IF EXISTS (SELECT 1 FROM control.live_arm_sessions WHERE state='ACTIVE')
       OR EXISTS (SELECT 1 FROM runtime.hot_positions)
       OR EXISTS (SELECT 1 FROM runtime.hot_orders)
       OR EXISTS (
           SELECT 1 FROM runtime.exchange_position_slot_claims
           WHERE state<>'RELEASED'
       )
       OR EXISTS (
           SELECT 1 FROM runtime.capital_reservations
           WHERE state<>'RELEASED'
       )
    THEN
        RAISE EXCEPTION 'R1_BINDING_BLOCKED: unresolved ownership/entry state';
    END IF;

    INSERT INTO strategy_entry.strategy_execution_bindings (
        strategy_execution_binding_id,
        strategy_activation_id,execution_leg,execution_role,
        trading_account_id,allocation_mode,binding_state,
        effective_from,operator,source,reason
    )
    VALUES (
        'binding-r1-unified-' || substr(md5(v_activation || ':' || v_account),1,16),
        v_activation,'PRIMARY','PRIMARY',v_account,
        'DEDICATED_STRATEGY','ACTIVE',clock_timestamp(),
        'owner-confirmed-2026-10-21',
        'operations/implementation/bind_r1_five_symbols.sql',
        'OWNER_ONE_R1_FIVE_SYMBOLS_ONE_TRADING_ACCOUNT'
    );
END;
$r1_binding$;
COMMIT;
