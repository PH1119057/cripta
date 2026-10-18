-- Disposable PostgreSQL CI schema baseline (CHECKED HERE 2026-10-18, PG16).
-- Non-authoritative schema-only evidence: NO rows, credentials or production data.
-- DO NOT apply to production. Apply the R10 binding-lock migration separately.

--
-- PostgreSQL database dump
--


-- Dumped from database version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)
-- Dumped by pg_dump version 16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: analyst; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA analyst;


--
-- Name: analytics; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA analytics;


--
-- Name: control; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA control;


--
-- Name: dispatcher_v2; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA dispatcher_v2;


--
-- Name: identity; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA identity;


--
-- Name: mayak_v2; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA mayak_v2;


--
-- Name: monitoring; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA monitoring;


--
-- Name: research_context; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA research_context;


--
-- Name: runtime; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA runtime;


--
-- Name: safety; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA safety;


--
-- Name: strategy_dispatcher; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA strategy_dispatcher;


--
-- Name: strategy_entry; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA strategy_entry;


--
-- Name: strategy_exit; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA strategy_exit;


--
-- Name: supervisor; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA supervisor;


--
-- Name: reject_mutation(); Type: FUNCTION; Schema: dispatcher_v2; Owner: -
--

CREATE FUNCTION dispatcher_v2.reject_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'dispatcher_v2 contexts are append-only';
END;
$$;


--
-- Name: reject_dispatcher_v2_event_link_mutation(); Type: FUNCTION; Schema: research_context; Owner: -
--

CREATE FUNCTION research_context.reject_dispatcher_v2_event_link_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'dispatcher_v2 event context links are append-only';
END;
$$;


--
-- Name: guard_capital_reservation_update(); Type: FUNCTION; Schema: runtime; Owner: -
--

CREATE FUNCTION runtime.guard_capital_reservation_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF ROW(
        OLD.reservation_id,OLD.account_ref,OLD.strategy_id,OLD.strategy_version,
        OLD.strategy_config_fingerprint,OLD.entry_plan_fingerprint,OLD.signal_id,
        OLD.strategy_attempt_id,OLD.requested_amount,OLD.amount_currency,
        OLD.capacity_snapshot_id,OLD.capacity_observed_at,
        OLD.capacity_available_at_reservation,OLD.pre_dispatch_expires_at,
        OLD.created_at
    ) IS DISTINCT FROM ROW(
        NEW.reservation_id,NEW.account_ref,NEW.strategy_id,NEW.strategy_version,
        NEW.strategy_config_fingerprint,NEW.entry_plan_fingerprint,NEW.signal_id,
        NEW.strategy_attempt_id,NEW.requested_amount,NEW.amount_currency,
        NEW.capacity_snapshot_id,NEW.capacity_observed_at,
        NEW.capacity_available_at_reservation,NEW.pre_dispatch_expires_at,
        NEW.created_at
    ) THEN
        RAISE EXCEPTION 'capital reservation identity is immutable';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;


--
-- Name: guard_lifecycle_fault_update(); Type: FUNCTION; Schema: runtime; Owner: -
--

CREATE FUNCTION runtime.guard_lifecycle_fault_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF ROW(
        OLD.fault_id,OLD.fault_code,OLD.severity,OLD.strategy_position_id,
        OLD.detected_at,OLD.exact_ids,OLD.created_at
    ) IS DISTINCT FROM ROW(
        NEW.fault_id,NEW.fault_code,NEW.severity,NEW.strategy_position_id,
        NEW.detected_at,NEW.exact_ids,NEW.created_at
    ) THEN
        RAISE EXCEPTION 'lifecycle fault identity is immutable';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: guard_plan_consumption_update(); Type: FUNCTION; Schema: runtime; Owner: -
--

CREATE FUNCTION runtime.guard_plan_consumption_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF ROW(
        OLD.plan_consumption_id,OLD.plan_kind,OLD.entry_plan_fingerprint,
        OLD.exit_plan_fingerprint,OLD.strategy_activation_id,OLD.consumer_kind,
        OLD.consumer_instance_id,OLD.loaded_at,OLD.created_at
    ) IS DISTINCT FROM ROW(
        NEW.plan_consumption_id,NEW.plan_kind,NEW.entry_plan_fingerprint,
        NEW.exit_plan_fingerprint,NEW.strategy_activation_id,NEW.consumer_kind,
        NEW.consumer_instance_id,NEW.loaded_at,NEW.created_at
    ) THEN
        RAISE EXCEPTION 'plan consumption identity is immutable';
    END IF;
    IF NEW.last_seen_at < OLD.last_seen_at THEN
        RAISE EXCEPTION 'plan consumption last_seen_at cannot move backwards';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: guard_position_exit_claim_update(); Type: FUNCTION; Schema: runtime; Owner: -
--

CREATE FUNCTION runtime.guard_position_exit_claim_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF ROW(
        OLD.claim_id,OLD.strategy_position_id,OLD.exit_plan_fingerprint,
        OLD.strategy_activation_id,OLD.consumer_instance_id,OLD.claimed_at,
        OLD.created_at
    ) IS DISTINCT FROM ROW(
        NEW.claim_id,NEW.strategy_position_id,NEW.exit_plan_fingerprint,
        NEW.strategy_activation_id,NEW.consumer_instance_id,NEW.claimed_at,
        NEW.created_at
    ) THEN
        RAISE EXCEPTION 'position exit claim identity is immutable';
    END IF;
    IF NEW.last_seen_at < OLD.last_seen_at THEN
        RAISE EXCEPTION 'position exit claim last_seen_at cannot move backwards';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: reject_immutable_change(); Type: FUNCTION; Schema: runtime; Owner: -
--

CREATE FUNCTION runtime.reject_immutable_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'immutable audit record % cannot be changed', TG_TABLE_NAME;
END;
$$;


--
-- Name: guard_activation_update(); Type: FUNCTION; Schema: strategy_entry; Owner: -
--

CREATE FUNCTION strategy_entry.guard_activation_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF ROW(
        OLD.activation_id,
        OLD.strategy_id,
        OLD.strategy_version,
        OLD.strategy_config_fingerprint,
        OLD.scope,
        OLD.created_at
    ) IS DISTINCT FROM ROW(
        NEW.activation_id,
        NEW.strategy_id,
        NEW.strategy_version,
        NEW.strategy_config_fingerprint,
        NEW.scope,
        NEW.created_at
    ) THEN
        RAISE EXCEPTION 'StrategyActivation identity/scope is immutable; create a new activation';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;


--
-- Name: guard_execution_permission_update(); Type: FUNCTION; Schema: strategy_entry; Owner: -
--

CREATE FUNCTION strategy_entry.guard_execution_permission_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF ROW(OLD.execution_permission_id,OLD.strategy_id,OLD.strategy_version,
           OLD.strategy_config_fingerprint,OLD.created_at)
       IS DISTINCT FROM
       ROW(NEW.execution_permission_id,NEW.strategy_id,NEW.strategy_version,
           NEW.strategy_config_fingerprint,NEW.created_at) THEN
       RAISE EXCEPTION 'ExecutionPermission identity is immutable';
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END;
$$;


--
-- Name: guard_shadow_parity_run_update(); Type: FUNCTION; Schema: strategy_entry; Owner: -
--

CREATE FUNCTION strategy_entry.guard_shadow_parity_run_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF OLD.status IN ('PASS', 'FAIL', 'NOT_COMPARABLE') THEN
        RAISE EXCEPTION 'final shadow parity run is immutable';
    END IF;

    IF ROW(
        OLD.parity_run_id,
        OLD.strategy_id,
        OLD.strategy_version,
        OLD.strategy_config_fingerprint,
        OLD.entry_plan_fingerprint,
        OLD.calibration_sha256,
        OLD.calibration_size,
        OLD.baseline_source_commit,
        OLD.universal_source_commit,
        OLD.fact_source_id,
        OLD.service_instance_id,
        OLD.started_at,
        OLD.created_at
    ) IS DISTINCT FROM ROW(
        NEW.parity_run_id,
        NEW.strategy_id,
        NEW.strategy_version,
        NEW.strategy_config_fingerprint,
        NEW.entry_plan_fingerprint,
        NEW.calibration_sha256,
        NEW.calibration_size,
        NEW.baseline_source_commit,
        NEW.universal_source_commit,
        NEW.fact_source_id,
        NEW.service_instance_id,
        NEW.started_at,
        NEW.created_at
    ) THEN
        RAISE EXCEPTION 'shadow parity run identity is immutable';
    END IF;

    IF OLD.status <> NEW.status AND NOT (
        (OLD.status = 'WARMUP' AND NEW.status IN ('PARITY_COMPARABLE', 'NOT_COMPARABLE'))
        OR
        (OLD.status = 'PARITY_COMPARABLE' AND NEW.status IN ('PASS', 'FAIL', 'NOT_COMPARABLE'))
    ) THEN
        RAISE EXCEPTION 'invalid shadow parity run status transition % -> %', OLD.status, NEW.status;
    END IF;

    IF NEW.status IN ('WARMUP', 'PARITY_COMPARABLE') AND NEW.finished_at IS NOT NULL THEN
        RAISE EXCEPTION 'active shadow parity run cannot have finished_at';
    END IF;
    IF NEW.status IN ('PASS', 'FAIL', 'NOT_COMPARABLE') AND NEW.finished_at IS NULL THEN
        RAISE EXCEPTION 'final shadow parity run requires finished_at';
    END IF;
    IF jsonb_typeof(NEW.summary) <> 'object' THEN
        RAISE EXCEPTION 'shadow parity run summary must be an object';
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: log_activation_event(); Type: FUNCTION; Schema: strategy_entry; Owner: -
--

CREATE FUNCTION strategy_entry.log_activation_event() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'strategy_entry', 'pg_catalog'
    AS $$
BEGIN
    INSERT INTO strategy_activation_events(
        activation_id, enabled, enabled_at, disabled_at,
        operator, source, reason, scope
    ) VALUES (
        NEW.activation_id, NEW.enabled, NEW.enabled_at, NEW.disabled_at,
        NEW.operator, NEW.source, NEW.change_reason, NEW.scope
    );
    RETURN NEW;
END;
$$;


--
-- Name: log_execution_permission_event(); Type: FUNCTION; Schema: strategy_entry; Owner: -
--

CREATE FUNCTION strategy_entry.log_execution_permission_event() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'strategy_entry', 'pg_catalog'
    AS $$
BEGIN
    INSERT INTO execution_permission_events(
      execution_permission_id,enabled,enabled_at,disabled_at,operator,source,reason
    ) VALUES(
      NEW.execution_permission_id,NEW.enabled,NEW.enabled_at,NEW.disabled_at,
      NEW.operator,NEW.source,NEW.change_reason
    );
    RETURN NEW;
END;
$$;


--
-- Name: reject_immutable_change(); Type: FUNCTION; Schema: strategy_entry; Owner: -
--

CREATE FUNCTION strategy_entry.reject_immutable_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'immutable strategy_entry record % cannot be changed', TG_TABLE_NAME;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: lifecycle_events; Type: TABLE; Schema: analyst; Owner: -
--

CREATE TABLE analyst.lifecycle_events (
    trade_id text NOT NULL,
    event_type text NOT NULL,
    event_id text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: trade_lifecycles; Type: TABLE; Schema: analyst; Owner: -
--

CREATE TABLE analyst.trade_lifecycles (
    trade_id text NOT NULL,
    position_id text,
    symbol text NOT NULL,
    side text,
    strategy_id text NOT NULL,
    strategy_version text,
    opened_at timestamp with time zone,
    closed_at timestamp with time zone,
    lifecycle_state text NOT NULL,
    data_completeness text NOT NULL,
    diagnosis_class text NOT NULL,
    actual_net_pnl numeric,
    lifecycle_json jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    actual_net_without_funding numeric,
    bot_instance_id text,
    entry_command_id text,
    geometry_handoff_id text
);


--
-- Name: counterfactual_candidates; Type: TABLE; Schema: analytics; Owner: -
--

CREATE TABLE analytics.counterfactual_candidates (
    counterfactual_id text NOT NULL,
    strategy_activation_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    exit_plan_fingerprint text NOT NULL,
    signal_id text NOT NULL,
    strategy_attempt_id text NOT NULL,
    entry_decision_id text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    decided_at timestamp with time zone NOT NULL,
    captured_at timestamp with time zone NOT NULL,
    decision_code text NOT NULL,
    requested_amount numeric NOT NULL,
    amount_currency text,
    capacity_snapshot_id text,
    reported_available_amount numeric,
    decision_reason text NOT NULL,
    evidence jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT counterfactual_candidates_decision_code_check CHECK ((decision_code = ANY (ARRAY['INSUFFICIENT_AVAILABLE_FUNDS'::text, 'EXCHANGE_POSITION_OWNERSHIP_CONFLICT'::text]))),
    CONSTRAINT counterfactual_candidates_direction_check CHECK ((direction = ANY (ARRAY['LONG'::text, 'SHORT'::text]))),
    CONSTRAINT counterfactual_candidates_evidence_check CHECK ((jsonb_typeof(evidence) = 'object'::text)),
    CONSTRAINT counterfactual_candidates_reported_available_amount_check CHECK (((reported_available_amount IS NULL) OR (reported_available_amount >= (0)::numeric))),
    CONSTRAINT counterfactual_candidates_requested_amount_check CHECK ((requested_amount > (0)::numeric))
);


--
-- Name: counterfactual_outcomes; Type: TABLE; Schema: analytics; Owner: -
--

CREATE TABLE analytics.counterfactual_outcomes (
    outcome_id text NOT NULL,
    counterfactual_id text NOT NULL,
    status text NOT NULL,
    evaluated_at timestamp with time zone NOT NULL,
    opened_at timestamp with time zone,
    entry_price numeric,
    closed_at timestamp with time zone,
    exit_price numeric,
    exit_reason text,
    gross_pnl numeric,
    fees numeric,
    funding numeric,
    slippage numeric,
    net_pnl_after_fees numeric,
    economics_status text NOT NULL,
    evidence jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT counterfactual_outcomes_check CHECK ((((status = 'NO_ENTRY'::text) AND (opened_at IS NULL) AND (entry_price IS NULL) AND (closed_at IS NULL) AND (exit_price IS NULL)) OR ((status = 'OPEN_AT_CUTOFF'::text) AND (opened_at IS NOT NULL) AND (entry_price IS NOT NULL) AND (closed_at IS NULL) AND (exit_price IS NULL)) OR ((status = 'CLOSED'::text) AND (opened_at IS NOT NULL) AND (entry_price IS NOT NULL) AND (closed_at IS NOT NULL) AND (exit_price IS NOT NULL)))),
    CONSTRAINT counterfactual_outcomes_economics_status_check CHECK ((economics_status = ANY (ARRAY['NO_ENTRY'::text, 'OPEN_UNREALIZED'::text, 'PARTIAL_NO_FUNDING'::text, 'COMPLETE'::text]))),
    CONSTRAINT counterfactual_outcomes_entry_price_check CHECK (((entry_price IS NULL) OR (entry_price > (0)::numeric))),
    CONSTRAINT counterfactual_outcomes_evidence_check CHECK ((jsonb_typeof(evidence) = 'object'::text)),
    CONSTRAINT counterfactual_outcomes_exit_price_check CHECK (((exit_price IS NULL) OR (exit_price > (0)::numeric))),
    CONSTRAINT counterfactual_outcomes_status_check CHECK ((status = ANY (ARRAY['NO_ENTRY'::text, 'OPEN_AT_CUTOFF'::text, 'CLOSED'::text])))
);


--
-- Name: m3_consumed_context; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.m3_consumed_context (
    signal_id text NOT NULL,
symbol text NOT NULL,
    direction text NOT NULL,
    signal_at timestamp with time zone NOT NULL,
    assessment_id text,
    mayak_snapshot_id text,
    assessment_observed_at timestamp with time zone,
    strategy_decision_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    profile_id text NOT NULL,
    profile_version text,
    dispatcher_status text NOT NULL,
    decision text NOT NULL,
    reason_ru text NOT NULL,
    context_type text NOT NULL,
    trading_effect text NOT NULL,
    payload jsonb NOT NULL,
    market_context_id text,
    CONSTRAINT m3_consumed_context_context_type_check CHECK ((context_type = ANY (ARRAY['CONSUMED_CONTEXT'::text, 'OBSERVED_CONTEXT'::text]))),
    CONSTRAINT m3_consumed_context_trading_effect_check CHECK ((trading_effect = ANY (ARRAY['FULL_LIVE_V1'::text, 'NONE'::text, 'CONTEXT_ONLY'::text])))
);


--
-- Name: entry_advisory_contexts; Type: VIEW; Schema: analytics; Owner: -
--

CREATE VIEW analytics.entry_advisory_contexts AS
 SELECT signal_id,
    symbol,
    direction,
    signal_at,
    assessment_id,
    mayak_snapshot_id,
    market_context_id,
    assessment_observed_at,
    strategy_decision_at,
    profile_id,
    profile_version,
    dispatcher_status,
    decision,
    reason_ru,
    context_type,
    trading_effect,
    payload
   FROM runtime.m3_consumed_context;


--
-- Name: market_minutes; Type: TABLE; Schema: analytics; Owner: -
--

CREATE TABLE analytics.market_minutes (
    observed_minute timestamp with time zone NOT NULL,
    source_observed_at timestamp with time zone NOT NULL,
    venue_id text NOT NULL,
    market_source_id text NOT NULL,
    instrument_id text NOT NULL,
    external_symbol text NOT NULL,
    product_category text NOT NULL,
    return_5m_pct double precision,
    mid_price double precision,
    buy_usd double precision,
    sell_usd double precision,
    net_usd double precision,
    turnover_usd double precision,
    large_buy_usd double precision,
    large_sell_usd double precision,
    bid_usd double precision,
    ask_usd double precision,
    bid_change_pct double precision,
    ask_change_pct double precision,
    open_interest double precision,
    open_interest_change_pct double precision,
    funding_rate double precision,
    last_price double precision,
    mark_price double precision,
    index_price double precision,
    long_ratio double precision,
    short_ratio double precision,
    trade_quality text,
    trade_age_seconds double precision,
    book_quality text,
    book_age_seconds double precision,
    ticker_quality text,
    ticker_age_seconds double precision,
    transport_quality text,
    instrument_availability text,
    source_snapshot_id bigint NOT NULL,
    architecture_version text NOT NULL,
    engine_version text NOT NULL,
    feature_version text NOT NULL,
    config_fingerprint text NOT NULL,
    schema_version text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    flow_5m_prior_turnover_usd double precision,
    flow_5m_acceleration_usd_per_min2 double precision,
    flow_1m_return_pct double precision,
    flow_1m_buy_usd double precision,
    flow_1m_sell_usd double precision,
    flow_1m_net_usd double precision,
    flow_1m_turnover_usd double precision,
    flow_1m_large_buy_usd double precision,
    flow_1m_large_sell_usd double precision,
    flow_1m_prior_turnover_usd double precision,
    flow_1m_acceleration_usd_per_min2 double precision,
    flow_15m_return_pct double precision,
    flow_15m_buy_usd double precision,
    flow_15m_sell_usd double precision,
    flow_15m_net_usd double precision,
    flow_15m_turnover_usd double precision,
    flow_15m_large_buy_usd double precision,
    flow_15m_large_sell_usd double precision,
    flow_15m_prior_turnover_usd double precision,
    flow_15m_acceleration_usd_per_min2 double precision,
    flow_30m_return_pct double precision,
    flow_30m_buy_usd double precision,
    flow_30m_sell_usd double precision,
    flow_30m_net_usd double precision,
    flow_30m_turnover_usd double precision,
    flow_30m_large_buy_usd double precision,
    flow_30m_large_sell_usd double precision,
    flow_30m_prior_turnover_usd double precision,
    flow_30m_acceleration_usd_per_min2 double precision,
    flow_60m_return_pct double precision,
    flow_60m_buy_usd double precision,
    flow_60m_sell_usd double precision,
    flow_60m_net_usd double precision,
    flow_60m_turnover_usd double precision,
    flow_60m_large_buy_usd double precision,
    flow_60m_large_sell_usd double precision,
    flow_60m_prior_turnover_usd double precision,
    flow_60m_acceleration_usd_per_min2 double precision,
    best_bid double precision,
    best_ask double precision,
    bid_depth_5bps_usd double precision,
    ask_depth_5bps_usd double precision,
    bid_depth_10bps_usd double precision,
    ask_depth_10bps_usd double precision,
    bid_depth_25bps_usd double precision,
    ask_depth_25bps_usd double precision,
    bid_depth_50bps_usd double precision,
    ask_depth_50bps_usd double precision,
    bid_change_1m_pct double precision,
    ask_change_1m_pct double precision,
    imbalance_change_1m double precision,
    bid_change_5m_pct double precision,
    ask_change_5m_pct double precision,
    imbalance_change_5m double precision,
    bid_change_15m_pct double precision,
    ask_change_15m_pct double precision,
    imbalance_change_15m double precision,
    open_interest_value double precision,
    open_interest_change_15m_pct double precision,
    open_interest_change_30m_pct double precision,
    open_interest_change_60m_pct double precision,
    open_interest_speed_5m_pct_per_min double precision,
    open_interest_acceleration_5m_pct_per_min2 double precision,
    funding_rate_change_from_previous double precision,
    CONSTRAINT market_minutes_check CHECK ((date_trunc('minute'::text, source_observed_at) = observed_minute)),
    CONSTRAINT market_minutes_check1 CHECK (((product_category = 'LINEAR'::text) OR ((open_interest IS NULL) AND (open_interest_change_pct IS NULL) AND (funding_rate IS NULL) AND (last_price IS NULL) AND (mark_price IS NULL) AND (index_price IS NULL) AND (long_ratio IS NULL) AND (short_ratio IS NULL) AND (ticker_quality IS NULL) AND (ticker_age_seconds IS NULL)))),
    CONSTRAINT market_minutes_observed_minute_check CHECK ((observed_minute = date_trunc('minute'::text, observed_minute))),
    CONSTRAINT market_minutes_product_category_check CHECK ((product_category = ANY (ARRAY['SPOT'::text, 'LINEAR'::text])))
);


--
-- Name: position_exit_attribution; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.position_exit_attribution (
    attribution_id text NOT NULL,
    position_id text NOT NULL,
    trade_id text NOT NULL,
    closed_at timestamp with time zone NOT NULL,
    link_status text NOT NULL,
    link_method text NOT NULL,
    exit_owner text NOT NULL,
    exit_mechanism text NOT NULL,
    exit_order_id text,
    exit_execution_ids jsonb NOT NULL,
    actual_avg_entry numeric NOT NULL,
    intended_initial_hard_stop numeric,
    actual_exchange_stop_before_exit numeric,
    exchange_trigger_price numeric,
    trigger_by text,
    actual_exit_avg_fill numeric,
    actual_exit_qty numeric,
    entry_to_exit_price_move_pct numeric,
    trigger_to_fill_slippage_pct numeric,
    gross_pnl numeric,
    entry_fee_actual numeric,
    exit_fee_actual numeric,
    funding numeric,
    actual_net_without_funding numeric,
    actual_net_pnl numeric,
    economics_completeness text NOT NULL,
    evidence jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    exit_order_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    CONSTRAINT position_exit_attribution_exit_mechanism_check CHECK ((exit_mechanism = ANY (ARRAY['INITIAL_HARD_STOP'::text, 'PROFIT_PROTECTION_STOP'::text, 'TRAILING_STOP'::text, 'TAKE_PROFIT'::text, 'STRATEGY_EXIT'::text, 'OWNER_MANUAL_STOP'::text, 'OWNER_MANUAL_CLOSE'::text, 'TECHNICAL_CLOSE'::text, 'UNKNOWN'::text, 'OWNER_MODIFIED_STOP'::text]))),
    CONSTRAINT position_exit_attribution_exit_owner_check CHECK ((exit_owner = ANY (ARRAY['ALGORITHM'::text, 'OWNER'::text, 'TECHNICAL_SAFETY'::text, 'EXCHANGE'::text, 'UNKNOWN'::text]))),
    CONSTRAINT position_exit_attribution_link_status_check CHECK ((link_status = ANY (ARRAY['EXACT'::text, 'UNRESOLVED_EXACT_LINK'::text])))
);


--
-- Name: position_ownership; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.position_ownership (
    position_id text NOT NULL,
    trade_id text NOT NULL,
    bot_instance_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    signal_id text NOT NULL,
    entry_command_id text NOT NULL,
    geometry_handoff_id text,
    symbol text NOT NULL,
    side text NOT NULL,
    actual_avg_fill numeric NOT NULL,
    actual_qty numeric NOT NULL,
    fill_at timestamp with time zone NOT NULL,
    exchange_order_ids jsonb NOT NULL,
    client_order_ids jsonb NOT NULL,
    execution_ids jsonb NOT NULL,
    state text DEFAULT 'OPEN'::text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    exchange_position_key text,
    position_idx integer DEFAULT 0 NOT NULL,
    closed_at timestamp with time zone,
    exit_order_id text,
    exit_execution_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    close_link_status text DEFAULT 'OPEN'::text NOT NULL,
    exit_order_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    account_ref text,
    strategy_config_fingerprint text,
    strategy_activation_id text,
    strategy_attempt_id text,
    entry_decision_id text,
    entry_execution_request_id text,
    entry_plan_fingerprint text,
    exit_plan_fingerprint text,
    exchange_position_slot_claim_id text,
    position_mode_state_ref text,
    initial_protection_confirmed_at timestamp with time zone,
    initial_protection_evidence jsonb,
    emergency_policy jsonb,
    strategy_execution_binding_id text,
    execution_leg text,
    trading_account_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL,
    CONSTRAINT position_ownership_state_check CHECK ((state = ANY (ARRAY['OPEN'::text, 'CLOSED'::text, 'RECONCILIATION_REQUIRED'::text]))),
    CONSTRAINT position_ownership_universal_lineage_check CHECK (((bot_instance_id <> 'universal-entry'::text) OR ((account_ref IS NOT NULL) AND (strategy_config_fingerprint IS NOT NULL) AND (strategy_activation_id IS NOT NULL) AND (strategy_attempt_id IS NOT NULL) AND (entry_decision_id IS NOT NULL) AND (entry_execution_request_id IS NOT NULL) AND (entry_plan_fingerprint IS NOT NULL) AND (exit_plan_fingerprint IS NOT NULL) AND (exchange_position_key IS NOT NULL))))
);


--
-- Name: position_exit_attribution_v1; Type: VIEW; Schema: analytics; Owner: -
--

CREATE VIEW analytics.position_exit_attribution_v1 AS
 SELECT o.signal_id,
    o.strategy_id,
    o.strategy_version,
    o.entry_command_id,
    o.geometry_handoff_id,
    o.position_id,
    o.trade_id,
    o.symbol,
    o.side,
    o.actual_avg_fill,
    o.actual_qty,
    o.fill_at,
    o.state,
    a.closed_at,
    a.link_status,
    a.link_method,
    a.exit_owner,
    a.exit_mechanism,
    a.exit_order_id,
    a.exit_execution_ids,
    a.intended_initial_hard_stop,
    a.actual_exchange_stop_before_exit,
    a.exchange_trigger_price,
    a.trigger_by,
    a.actual_exit_avg_fill,
    a.actual_exit_qty,
    a.entry_to_exit_price_move_pct,
    a.trigger_to_fill_slippage_pct,
    a.gross_pnl,
    a.entry_fee_actual,
    a.exit_fee_actual,
    a.funding,
    a.actual_net_without_funding,
    a.actual_net_pnl,
    a.economics_completeness,
    a.evidence,
    a.exit_order_ids
   FROM (runtime.position_ownership o
     LEFT JOIN runtime.position_exit_attribution a USING (position_id, trade_id));


--
-- Name: entry_geometry_handoffs; Type: TABLE; Schema: monitoring; Owner: -
--

CREATE TABLE monitoring.entry_geometry_handoffs (
    geometry_handoff_id text NOT NULL,
    signal_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    entry_fingerprint text NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL,
    signal_at timestamp with time zone NOT NULL,
    geometry_observed_at timestamp with time zone NOT NULL,
    geometry_version text NOT NULL,
    config_fingerprint text NOT NULL,
    geometry_hash text NOT NULL,
    payload jsonb NOT NULL,
    provenance jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT entry_geometry_handoffs_check CHECK ((geometry_observed_at <= signal_at))
);


--
-- Name: position_lifecycle_identity; Type: VIEW; Schema: analytics; Owner: -
--

CREATE VIEW analytics.position_lifecycle_identity AS
 SELECT o.position_id,
    o.trade_id,
    o.bot_instance_id,
    o.strategy_id,
    o.strategy_version,
    o.signal_id,
    o.entry_command_id,
    o.geometry_handoff_id,
    o.symbol,
    o.side,
    o.actual_avg_fill,
    o.actual_qty,
    o.fill_at,
    o.state,
    g.signal_at,
    g.geometry_observed_at,
    g.geometry_version,
    g.geometry_hash
   FROM (runtime.position_ownership o
     LEFT JOIN monitoring.entry_geometry_handoffs g ON ((g.geometry_handoff_id = o.geometry_handoff_id)));


--
-- Name: shared_market_contexts; Type: TABLE; Schema: mayak_v2; Owner: -
--

CREATE TABLE mayak_v2.shared_market_contexts (
    market_context_id text NOT NULL,
    mayak_snapshot_id bigint NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    mayak_version text NOT NULL,
    schema_version text NOT NULL,
    config_fingerprint text NOT NULL,
    data_quality text NOT NULL,
    payload jsonb NOT NULL,
    provenance jsonb NOT NULL,
    content_hash text NOT NULL,
    market_source_id text,
    CONSTRAINT shared_market_contexts_check CHECK (((payload ->> 'market_context_id'::text) = market_context_id)),
    CONSTRAINT shared_market_contexts_payload_check CHECK ((((payload -> 'provenance'::text) ->> 'trading_command'::text) = 'false'::text))
);


--
-- Name: assessments; Type: TABLE; Schema: strategy_dispatcher; Owner: -
--

CREATE TABLE strategy_dispatcher.assessments (
    assessment_id text NOT NULL,
    snapshot_id text NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    dispatcher_version text NOT NULL,
    profile_id text NOT NULL,
    profile_version text NOT NULL,
    suitability double precision,
    confidence double precision NOT NULL,
    status text NOT NULL,
    payload jsonb NOT NULL,
    stored_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    mayak_snapshot_id text,
    created_at timestamp with time zone,
    data_quality text,
    coverage jsonb,
    market_context_id text
);


--
-- Name: shared_market_context_consumption; Type: VIEW; Schema: analytics; Owner: -
--

CREATE VIEW analytics.shared_market_context_consumption AS
 SELECT a.assessment_id,
    a.profile_id,
    a.profile_version,
    a.market_context_id,
    c.mayak_snapshot_id,
    c.observed_at AS market_observed_at,
    a.observed_at AS assessment_observed_at,
    a.status,
    a.suitability,
    a.confidence,
    a.data_quality
   FROM (strategy_dispatcher.assessments a
     LEFT JOIN mayak_v2.shared_market_contexts c ON ((c.market_context_id = a.market_context_id)));


--
-- Name: bot_events; Type: TABLE; Schema: control; Owner: -
--

CREATE TABLE control.bot_events (
    id bigint NOT NULL,
    at_epoch_ms bigint NOT NULL,
    bot_id text NOT NULL,
    action text NOT NULL,
    result text NOT NULL,
    details_json text NOT NULL
);


--
-- Name: bot_events_id_seq; Type: SEQUENCE; Schema: control; Owner: -
--

ALTER TABLE control.bot_events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME control.bot_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: bots; Type: TABLE; Schema: control; Owner: -
--

CREATE TABLE control.bots (
    id text NOT NULL,
    name text NOT NULL,
    strategy text NOT NULL,
    mode text NOT NULL,
    desired_state text NOT NULL,
    actual_state text NOT NULL,
    executable text NOT NULL,
    mainnet_approved smallint DEFAULT 0 NOT NULL,
    symbols_json text NOT NULL,
    stats_json text NOT NULL,
    updated_at_epoch bigint NOT NULL
);


--
-- Name: command_requests; Type: TABLE; Schema: control; Owner: -
--

CREATE TABLE control.command_requests (
    id bigint NOT NULL,
    idempotency_key text NOT NULL,
    bot_id text NOT NULL,
    mode text NOT NULL,
    action text NOT NULL,
    payload_json text NOT NULL,
    state text NOT NULL,
    created_at_epoch_ms bigint NOT NULL,
    claimed_at_epoch_ms bigint,
    completed_at_epoch_ms bigint,
    result_json text NOT NULL,
    error text NOT NULL
);


--
-- Name: command_requests_id_seq; Type: SEQUENCE; Schema: control; Owner: -
--

ALTER TABLE control.command_requests ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME control.command_requests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: command_timings; Type: TABLE; Schema: control; Owner: -
--

CREATE TABLE control.command_timings (
    command_id bigint NOT NULL,
    signal_at_epoch_ns bigint,
    queued_at_epoch_ns bigint,
    claimed_at_epoch_ns bigint,
    send_at_epoch_ns bigint,
    accepted_at_epoch_ns bigint,
    filled_at_epoch_ns bigint
);


--
-- Name: execution_gate_events; Type: TABLE; Schema: control; Owner: -
--

CREATE TABLE control.execution_gate_events (
    id bigint NOT NULL,
    at_epoch_ms bigint NOT NULL,
    mode text NOT NULL,
    previous_enabled boolean NOT NULL,
    requested_enabled boolean NOT NULL,
    resulting_enabled boolean NOT NULL,
    reason text NOT NULL,
    source text NOT NULL,
    origin text NOT NULL,
    request_id text NOT NULL,
    settings_version text,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: execution_gate_events_id_seq; Type: SEQUENCE; Schema: control; Owner: -
--

CREATE SEQUENCE control.execution_gate_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: execution_gate_events_id_seq; Type: SEQUENCE OWNED BY; Schema: control; Owner: -
--
ALTER SEQUENCE control.execution_gate_events_id_seq OWNED BY control.execution_gate_events.id;


--
-- Name: execution_gates; Type: TABLE; Schema: control; Owner: -
--

CREATE TABLE control.execution_gates (
    mode text NOT NULL,
    enabled smallint NOT NULL,
    reason text NOT NULL,
    updated_at_epoch_ms bigint NOT NULL
);


--
-- Name: live_arm_evidence; Type: TABLE; Schema: control; Owner: -
--

CREATE TABLE control.live_arm_evidence (
    evidence_id text NOT NULL,
    check_code text NOT NULL,
    scope_type text NOT NULL,
    scope_key text NOT NULL,
    status text NOT NULL,
    checked_at timestamp with time zone NOT NULL,
    valid_until timestamp with time zone,
    release_commit text NOT NULL,
    source text NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT live_arm_evidence_check CHECK (((valid_until IS NULL) OR (valid_until >= checked_at))),
    CONSTRAINT live_arm_evidence_check_code_check CHECK ((check_code = ANY (ARRAY['CANON_CURRENT'::text, 'REMOTE_COMMIT_VERIFIED'::text, 'SOURCE_LIVE_IDENTITY'::text, 'TESTS'::text, 'LIVE_EQUIVALENCE'::text, 'PAPER_REAL_DECISION_PARITY'::text, 'EXCHANGE_ACCOUNT_IDENTITY'::text, 'POSITION_MODE_FRESH'::text, 'POSITION_IDX_EXPECTED'::text, 'PHYSICAL_SLOT_CLAIM_CONTRACT'::text, 'CAPITAL_RESERVATION_CONTRACT'::text, 'EXACT_STRATEGY_ACTIVATION'::text, 'ENTRY_PLAN_EXECUTABLE'::text, 'EXIT_PLAN_EXECUTABLE'::text, 'INITIAL_PROTECTION_EXECUTABLE'::text, 'TERMINAL_LOSS_CONTAINMENT_PATH'::text, 'EMERGENCY_POLICY_SUPPORTED'::text, 'LIFECYCLE_SUPERVISOR_BEHAVIOR'::text, 'CRITICAL_FAULT_DELIVERY'::text, 'RECONCILIATION_PATH'::text, 'MAINNET_GATE_EXPLICIT_OWNER_APPROVAL'::text, 'MICRO_LIVE_LIMITS'::text, 'ROLLBACK_OR_KILL_PATH'::text]))),
    CONSTRAINT live_arm_evidence_evidence_check CHECK ((jsonb_typeof(evidence) = 'object'::text)),
    CONSTRAINT live_arm_evidence_release_commit_check CHECK ((length(release_commit) = 40)),
    CONSTRAINT live_arm_evidence_scope_type_check CHECK ((scope_type = ANY (ARRAY['GLOBAL'::text, 'STRATEGY'::text, 'STRATEGY_SYMBOL'::text]))),
    CONSTRAINT live_arm_evidence_status_check CHECK ((status = ANY (ARRAY['PASS'::text, 'FAIL'::text, 'UNKNOWN'::text, 'STALE'::text, 'NOT_CHECKED_HERE'::text, 'OWNER_WAIVED_FOR_R1_MICRO_LIVE'::text])))
);


--
-- Name: live_arm_retirement_events; Type: TABLE; Schema: control; Owner: -
--

CREATE TABLE control.live_arm_retirement_events (
    event_id text NOT NULL,
    live_arm_session_id text NOT NULL,
    previous_release_commit text NOT NULL,
    loaded_commit text NOT NULL,
    reason text NOT NULL,
    retired_at timestamp with time zone NOT NULL,
    evidence jsonb NOT NULL,
    CONSTRAINT live_arm_retirement_events_evidence_check CHECK ((jsonb_typeof(evidence) = 'object'::text))
);


--
-- Name: live_arm_sessions; Type: TABLE; Schema: control; Owner: -
--

CREATE TABLE control.live_arm_sessions (
    live_arm_session_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    strategy_activation_id text NOT NULL,
    symbol text NOT NULL,
    release_commit text NOT NULL,
    state text NOT NULL,
    owner_approved_at timestamp with time zone NOT NULL,
    activated_at timestamp with time zone NOT NULL,
    deactivated_at timestamp with time zone,
    source text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT live_arm_sessions_check CHECK ((((state = 'ACTIVE'::text) AND (deactivated_at IS NULL)) OR ((state = 'CLOSED'::text) AND (deactivated_at IS NOT NULL)))),
    CONSTRAINT live_arm_sessions_release_commit_check CHECK ((length(release_commit) = 40)),
    CONSTRAINT live_arm_sessions_state_check CHECK ((state = ANY (ARRAY['ACTIVE'::text, 'CLOSED'::text])))
);


--
-- Name: release_arm_incidents; Type: TABLE; Schema: control; Owner: -
--

CREATE TABLE control.release_arm_incidents (
    incident_id text NOT NULL,
    release_commit text NOT NULL,
    reason text NOT NULL,
    affected jsonb NOT NULL,
    state text NOT NULL,
    detected_at timestamp with time zone NOT NULL,
    last_checked_at timestamp with time zone NOT NULL,
    resolved_at timestamp with time zone,
    CONSTRAINT release_arm_incidents_affected_check CHECK ((jsonb_typeof(affected) = 'object'::text)),
    CONSTRAINT release_arm_incidents_check CHECK ((((state = 'OPEN'::text) AND (resolved_at IS NULL)) OR ((state = 'RESOLVED'::text) AND (resolved_at IS NOT NULL)))),
    CONSTRAINT release_arm_incidents_state_check CHECK ((state = ANY (ARRAY['OPEN'::text, 'RESOLVED'::text])))
);


--
-- Name: coin_market_contexts; Type: TABLE; Schema: dispatcher_v2; Owner: -
--

CREATE TABLE dispatcher_v2.coin_market_contexts (
    coin_context_id text NOT NULL,
    global_context_id text NOT NULL,
    source_coin_context_id text NOT NULL,
    source_mayak_snapshot_id bigint NOT NULL,
    symbol text NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL,
    dispatcher_version text NOT NULL,
    schema_version text NOT NULL,
    config_fingerprint text NOT NULL,
    data_quality text NOT NULL,
    freshness_status text NOT NULL,
    freshness_age_seconds double precision,
    coverage jsonb NOT NULL,
    payload jsonb NOT NULL,
    provenance jsonb NOT NULL,
    content_hash text NOT NULL,
    trading_effect text DEFAULT 'NONE'::text NOT NULL,
    market_source_id text,
    spot_instrument_id text,
    linear_instrument_id text,
    CONSTRAINT coin_market_contexts_coverage_check CHECK ((jsonb_typeof(coverage) = 'object'::text)),
    CONSTRAINT coin_market_contexts_data_quality_check CHECK ((data_quality = ANY (ARRAY['HIGH'::text, 'MEDIUM'::text, 'LOW'::text, 'INSUFFICIENT'::text]))),
    CONSTRAINT coin_market_contexts_freshness_status_check CHECK ((freshness_status = ANY (ARRAY['FRESH'::text, 'STALE'::text, 'UNKNOWN'::text]))),
    CONSTRAINT coin_market_contexts_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT coin_market_contexts_provenance_check CHECK ((jsonb_typeof(provenance) = 'object'::text)),
    CONSTRAINT coin_market_contexts_trading_effect_check CHECK ((trading_effect = 'NONE'::text))
);


--
-- Name: global_market_contexts; Type: TABLE; Schema: dispatcher_v2; Owner: -
--

CREATE TABLE dispatcher_v2.global_market_contexts (
    global_context_id text NOT NULL,
    source_market_context_id text NOT NULL,
    source_mayak_snapshot_id bigint NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL,
    dispatcher_version text NOT NULL,
    schema_version text NOT NULL,
    config_fingerprint text NOT NULL,
    data_quality text NOT NULL,
    freshness_status text NOT NULL,
    freshness_age_seconds double precision,
    coverage jsonb NOT NULL,
    payload jsonb NOT NULL,
    provenance jsonb NOT NULL,
    content_hash text NOT NULL,
    trading_effect text DEFAULT 'NONE'::text NOT NULL,
    market_source_id text,
    CONSTRAINT global_market_contexts_coverage_check CHECK ((jsonb_typeof(coverage) = 'object'::text)),
    CONSTRAINT global_market_contexts_data_quality_check CHECK ((data_quality = ANY (ARRAY['HIGH'::text, 'MEDIUM'::text, 'LOW'::text, 'INSUFFICIENT'::text]))),
    CONSTRAINT global_market_contexts_freshness_status_check CHECK ((freshness_status = ANY (ARRAY['FRESH'::text, 'STALE'::text, 'UNKNOWN'::text]))),
    CONSTRAINT global_market_contexts_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT global_market_contexts_provenance_check CHECK ((jsonb_typeof(provenance) = 'object'::text)),
    CONSTRAINT global_market_contexts_trading_effect_check CHECK ((trading_effect = 'NONE'::text))
);


--
-- Name: trading_capacity_snapshots; Type: TABLE; Schema: dispatcher_v2; Owner: -
--

CREATE TABLE dispatcher_v2.trading_capacity_snapshots (
    capacity_snapshot_id text NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone NOT NULL,
    dispatcher_version text NOT NULL,
    schema_version text NOT NULL,
    config_fingerprint text NOT NULL,
    source_adapter text NOT NULL,
    source_account_ref text NOT NULL,
    account_type text,
    data_quality text NOT NULL,
    freshness_status text NOT NULL,
    freshness_age_seconds double precision,
    total_equity numeric,
    wallet_balance numeric,
    used_position_margin numeric,
    reserved_order_margin numeric,
    free_balance numeric,
    available_for_new_trading numeric,
    open_positions_count integer NOT NULL,
    active_orders_count integer NOT NULL,
    coverage jsonb NOT NULL,
    payload jsonb NOT NULL,
    provenance jsonb NOT NULL,
    content_hash text NOT NULL,
    trading_effect text DEFAULT 'NONE'::text NOT NULL,
    trading_account_id text,
    account_connector_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL,
    CONSTRAINT trading_capacity_snapshots_active_orders_count_check CHECK ((active_orders_count >= 0)),
    CONSTRAINT trading_capacity_snapshots_coverage_check CHECK ((jsonb_typeof(coverage) = 'object'::text)),
    CONSTRAINT trading_capacity_snapshots_data_quality_check CHECK ((data_quality = ANY (ARRAY['HIGH'::text, 'MEDIUM'::text, 'LOW'::text, 'INSUFFICIENT'::text]))),
    CONSTRAINT trading_capacity_snapshots_freshness_status_check CHECK ((freshness_status = ANY (ARRAY['FRESH'::text, 'STALE'::text, 'UNKNOWN'::text]))),
    CONSTRAINT trading_capacity_snapshots_open_positions_count_check CHECK ((open_positions_count >= 0)),
    CONSTRAINT trading_capacity_snapshots_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT trading_capacity_snapshots_provenance_check CHECK ((jsonb_typeof(provenance) = 'object'::text)),
    CONSTRAINT trading_capacity_snapshots_trading_effect_check CHECK ((trading_effect = 'NONE'::text))
);


--
-- Name: account_connectors; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.account_connectors (
    account_connector_id text NOT NULL,
    trading_account_id text NOT NULL,
    connector_kind text NOT NULL,
    adapter_id text NOT NULL,
    credential_ref text,
    status text DEFAULT 'ACTIVE'::text NOT NULL,
    config_fingerprint text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    retired_at timestamp with time zone
);


--
-- Name: instruments; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.instruments (
    instrument_id text NOT NULL,
    venue_id text NOT NULL,
    product_category text,
    external_symbol text,
    base_asset text,
    quote_asset text,
    network text,
    protocol text,
    pool_ref text,
    status text DEFAULT 'ACTIVE'::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: market_sources; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.market_sources (
    market_source_id text NOT NULL,
    venue_id text NOT NULL,
    source_kind text NOT NULL,
    provider_ref text,
    environment text,
    market_scope text,
    status text DEFAULT 'ACTIVE'::text NOT NULL,
    provenance jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: trading_accounts; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.trading_accounts (
    trading_account_id text NOT NULL,
    venue_id text NOT NULL,
    external_account_ref text,
    parent_trading_account_id text,
    account_kind text NOT NULL,
    account_type text,
    environment text NOT NULL,
    status text DEFAULT 'ACTIVE'::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    retired_at timestamp with time zone,
    CONSTRAINT trading_accounts_check CHECK (((parent_trading_account_id IS NULL) OR (parent_trading_account_id <> trading_account_id)))
);


--
-- Name: venues; Type: TABLE; Schema: identity; Owner: -
--

CREATE TABLE identity.venues (
    venue_id text NOT NULL,
    venue_kind text NOT NULL,
    display_name text NOT NULL,
    status text DEFAULT 'ACTIVE'::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: coin_market_contexts; Type: TABLE; Schema: mayak_v2; Owner: -
--

CREATE TABLE mayak_v2.coin_market_contexts (
    coin_context_id text NOT NULL,
    mayak_snapshot_id bigint NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    symbol text NOT NULL,
    schema_version text NOT NULL,
    engine_version text NOT NULL,
    feature_version text NOT NULL,
    config_fingerprint text NOT NULL,
    data_quality text NOT NULL,
    payload jsonb NOT NULL,
    provenance jsonb NOT NULL,
    content_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    market_source_id text,
    spot_instrument_id text,
    linear_instrument_id text,
    CONSTRAINT coin_market_contexts_data_quality_check CHECK ((data_quality = ANY (ARRAY['HIGH'::text, 'MEDIUM'::text, 'LOW'::text, 'INSUFFICIENT'::text]))),
    CONSTRAINT coin_market_contexts_no_trade_command_check CHECK (((provenance ->> 'trading_command'::text) = 'false'::text)),
    CONSTRAINT coin_market_contexts_payload_object_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT coin_market_contexts_provenance_object_check CHECK ((jsonb_typeof(provenance) = 'object'::text))
);


--
-- Name: coin_minutes; Type: TABLE; Schema: mayak_v2; Owner: -
--

CREATE TABLE mayak_v2.coin_minutes (
    observed_at timestamp with time zone NOT NULL,
    snapshot_id bigint NOT NULL,
    symbol text NOT NULL,
    spot_buy_usd double precision,
    spot_sell_usd double precision,
    spot_net_usd double precision,
    spot_turnover_usd double precision,
    derivatives_buy_usd double precision,
    derivatives_sell_usd double precision,
    derivatives_net_usd double precision,
    derivatives_turnover_usd double precision,
    return_5m_pct double precision,
    open_interest double precision,
    open_interest_change_pct double precision,
    funding_rate double precision,
    mark_price double precision,
    index_price double precision,
    long_ratio double precision,
    short_ratio double precision,
    spot_bid_usd double precision,
    spot_ask_usd double precision,
    spot_bid_change_pct double precision,
    spot_ask_change_pct double precision,
    derivatives_bid_usd double precision,
    derivatives_ask_usd double precision,
    derivatives_bid_change_pct double precision,
    derivatives_ask_change_pct double precision,
    large_spot_buy_usd double precision,
    large_spot_sell_usd double precision,
    large_derivatives_buy_usd double precision,
    large_derivatives_sell_usd double precision,
    source_quality jsonb NOT NULL,
    market_source_id text,
    spot_instrument_id text,
    linear_instrument_id text
);


--
-- Name: continuity_gaps; Type: TABLE; Schema: mayak_v2; Owner: -
--

CREATE TABLE mayak_v2.continuity_gaps (
    source text NOT NULL,
    scope text NOT NULL,
    gap_started_at timestamp with time zone NOT NULL,
    gap_ended_at timestamp with time zone NOT NULL,
    duration_seconds double precision NOT NULL,
    detected_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    repair_status text DEFAULT 'UNREPAIRED'::text NOT NULL,
    repair_source text,
    provenance jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: events; Type: TABLE; Schema: mayak_v2; Owner: -
--

CREATE TABLE mayak_v2.events (
    id bigint NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    event_type text NOT NULL,
    reference_id text NOT NULL,
    symbol text,
    side text,
    snapshot_id bigint,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    link_quality text DEFAULT 'LEGACY_UNVERIFIED'::text NOT NULL,
    link_provenance jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: events_id_seq; Type: SEQUENCE; Schema: mayak_v2; Owner: -
--

CREATE SEQUENCE mayak_v2.events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: events_id_seq; Type: SEQUENCE OWNED BY; Schema: mayak_v2; Owner: -
--

ALTER SEQUENCE mayak_v2.events_id_seq OWNED BY mayak_v2.events.id;


--
-- Name: liquidations; Type: TABLE; Schema: mayak_v2; Owner: -
--

CREATE TABLE mayak_v2.liquidations (
    occurred_at timestamp with time zone NOT NULL,
    symbol text NOT NULL,
    position_side text NOT NULL,
    bankruptcy_price double precision NOT NULL,
    executed_size double precision NOT NULL,
    notional_usd double precision NOT NULL,
    source text DEFAULT 'bybit_all_liquidation_v5'::text NOT NULL,
    market_source_id text,
    instrument_id text
);


--
-- Name: market_observation_alert_deliveries; Type: TABLE; Schema: mayak_v2; Owner: -
--

CREATE TABLE mayak_v2.market_observation_alert_deliveries (
    delivery_id text NOT NULL,
    alert_id text NOT NULL,
    channel text NOT NULL,
    state text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    next_attempt_at timestamp with time zone NOT NULL,
    last_attempt_at timestamp with time zone,
    delivered_at timestamp with time zone,
    acknowledged_at timestamp with time zone,
    escalation_at timestamp with time zone,
    last_error text,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT market_observation_alert_deliveries_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT market_observation_alert_deliveries_channel_check CHECK ((channel = 'OWNER_WEBHOOK'::text)),
    CONSTRAINT market_observation_alert_deliveries_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT market_observation_alert_deliveries_state_check CHECK ((state = ANY (ARRAY['PENDING'::text, 'DELIVERED'::text, 'ACKNOWLEDGED'::text, 'ESCALATION_REQUIRED'::text])))
);


--
-- Name: market_observation_alerts; Type: TABLE; Schema: mayak_v2; Owner: -
--

CREATE TABLE mayak_v2.market_observation_alerts (
    alert_id text NOT NULL,
    alert_class text NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    source_component text NOT NULL,
    scope_key text NOT NULL,
    owner_notifiable boolean DEFAULT false NOT NULL,
    payload jsonb NOT NULL,
    provenance jsonb NOT NULL,
    content_hash text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT market_observation_alerts_alert_class_check CHECK ((alert_class = ANY (ARRAY['REGIME_CHANGE'::text, 'MARKET_WIDE_STRESS'::text, 'SYNCHRONIZATION_SPIKE'::text, 'LIQUIDATION_CASCADE'::text, 'DATA_QUALITY_DEGRADATION'::text, 'SOURCE_OUTAGE'::text]))),
    CONSTRAINT market_observation_alerts_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT market_observation_alerts_provenance_check CHECK ((jsonb_typeof(provenance) = 'object'::text)),
    CONSTRAINT market_observation_alerts_provenance_check1 CHECK (((provenance ->> 'trading_command'::text) = 'false'::text))
);


--
-- Name: observation_journal; Type: TABLE; Schema: mayak_v2; Owner: -
--

CREATE TABLE mayak_v2.observation_journal (
    snapshot_id bigint NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    market_state text NOT NULL,
    confidence double precision NOT NULL,
    reasons jsonb NOT NULL,
    price_breadth jsonb NOT NULL,
    money_breadth jsonb NOT NULL,
    direction_synchronization jsonb NOT NULL,
    btc_context jsonb,
    eth_context jsonb,
    data_quality jsonb NOT NULL,
    architecture_version text NOT NULL,
    engine_version text NOT NULL,
    feature_version text NOT NULL,
    config_fingerprint text NOT NULL,
    dispatcher_handoff jsonb
);


--
-- Name: snapshots; Type: TABLE; Schema: mayak_v2; Owner: -
--

CREATE TABLE mayak_v2.snapshots (
    id bigint NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    state text NOT NULL,
    confidence double precision NOT NULL,
    payload jsonb NOT NULL,
    engine_version text NOT NULL,
    snapshot_kind text DEFAULT 'LEGACY'::text NOT NULL,
    regular_minute timestamp with time zone
);


--
-- Name: snapshots_id_seq; Type: SEQUENCE; Schema: mayak_v2; Owner: -
--

CREATE SEQUENCE mayak_v2.snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: snapshots_id_seq; Type: SEQUENCE OWNED BY; Schema: mayak_v2; Owner: -
--

ALTER SEQUENCE mayak_v2.snapshots_id_seq OWNED BY mayak_v2.snapshots.id;

--
-- Name: state_events; Type: TABLE; Schema: mayak_v2; Owner: -
--

CREATE TABLE mayak_v2.state_events (
    id bigint NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    state text NOT NULL,
    previous_state text,
    confidence double precision NOT NULL,
    reasons jsonb NOT NULL,
    snapshot_id bigint
);


--
-- Name: state_events_id_seq; Type: SEQUENCE; Schema: mayak_v2; Owner: -
--

CREATE SEQUENCE mayak_v2.state_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: state_events_id_seq; Type: SEQUENCE OWNED BY; Schema: mayak_v2; Owner: -
--

ALTER SEQUENCE mayak_v2.state_events_id_seq OWNED BY mayak_v2.state_events.id;


--
-- Name: entry_dispatcher_shadow_decisions; Type: TABLE; Schema: monitoring; Owner: -
--

CREATE TABLE monitoring.entry_dispatcher_shadow_decisions (
    chain_id text NOT NULL,
    m3_setup_id text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    signal_at timestamp with time zone NOT NULL,
    baseline_decision text NOT NULL,
    shadow_dispatcher_decision text NOT NULL,
    consumed_context_type text NOT NULL,
    consumed_dispatcher_assessment_id text,
    consumed_mayak_snapshot_id text,
    assessment_observed_at timestamp with time zone,
    strategy_decision_at timestamp with time zone NOT NULL,
    profile_id text NOT NULL,
    profile_version text,
    data_quality text,
    coverage jsonb,
    decision_reason_ru text NOT NULL,
    counterfactual_state text DEFAULT 'PENDING'::text NOT NULL,
    counterfactual_path jsonb DEFAULT '{}'::jsonb NOT NULL,
    trading_effect text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT entry_dispatcher_shadow_decisions_consumed_context_type_check CHECK ((consumed_context_type = 'CONSUMED_CONTEXT'::text)),
    CONSTRAINT entry_dispatcher_shadow_decisions_trading_effect_check CHECK ((trading_effect = 'NONE'::text))
);


--
-- Name: health_events; Type: TABLE; Schema: monitoring; Owner: -
--

CREATE TABLE monitoring.health_events (
    id bigint NOT NULL,
    at_epoch_ms bigint NOT NULL,
    state text NOT NULL,
    issues_json text NOT NULL
);


--
-- Name: health_events_id_seq; Type: SEQUENCE; Schema: monitoring; Owner: -
--

ALTER TABLE monitoring.health_events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME monitoring.health_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: opportunities; Type: TABLE; Schema: monitoring; Owner: -
--

CREATE TABLE monitoring.opportunities (
    signal_id text NOT NULL,
    bot_id text NOT NULL,
    strategy_version text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    signal_at_epoch_ms bigint NOT NULL,
    signal_price double precision NOT NULL,
    decision text NOT NULL,
    decision_reason text NOT NULL,
    traffic_light text NOT NULL,
    horizon_seconds integer NOT NULL,
    state text NOT NULL,
    last_price double precision,
    max_favorable_pct double precision DEFAULT 0 NOT NULL,
    max_adverse_pct double precision DEFAULT 0 NOT NULL,
    first_hits_json text DEFAULT '{}'::text NOT NULL,
    samples bigint DEFAULT 0 NOT NULL,
    finalized_at_epoch_ms bigint,
    created_at_epoch_ms bigint NOT NULL,
    CONSTRAINT opportunities_decision_check CHECK ((decision = ANY (ARRAY['entered'::text, 'skipped'::text, 'blocked'::text, 'shadow'::text]))),
    CONSTRAINT opportunities_direction_check CHECK ((direction = ANY (ARRAY['long'::text, 'short'::text]))),
    CONSTRAINT opportunities_signal_price_check CHECK ((signal_price > (0)::double precision))
);


--
-- Name: opportunity_analysis; Type: VIEW; Schema: monitoring; Owner: -
--

CREATE VIEW monitoring.opportunity_analysis AS
 SELECT signal_id,
    bot_id,
    strategy_version,
    symbol,
    direction,
    signal_at_epoch_ms,
    signal_price,
    decision,
    decision_reason,
    traffic_light,
    horizon_seconds,
    state,
    last_price,
    max_favorable_pct,
    max_adverse_pct,
    first_hits_json,
    samples,
    finalized_at_epoch_ms,
    created_at_epoch_ms,
        CASE
            WHEN (last_price IS NULL) THEN NULL::double precision
            WHEN (direction = 'long'::text) THEN (((last_price / signal_price) - (1)::double precision) * (100)::double precision)
            ELSE (((signal_price / last_price) - (1)::double precision) * (100)::double precision)
        END AS last_move_pct,
    ((first_hits_json)::jsonb ? '+0.1'::text) AS hit_plus_01,
    ((first_hits_json)::jsonb ? '+0.5'::text) AS hit_plus_05,
    ((first_hits_json)::jsonb ? '+0.7'::text) AS hit_plus_07,
    ((first_hits_json)::jsonb ? '+1.0'::text) AS hit_plus_10,
    ((first_hits_json)::jsonb ? '+1.1'::text) AS hit_plus_11,
    ((first_hits_json)::jsonb ? '-0.1'::text) AS hit_minus_01,
    ((first_hits_json)::jsonb ? '-0.2'::text) AS hit_minus_02,
    ((first_hits_json)::jsonb ? '-1.0'::text) AS hit_minus_10,
    ((first_hits_json)::jsonb ? '-3.0'::text) AS hit_minus_30
   FROM monitoring.opportunities o;


--
-- Name: opportunity_events; Type: TABLE; Schema: monitoring; Owner: -
--

CREATE TABLE monitoring.opportunity_events (
    id bigint NOT NULL,
    signal_id text NOT NULL,
    at_epoch_ms bigint NOT NULL,
    event text NOT NULL,
    value_pct double precision,
    price double precision,
    details_json text NOT NULL
);


--
-- Name: opportunity_events_id_seq; Type: SEQUENCE; Schema: monitoring; Owner: -
--

ALTER TABLE monitoring.opportunity_events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME monitoring.opportunity_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: opportunity_quality; Type: VIEW; Schema: monitoring; Owner: -
--

CREATE VIEW monitoring.opportunity_quality AS
 SELECT strategy_version,
    decision,
    decision_reason,
    traffic_light,
    symbol,
    direction,
    count(*) AS signals,
    count(*) FILTER (WHERE (state = 'completed'::text)) AS completed,
    avg(max_favorable_pct) AS avg_mfe_pct,
    avg(max_adverse_pct) AS avg_mae_pct,
    avg(last_move_pct) FILTER (WHERE (state = 'completed'::text)) AS avg_last_move_pct,
    avg((hit_plus_01)::integer) AS hit_plus_01_rate,
    avg((hit_plus_05)::integer) AS hit_plus_05_rate,
    avg((hit_plus_07)::integer) AS hit_plus_07_rate,
    avg((hit_plus_10)::integer) AS hit_plus_10_rate,
    avg((hit_plus_11)::integer) AS hit_plus_11_rate,
    avg((hit_minus_10)::integer) AS hit_minus_10_rate,
    avg((hit_minus_30)::integer) AS hit_minus_30_rate
   FROM monitoring.opportunity_analysis
  GROUP BY strategy_version, decision, decision_reason, traffic_light, symbol, direction;


--
-- Name: dispatcher_v2_event_links; Type: TABLE; Schema: research_context; Owner: -
--

CREATE TABLE research_context.dispatcher_v2_event_links (
    event_type text NOT NULL,
    reference_id text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    signal_id text,
    strategy_attempt_id text,
    position_id text,
    trade_id text,
    symbol text,
    side text,
    global_context_id text NOT NULL,
    global_observed_at timestamp with time zone NOT NULL,
    global_age_seconds double precision NOT NULL,
    coin_context_id text,
    coin_observed_at timestamp with time zone,
    coin_age_seconds double precision,
    capacity_snapshot_id text,
    capacity_observed_at timestamp with time zone,
    capacity_age_seconds double precision,
    observed_context_mode text DEFAULT 'OBSERVED_CONTEXT'::text NOT NULL,
    consumed_context_mode text DEFAULT 'NOT_CONSUMED'::text NOT NULL,
    link_quality text NOT NULL,
    event_payload jsonb NOT NULL,
    provenance jsonb NOT NULL,
    linked_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    trading_effect text DEFAULT 'NONE'::text NOT NULL,
    trading_account_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL,
    CONSTRAINT dispatcher_v2_event_links_capacity_age_seconds_check CHECK (((capacity_age_seconds IS NULL) OR (capacity_age_seconds >= (0)::double precision))),
    CONSTRAINT dispatcher_v2_event_links_check CHECK (((coin_context_id IS NULL) = (coin_observed_at IS NULL))),
    CONSTRAINT dispatcher_v2_event_links_check1 CHECK (((coin_context_id IS NULL) = (coin_age_seconds IS NULL))),
    CONSTRAINT dispatcher_v2_event_links_check2 CHECK (((capacity_snapshot_id IS NULL) = (capacity_observed_at IS NULL))),
    CONSTRAINT dispatcher_v2_event_links_check3 CHECK (((capacity_snapshot_id IS NULL) = (capacity_age_seconds IS NULL))),
    CONSTRAINT dispatcher_v2_event_links_coin_age_seconds_check CHECK (((coin_age_seconds IS NULL) OR (coin_age_seconds >= (0)::double precision))),
    CONSTRAINT dispatcher_v2_event_links_consumed_context_mode_check CHECK ((consumed_context_mode = 'NOT_CONSUMED'::text)),
    CONSTRAINT dispatcher_v2_event_links_event_payload_check CHECK ((jsonb_typeof(event_payload) = 'object'::text)),
    CONSTRAINT dispatcher_v2_event_links_global_age_seconds_check CHECK ((global_age_seconds >= (0)::double precision)),
    CONSTRAINT dispatcher_v2_event_links_link_quality_check CHECK ((link_quality = ANY (ARRAY['GLOBAL_COIN_CAPACITY_CAUSAL_PRIOR'::text, 'GLOBAL_COIN_CAUSAL_PRIOR_NO_CAPACITY'::text, 'GLOBAL_CAPACITY_CAUSAL_PRIOR_NO_COIN'::text, 'GLOBAL_ONLY_CAUSAL_PRIOR'::text]))),
    CONSTRAINT dispatcher_v2_event_links_observed_context_mode_check CHECK ((observed_context_mode = 'OBSERVED_CONTEXT'::text)),
    CONSTRAINT dispatcher_v2_event_links_provenance_check CHECK ((jsonb_typeof(provenance) = 'object'::text)),
    CONSTRAINT dispatcher_v2_event_links_provenance_check1 CHECK (((provenance ->> 'trading_command'::text) = 'false'::text)),
    CONSTRAINT dispatcher_v2_event_links_trading_effect_check CHECK ((trading_effect = 'NONE'::text))
);


--
-- Name: event_links; Type: TABLE; Schema: research_context; Owner: -
--

CREATE TABLE research_context.event_links (
    event_type text NOT NULL,
    reference_id text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    symbol text,
    side text,
    event_payload jsonb NOT NULL,
    observed_mayak_snapshot_id bigint,
    observed_mayak_at timestamp with time zone,
    observed_dispatcher_snapshot_id text,
    observed_dispatcher_at timestamp with time zone,
    observed_context jsonb NOT NULL,
    consumed_context jsonb,
    link_quality text NOT NULL,
    provenance jsonb NOT NULL,
    linked_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: account_state_generations; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.account_state_generations (
    generation_id text NOT NULL,
    account_ref text NOT NULL,
    reason text NOT NULL,
    state text NOT NULL,
    started_at timestamp with time zone NOT NULL,
    completed_at timestamp with time zone,
    account_type text,
    total_equity numeric,
    wallet_balance numeric,
    available_balance numeric,
    positions_count integer,
    active_orders_count integer,
    position_mode_refs jsonb DEFAULT '{}'::jsonb NOT NULL,
    wallet_payload jsonb,
    error text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    trading_account_id text,
    account_connector_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL,
    CONSTRAINT account_state_generations_active_orders_count_check CHECK (((active_orders_count IS NULL) OR (active_orders_count >= 0))),
    CONSTRAINT account_state_generations_check CHECK ((((state = 'COLLECTING'::text) AND (completed_at IS NULL)) OR ((state = 'FAILED'::text) AND (completed_at IS NOT NULL)) OR ((state = 'COMPLETE'::text) AND (completed_at IS NOT NULL) AND (account_type IS NOT NULL) AND (total_equity IS NOT NULL) AND (wallet_balance IS NOT NULL) AND (available_balance IS NOT NULL) AND (positions_count IS NOT NULL) AND (active_orders_count IS NOT NULL)))),
    CONSTRAINT account_state_generations_position_mode_refs_check CHECK ((jsonb_typeof(position_mode_refs) = 'object'::text)),
    CONSTRAINT account_state_generations_positions_count_check CHECK (((positions_count IS NULL) OR (positions_count >= 0))),
    CONSTRAINT account_state_generations_state_check CHECK ((state = ANY (ARRAY['COLLECTING'::text, 'COMPLETE'::text, 'FAILED'::text]))),
    CONSTRAINT account_state_generations_wallet_payload_check CHECK (((wallet_payload IS NULL) OR (jsonb_typeof(wallet_payload) = 'object'::text)))
);


--
-- Name: capital_reservations; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.capital_reservations (
    reservation_id text NOT NULL,
    account_ref text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    signal_id text NOT NULL,
    strategy_attempt_id text NOT NULL,
    requested_amount numeric NOT NULL,
    amount_currency text NOT NULL,
    capacity_snapshot_id text NOT NULL,
    capacity_observed_at timestamp with time zone NOT NULL,
    capacity_available_at_reservation numeric NOT NULL,
    pre_dispatch_expires_at timestamp with time zone NOT NULL,
    state text NOT NULL,
    state_reason text,
    exchange_commitment_ref text,
    exchange_commitment_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    strategy_position_id text,
    strategy_execution_binding_id text,
    execution_leg text,
    trading_account_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL,
    CONSTRAINT capital_reservations_amount_currency_check CHECK ((amount_currency <> ''::text)),
    CONSTRAINT capital_reservations_capacity_available_at_reservation_check CHECK ((capacity_available_at_reservation >= (0)::numeric)),
    CONSTRAINT capital_reservations_requested_amount_check CHECK ((requested_amount > (0)::numeric)),
    CONSTRAINT capital_reservations_state_check CHECK ((state = ANY (ARRAY['RESERVED'::text, 'DISPATCHED'::text, 'PENDING_EXCHANGE_REFLECTION'::text, 'CONSUMED'::text, 'RELEASED'::text, 'RECONCILIATION_REQUIRED'::text])))
);


--
-- Name: connection_events; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.connection_events (
    id bigint NOT NULL,
    at_epoch_ms bigint NOT NULL,
    channel text NOT NULL,
    event text NOT NULL,
    details_json text NOT NULL,
    trading_account_id text,
    account_connector_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL
);


--
-- Name: connection_events_id_seq; Type: SEQUENCE; Schema: runtime; Owner: -
--

ALTER TABLE runtime.connection_events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME runtime.connection_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: effective_protection_evidence; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.effective_protection_evidence (
    evidence_id text NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    recorded_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    command_id text,
    strategy_position_id text,
    account_ref text NOT NULL,
    exchange_position_key text NOT NULL,
    symbol text NOT NULL,
    position_idx integer NOT NULL,
    evidence_source text NOT NULL,
    state text NOT NULL,
    stop_loss numeric,
    position_take_profit numeric,
    maker_take_profit_order_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    reason text NOT NULL,
    evidence jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT effective_protection_evidence_evidence_check CHECK ((jsonb_typeof(evidence) = 'object'::text)),
    CONSTRAINT effective_protection_evidence_evidence_source_check CHECK ((evidence_source = 'BYBIT_AUTHENTICATED_ACCOUNT_GET'::text)),
    CONSTRAINT effective_protection_evidence_maker_take_profit_order_ids_check CHECK ((jsonb_typeof(maker_take_profit_order_ids) = 'array'::text)),
    CONSTRAINT effective_protection_evidence_state_check CHECK ((state = ANY (ARRAY['OBSERVED_PRESENT'::text, 'NOT_VERIFIED'::text, 'UNKNOWN'::text])))
);


--
-- Name: entry_cancel_intents; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.entry_cancel_intents (
    order_id text NOT NULL,
    command_id text NOT NULL,
    reason text NOT NULL,
    requested_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT entry_cancel_intents_command_id_check CHECK ((command_id <> ''::text)),
    CONSTRAINT entry_cancel_intents_order_id_check CHECK ((order_id <> ''::text)),
    CONSTRAINT entry_cancel_intents_reason_check CHECK ((reason <> ''::text))
);


--
-- Name: entry_decision_events; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.entry_decision_events (
    id bigint NOT NULL,
    signal_id text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    signal_at_epoch_ms bigint NOT NULL,
    observed_at_epoch_ms bigint NOT NULL,
    event_type text NOT NULL,
    decision text,
    reason text NOT NULL,
    details_json jsonb NOT NULL,
    entry_policy text NOT NULL,
    policy_version text NOT NULL,
    settings_version text NOT NULL
);


--
-- Name: entry_decision_events_id_seq; Type: SEQUENCE; Schema: runtime; Owner: -
--

ALTER TABLE runtime.entry_decision_events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME runtime.entry_decision_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: entry_decisions; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.entry_decisions (
    signal_id text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    signal_at_epoch_ms bigint NOT NULL,
    decided_at_epoch_ms bigint NOT NULL,
    decision text NOT NULL,
    reason text NOT NULL,
    details_json text DEFAULT '{}'::text NOT NULL,
    entry_policy text DEFAULT 'base_entry_v1'::text NOT NULL,
    policy_version text DEFAULT 'entry-policy-v1'::text NOT NULL,
    settings_version text DEFAULT 'unknown'::text NOT NULL,
    mayak_snapshot_id bigint,
    mayak_snapshot_time timestamp with time zone
);


--
-- Name: entry_geometry_bindings; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.entry_geometry_bindings (
    entry_command_id text NOT NULL,
    geometry_handoff_id text NOT NULL,
    signal_id text NOT NULL,
    bot_instance_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    bound_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    payload jsonb NOT NULL
);


--
-- Name: exchange_order_history; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.exchange_order_history (
    order_id text NOT NULL,
    order_link_id text DEFAULT ''::text NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL,
    order_status text NOT NULL,
    updated_at_epoch_ms bigint,
    payload_json jsonb NOT NULL,
    refreshed_at_epoch_ms bigint NOT NULL,
    trading_account_id text,
    account_connector_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL
);


--
-- Name: exchange_position_slot_claims; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.exchange_position_slot_claims (
    exchange_position_slot_claim_id text NOT NULL,
    exchange_position_key text NOT NULL,
    account_ref text NOT NULL,
    symbol text NOT NULL,
    position_idx integer NOT NULL,
    strategy_attempt_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    direction text NOT NULL,
    position_mode_state_ref text NOT NULL,
    capital_reservation_id text,
    strategy_position_id text,
    claim_state text NOT NULL,
    claimed_at timestamp with time zone NOT NULL,
    bound_at timestamp with time zone,


updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    released_at timestamp with time zone,
    release_reason text,
    strategy_execution_binding_id text,
    execution_leg text,
    trading_account_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL,
    CONSTRAINT exchange_position_slot_claims_check CHECK ((((claim_state = 'RELEASED'::text) AND (released_at IS NOT NULL) AND (release_reason IS NOT NULL)) OR ((claim_state <> 'RELEASED'::text) AND (released_at IS NULL)))),
    CONSTRAINT exchange_position_slot_claims_claim_state_check CHECK ((claim_state = ANY (ARRAY['CLAIMED'::text, 'BOUND'::text, 'RECONCILIATION_REQUIRED'::text, 'RELEASED'::text]))),
    CONSTRAINT exchange_position_slot_claims_direction_check CHECK ((direction = ANY (ARRAY['LONG'::text, 'SHORT'::text]))),
    CONSTRAINT exchange_position_slot_claims_position_idx_check CHECK ((position_idx >= 0))
);


--
-- Name: executions; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.executions (
    exec_id text NOT NULL,
    order_id text NOT NULL,
    order_link_id text NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL,
    exec_qty text NOT NULL,
    exec_price text NOT NULL,
    exec_fee text NOT NULL,
    exec_time_ms bigint,
    received_at_epoch_ms bigint NOT NULL,
    payload_json text NOT NULL,
    trading_account_id text,
    account_connector_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL
);


--
-- Name: hot_orders; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.hot_orders (
    order_id text NOT NULL,
    order_link_id text NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL,
    order_status text NOT NULL,
    qty text NOT NULL,
    price text NOT NULL,
    leaves_qty text NOT NULL,
    exchange_updated_ms bigint,
    refreshed_at_epoch_ms bigint NOT NULL,
    payload_json text NOT NULL,
    trading_account_id text,
    account_connector_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL
);


--
-- Name: hot_positions; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.hot_positions (
    symbol text NOT NULL,
    position_idx integer NOT NULL,
    side text NOT NULL,
    size text NOT NULL,
    entry_price text NOT NULL,
    leverage text NOT NULL,
    exchange_updated_ms bigint,
    refreshed_at_epoch_ms bigint NOT NULL,
    payload_json text NOT NULL,
    trading_account_id text,
    account_connector_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL
);


--
-- Name: lifecycle_fault_deliveries; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.lifecycle_fault_deliveries (
    delivery_id text NOT NULL,
    fault_id text NOT NULL,
    channel text NOT NULL,
    state text NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    next_attempt_at timestamp with time zone NOT NULL,
    last_attempt_at timestamp with time zone,
    delivered_at timestamp with time zone,
    acknowledged_at timestamp with time zone,
    escalation_at timestamp with time zone,
    last_error text,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT lifecycle_fault_deliveries_attempts_check CHECK ((attempts >= 0)),
    CONSTRAINT lifecycle_fault_deliveries_channel_check CHECK ((channel = ANY (ARRAY['OWNER_WEBHOOK'::text, 'DASHBOARD_INBOX'::text]))),
    CONSTRAINT lifecycle_fault_deliveries_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT lifecycle_fault_deliveries_state_check CHECK ((state = ANY (ARRAY['PENDING'::text, 'DELIVERED'::text, 'ACKNOWLEDGED'::text, 'ESCALATION_REQUIRED'::text])))
);


--
-- Name: lifecycle_faults; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.lifecycle_faults (
    fault_id text NOT NULL,
    fault_code text NOT NULL,
    severity text NOT NULL,
    state text NOT NULL,
    strategy_position_id text,
    detected_at timestamp with time zone NOT NULL,
    resolved_at timestamp with time zone,
    exact_ids jsonb NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT lifecycle_faults_check CHECK ((((state = 'OPEN'::text) AND (resolved_at IS NULL)) OR ((state = 'RESOLVED'::text) AND (resolved_at IS NOT NULL)))),
    CONSTRAINT lifecycle_faults_exact_ids_check CHECK ((jsonb_typeof(exact_ids) = 'object'::text)),
    CONSTRAINT lifecycle_faults_fault_code_check CHECK ((fault_code = ANY (ARRAY['PLAN_PAIR_INCOMPLETE'::text, 'ENTRY_PLAN_NOT_CONSUMED'::text, 'ENTRY_REQUEST_NOT_DISPATCHED'::text, 'ENTRY_EXECUTION_AMBIGUOUS'::text, 'POSITION_LINEAGE_INCOMPLETE'::text, 'POSITION_WITHOUT_EXIT_PLAN'::text, 'POSITION_WITHOUT_EXIT_OWNER'::text, 'POSITION_WITHOUT_CONFIRMED_INITIAL_PROTECTION'::text, 'EXIT_REQUEST_NOT_DISPATCHED'::text, 'EXIT_EXECUTION_AMBIGUOUS'::text, 'EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN'::text, 'EXCHANGE_POSITION_MODE_MISMATCH'::text, 'EXCHANGE_STATE_DIVERGED'::text, 'CAPITAL_RESERVATION_STUCK'::text, 'UNIVERSAL_ENTRY_OBSERVER_RUNTIME_ERROR'::text]))),
    CONSTRAINT lifecycle_faults_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT lifecycle_faults_severity_check CHECK ((severity = ANY (ARRAY['WARNING'::text, 'ERROR'::text, 'CRITICAL'::text]))),
    CONSTRAINT lifecycle_faults_state_check CHECK ((state = ANY (ARRAY['OPEN'::text, 'RESOLVED'::text])))
);


--
-- Name: owner_manual_interventions; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.owner_manual_interventions (
    intervention_id text NOT NULL,
    position_id text,
    trade_id text,
    occurred_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    action text NOT NULL,
    command_id text,
    exchange_order_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    before_state jsonb NOT NULL,
    after_state jsonb NOT NULL,
    provenance jsonb NOT NULL
);


--
-- Name: plan_consumptions; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.plan_consumptions (
    plan_consumption_id text NOT NULL,
    plan_kind text NOT NULL,
    entry_plan_fingerprint text,
    exit_plan_fingerprint text,
    strategy_activation_id text NOT NULL,
    consumer_kind text NOT NULL,
    consumer_instance_id text NOT NULL,
    loaded_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    status text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT plan_consumptions_check CHECK ((((plan_kind = 'ENTRY'::text) AND (entry_plan_fingerprint IS NOT NULL) AND (exit_plan_fingerprint IS NULL) AND (consumer_kind = 'ENTRY_ENGINE'::text)) OR ((plan_kind = 'EXIT'::text) AND (exit_plan_fingerprint IS NOT NULL) AND (entry_plan_fingerprint IS NULL) AND (consumer_kind = 'EXIT_ENGINE'::text)))),
    CONSTRAINT plan_consumptions_consumer_kind_check CHECK ((consumer_kind = ANY (ARRAY['ENTRY_ENGINE'::text, 'EXIT_ENGINE'::text]))),
    CONSTRAINT plan_consumptions_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT plan_consumptions_plan_kind_check CHECK ((plan_kind = ANY (ARRAY['ENTRY'::text, 'EXIT'::text]))),
    CONSTRAINT plan_consumptions_status_check CHECK ((status = ANY (ARRAY['LOADED'::text, 'STALE'::text, 'ERROR'::text])))
);


--
-- Name: position_exit_claims; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.position_exit_claims (
    claim_id text NOT NULL,
    strategy_position_id text NOT NULL,
    exit_plan_fingerprint text NOT NULL,
    strategy_activation_id text NOT NULL,
    consumer_instance_id text NOT NULL,
    claimed_at timestamp with time zone NOT NULL,
    last_seen_at timestamp with time zone NOT NULL,
    status text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT position_exit_claims_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT position_exit_claims_status_check CHECK ((status = ANY (ARRAY['CLAIMED'::text, 'STALE'::text, 'ERROR'::text])))
);


--
-- Name: position_lifecycle_events; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.position_lifecycle_events (
    lifecycle_event_id text NOT NULL,
    position_id text NOT NULL,
    trade_id text NOT NULL,
    event_type text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    exact_ids jsonb NOT NULL,
    payload jsonb NOT NULL,
    provenance jsonb NOT NULL
);


--
-- Name: position_mode_states; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.position_mode_states (
    position_mode_state_ref text NOT NULL,
    exchange text NOT NULL,
    account_ref text NOT NULL,
    product_category text NOT NULL,
    instrument text NOT NULL,
    position_mode text NOT NULL,
    position_idx integer,
    observed_at timestamp with time zone NOT NULL,
    received_at timestamp with time zone NOT NULL,
    fresh_until timestamp with time zone NOT NULL,
    provenance jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    trading_account_id text,
    account_connector_id text,
    instrument_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL,
    CONSTRAINT position_mode_states_check CHECK ((fresh_until >= observed_at)),
    CONSTRAINT position_mode_states_position_idx_check CHECK (((position_idx IS NULL) OR (position_idx >= 0))),
    CONSTRAINT position_mode_states_position_mode_check CHECK ((position_mode = ANY (ARRAY['ONE_WAY'::text, 'HEDGE'::text, 'UNKNOWN'::text]))),
    CONSTRAINT position_mode_states_provenance_check CHECK ((jsonb_typeof(provenance) = 'object'::text))
);


--
-- Name: private_events; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.private_events (
    id bigint NOT NULL,
    received_at_epoch_ms bigint NOT NULL,
    topic text NOT NULL,
    message_id text NOT NULL,
    creation_time_ms bigint,
    payload_json text NOT NULL,
    trading_account_id text,
    account_connector_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL
);


--
-- Name: private_events_id_seq; Type: SEQUENCE; Schema: runtime; Owner: -
--

ALTER TABLE runtime.private_events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME runtime.private_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: protection_events; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.protection_events (
    protection_event_id text NOT NULL,
    position_id text NOT NULL,
    trade_id text NOT NULL,
    occurred_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    command_id text,
    protection_kind text NOT NULL,
    initiator text NOT NULL,
    stop_before numeric,
    stop_after numeric,
    take_profit_before numeric,
    take_profit_after numeric,
    trailing_before numeric,
    trailing_after numeric,
    trailing_distance numeric,
    exchange_order_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    execution_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    source_payload jsonb NOT NULL,
    provenance jsonb NOT NULL,
    CONSTRAINT protection_events_initiator_check CHECK ((initiator = ANY (ARRAY['ALGORITHM'::text, 'OWNER'::text, 'TECHNICAL_SAFETY'::text, 'EXCHANGE'::text, 'UNKNOWN'::text]))),
    CONSTRAINT protection_events_protection_kind_check CHECK ((protection_kind = ANY (ARRAY['INITIAL_HARD_STOP'::text, 'PROFIT_PROTECTION_STOP'::text, 'TRAILING_STOP'::text, 'TAKE_PROFIT'::text, 'OWNER_MODIFIED_STOP'::text, 'UNKNOWN'::text])))
);


--
-- Name: reconciliation_runs; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.reconciliation_runs (
    id bigint NOT NULL,
    started_at_epoch_ms bigint NOT NULL,
    finished_at_epoch_ms bigint NOT NULL,
    reason text NOT NULL,
    ok smallint NOT NULL,
    positions integer NOT NULL,
    orders integer NOT NULL,
    error text NOT NULL,
    trading_account_id text,
    account_connector_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL
);


--
-- Name: reconciliation_runs_id_seq; Type: SEQUENCE; Schema: runtime; Owner: -
--

ALTER TABLE runtime.reconciliation_runs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME runtime.reconciliation_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: recovered_closed_entry_cycles; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.recovered_closed_entry_cycles (
    reservation_id text NOT NULL,
    account_ref text NOT NULL,
    strategy_attempt_id text NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL,
    entry_order_id text NOT NULL,
    entry_execution_ids jsonb NOT NULL,
    exit_order_ids jsonb NOT NULL,
    exit_execution_ids jsonb NOT NULL,
    actual_qty numeric NOT NULL,
    entry_vwap numeric NOT NULL,
    exit_vwap numeric NOT NULL,
    entry_fee_actual numeric NOT NULL,
    exit_fee_actual numeric NOT NULL,
    gross_pnl numeric NOT NULL,
    net_without_funding numeric NOT NULL,
    economics_completeness text DEFAULT 'PARTIAL_NO_FUNDING'::text NOT NULL,
    evidence jsonb NOT NULL,
    recovered_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT recovered_closed_entry_cycles_actual_qty_check CHECK ((actual_qty > (0)::numeric)),
    CONSTRAINT recovered_closed_entry_cycles_entry_vwap_check CHECK ((entry_vwap > (0)::numeric)),
    CONSTRAINT recovered_closed_entry_cycles_exit_vwap_check CHECK ((exit_vwap > (0)::numeric)),
    CONSTRAINT recovered_closed_entry_cycles_side_check CHECK ((side = ANY (ARRAY['Buy'::text, 'Sell'::text])))
);


--
-- Name: schema_contract; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.schema_contract (
    component text NOT NULL,
    version text NOT NULL,
    applied_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: trade_commands; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.trade_commands (
    command_id text NOT NULL,
    command_type text NOT NULL,
    symbol text NOT NULL,
    payload_json text NOT NULL,
    state text NOT NULL,
    requested_at_epoch_ms bigint NOT NULL,
    started_at_epoch_ms bigint,
    finished_at_epoch_ms bigint,
    result_json text DEFAULT '{}'::text NOT NULL,
    error text DEFAULT ''::text NOT NULL,
    strategy_execution_binding_id text,
    execution_leg text,
    trading_account_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL
);


--
-- Name: trade_lifecycle_events; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.trade_lifecycle_events (
    lifecycle_event_id text NOT NULL,
    event_type text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    strategy_id text,
    strategy_version text,
    strategy_config_fingerprint text,
    strategy_activation_id text,
    signal_id text,
    strategy_attempt_id text,
    entry_decision_id text,
    entry_execution_request_id text,
    strategy_position_id text,
    exit_plan_fingerprint text,
    exit_decision_id text,
    exit_execution_request_id text,
    exact_ids jsonb NOT NULL,
    payload jsonb NOT NULL,
    provenance jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    exchange_position_slot_claim_id text,
    position_mode_state_ref text,
    capital_reservation_id text,
    CONSTRAINT trade_lifecycle_events_event_type_check CHECK ((event_type = ANY (ARRAY['STRATEGY_ACTIVATED'::text, 'PLANS_MATERIALIZED'::text, 'PLANS_PUBLISHED'::text, 'ENTRY_PLAN_CONSUMED'::text, 'STRATEGY_SIGNAL_CREATED'::text, 'ENTRY_ATTEMPT_CREATED'::text, 'POSITION_MODE_VALIDATED'::text, 'EXCHANGE_SLOT_CLAIMED'::text, 'CAPITAL_RESERVED'::text, 'ENTRY_DECIDED'::text, 'ENTRY_REQUEST_CREATED'::text, 'ENTRY_REQUEST_STATE_CHANGED'::text, 'ENTRY_REQUEST_DISPATCHED'::text, 'ENTRY_ORDER_ACKNOWLEDGED'::text, 'ENTRY_FILLED'::text, 'STRATEGY_POSITION_CREATED'::text, 'EXCHANGE_SLOT_BOUND'::text, 'INITIAL_PROTECTION_CONFIRMED'::text, 'EXIT_PLAN_BOUND'::text, 'EXIT_POSITION_CLAIMED'::text, 'EXIT_DECISION_CREATED'::text, 'EXIT_REQUEST_CREATED'::text, 'EXIT_REQUEST_DISPATCHED'::text, 'EXIT_MUTATION_ACKNOWLEDGED'::text, 'EXIT_MUTATION_CONFIRMED'::text, 'POSITION_CLOSED'::text, 'EXCHANGE_SLOT_RELEASED'::text, 'CAPITAL_RESERVATION_RELEASED'::text, 'ECONOMICS_FINALIZED'::text, 'LIFECYCLE_FAULT'::text, 'CRITICAL_FAULT_DELIVERY'::text]))),
    CONSTRAINT trade_lifecycle_events_exact_ids_check CHECK ((jsonb_typeof(exact_ids) = 'object'::text)),
    CONSTRAINT trade_lifecycle_events_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT trade_lifecycle_events_provenance_check CHECK ((jsonb_typeof(provenance) = 'object'::text))
);


--
-- Name: trade_settings; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.trade_settings (
    singleton smallint NOT NULL,
    stake_usdt text DEFAULT '10'::text NOT NULL,
    leverage integer DEFAULT 10 NOT NULL,
    enabled_symbols_json text DEFAULT '[]'::text NOT NULL,
    updated_at_epoch_ms bigint NOT NULL,
    entry_offset_pct text DEFAULT '0.00'::text NOT NULL,
    entry_limit_ttl_seconds integer DEFAULT 30 NOT NULL,
    auto_profit_protection boolean DEFAULT true NOT NULL,
    auto_trailing_stop boolean DEFAULT true NOT NULL,
    trailing_distance_pct text DEFAULT '0.30'::text NOT NULL,
    entry_policy text DEFAULT 'base_entry_v1'::text NOT NULL,
    CONSTRAINT trade_settings_singleton_check CHECK ((singleton = 1))
);


--
-- Name: trade_settings_history; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.trade_settings_history (
    id bigint NOT NULL,
    changed_at_epoch_ms bigint NOT NULL,
    old_settings jsonb NOT NULL,
    new_settings jsonb NOT NULL,
    source text NOT NULL,
    origin text NOT NULL,
    settings_version text NOT NULL
);


--
-- Name: trade_settings_history_id_seq; Type: SEQUENCE; Schema: runtime; Owner: -
--

ALTER TABLE runtime.trade_settings_history ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME runtime.trade_settings_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: wallet_latest; Type: TABLE; Schema: runtime; Owner: -
--

CREATE TABLE runtime.wallet_latest (
    singleton smallint NOT NULL,
    refreshed_at_epoch_ms bigint NOT NULL,
    total_equity text NOT NULL,
    wallet_balance text NOT NULL,
    available_balance text NOT NULL,
    payload_json text NOT NULL,
    trading_account_id text,
    account_connector_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL,
    CONSTRAINT wallet_latest_singleton_check CHECK ((singleton = 1))
);


--
-- Name: exchange_snapshots; Type: TABLE; Schema: safety; Owner: -
--

CREATE TABLE safety.exchange_snapshots (
    id bigint NOT NULL,
    at_epoch_ms bigint NOT NULL,
    ok smallint NOT NULL,
    clock_offset_ms bigint,
    rest_latency_ms double precision,
    public_stream_age_ms bigint,
    total_equity text,
    available_balance text,
    wallet_balance text,
    account_im_rate text,
    account_mm_rate text,
    open_positions integer,
    open_orders integer,
    position_modes_json text NOT NULL,
    leverages_json text NOT NULL,
    error text NOT NULL
);


--
-- Name: exchange_snapshots_id_seq; Type: SEQUENCE; Schema: safety; Owner: -
--

ALTER TABLE safety.exchange_snapshots ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME safety.exchange_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: system_events; Type: TABLE; Schema: safety; Owner: -
--

CREATE TABLE safety.system_events (
    id bigint NOT NULL,
    at_epoch_ms bigint NOT NULL,
    event text NOT NULL,
    severity text NOT NULL,
    details_json text NOT NULL
);


--
-- Name: system_events_id_seq; Type: SEQUENCE; Schema: safety; Owner: -
--

ALTER TABLE safety.system_events ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME safety.system_events_id_seq
    START WITH 1
INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: runs; Type: TABLE; Schema: strategy_dispatcher; Owner: -
--

CREATE TABLE strategy_dispatcher.runs (
    snapshot_id text NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    mayak_version text NOT NULL,
    architecture_version text NOT NULL,
    data_quality text NOT NULL,
    service_version text NOT NULL,
    profile_count integer NOT NULL,
    trading_effect text NOT NULL,
    payload jsonb NOT NULL,
    stored_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    market_context_id text,
    CONSTRAINT runs_trading_effect_check CHECK ((trading_effect = 'NONE'::text))
);


--
-- Name: context_links; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.context_links (
    context_link_id text NOT NULL,
    signal_id text NOT NULL,
    strategy_attempt_id text,
    link_type text NOT NULL,
    requirement_id text NOT NULL,
    context_type text,
    context_id text,
    plan_mode text,
    context_observed_at timestamp with time zone,
    linked_at timestamp with time zone NOT NULL,
    age_seconds double precision,
    quality text,
    status text NOT NULL,
    source_refs jsonb NOT NULL,
    provenance jsonb NOT NULL,
    CONSTRAINT context_links_check CHECK (((link_type = 'OBSERVED_CONTEXT'::text) OR ((link_type = 'CONSUMED_CONTEXT'::text) AND (strategy_attempt_id IS NOT NULL) AND (context_id IS NOT NULL) AND (context_observed_at IS NOT NULL)))),
    CONSTRAINT context_links_link_type_check CHECK ((link_type = ANY (ARRAY['OBSERVED_CONTEXT'::text, 'CONSUMED_CONTEXT'::text]))),
    CONSTRAINT context_links_plan_mode_check CHECK (((plan_mode IS NULL) OR (plan_mode = ANY (ARRAY['OBSERVE'::text, 'CONDITION'::text, 'RANKING'::text])))),
    CONSTRAINT context_links_provenance_check CHECK ((jsonb_typeof(provenance) = 'object'::text)),
    CONSTRAINT context_links_source_refs_check CHECK ((jsonb_typeof(source_refs) = 'array'::text))
);


--
-- Name: entry_decisions; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.entry_decisions (
    entry_decision_id text NOT NULL,
    strategy_attempt_id text NOT NULL,
    signal_id text NOT NULL,
    decision_code text NOT NULL,
    reason text NOT NULL,
    decided_at timestamp with time zone NOT NULL,
    capacity_snapshot_id text,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    capital_reservation_id text,
    exchange_position_slot_claim_id text,
    position_mode_state_ref text,
    strategy_execution_binding_id text,
    execution_leg text,
    trading_account_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL,
    CONSTRAINT entry_decisions_decision_code_check CHECK ((decision_code = ANY (ARRAY['ACCEPTED'::text, 'STRATEGY_CONDITION_REJECTED'::text, 'INSUFFICIENT_AVAILABLE_FUNDS'::text, 'EXCHANGE_POSITION_OWNERSHIP_CONFLICT'::text, 'OPERATIONAL_SAFETY_BLOCKED'::text, 'STALE_OR_UNKNOWN_REQUIRED_STATE'::text, 'EXPIRED'::text, 'CANCELLED'::text]))),
    CONSTRAINT entry_decisions_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text))
);


--
-- Name: entry_plans; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.entry_plans (
    entry_plan_fingerprint text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_version text NOT NULL,
    plan_json jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT entry_plans_check CHECK (((plan_json ->> 'entry_plan_fingerprint'::text) = entry_plan_fingerprint)),
    CONSTRAINT entry_plans_plan_json_check CHECK ((jsonb_typeof(plan_json) = 'object'::text))
);


--
-- Name: execution_dispatches; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.execution_dispatches (
    dispatch_id text NOT NULL,
    execution_request_id text NOT NULL,
    command_id text,
    state text NOT NULL,
    reason text NOT NULL,
    strategy_attempt_id text NOT NULL,
    entry_decision_id text NOT NULL,
    signal_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    exit_plan_fingerprint text,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    capital_reservation_id text,
    exchange_position_slot_claim_id text,
    position_mode_state_ref text,
    CONSTRAINT execution_dispatches_check CHECK ((((state = 'DISPATCHED'::text) AND (command_id IS NOT NULL) AND (exit_plan_fingerprint IS NOT NULL)) OR ((state = 'BLOCKED'::text) AND (command_id IS NULL)))),
    CONSTRAINT execution_dispatches_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT execution_dispatches_state_check CHECK ((state = ANY (ARRAY['DISPATCHED'::text, 'BLOCKED'::text])))
);


--
-- Name: execution_permission_events; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.execution_permission_events (
    execution_permission_event_id bigint NOT NULL,
    execution_permission_id text NOT NULL,
    occurred_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    enabled boolean NOT NULL,
    enabled_at timestamp with time zone,
    disabled_at timestamp with time zone,
    operator text NOT NULL,
    source text NOT NULL,
    reason text NOT NULL
);


--
-- Name: execution_permission_events_execution_permission_event_id_seq; Type: SEQUENCE; Schema: strategy_entry; Owner: -
--

ALTER TABLE strategy_entry.execution_permission_events ALTER COLUMN execution_permission_event_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME strategy_entry.execution_permission_events_execution_permission_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: execution_permissions; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.execution_permissions (
    execution_permission_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    enabled boolean NOT NULL,
    enabled_at timestamp with time zone,
    disabled_at timestamp with time zone,
    operator text NOT NULL,
    source text NOT NULL,
    change_reason text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT execution_permissions_check CHECK (((enabled AND (enabled_at IS NOT NULL) AND (disabled_at IS NULL)) OR ((NOT enabled) AND (disabled_at IS NOT NULL))))
);


--
-- Name: execution_request_state_events; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.execution_request_state_events (
    request_state_event_id text NOT NULL,
    execution_request_id text NOT NULL,
    state text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    reason text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT execution_request_state_events_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT execution_request_state_events_state_check CHECK ((state = ANY (ARRAY['REQUEST_PENDING'::text, 'REQUEST_DISPATCHED'::text, 'REQUEST_ACKNOWLEDGED'::text, 'REQUEST_EXPIRED'::text, 'REQUEST_CANCELLED'::text, 'REQUEST_RECONCILIATION_REQUIRED'::text, 'REQUEST_TERMINAL'::text])))
);


--
-- Name: execution_requests; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.execution_requests (
    execution_request_id text NOT NULL,
    strategy_attempt_id text NOT NULL,
    entry_decision_id text NOT NULL,
    signal_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    requested_at timestamp with time zone NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    exit_plan_fingerprint text,
    capital_reservation_id text,
    exchange_position_slot_claim_id text,
    position_mode_state_ref text,
    strategy_execution_binding_id text,
    execution_leg text,
    trading_account_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL,
    CONSTRAINT execution_requests_direction_check CHECK ((direction = ANY (ARRAY['LONG'::text, 'SHORT'::text]))),
    CONSTRAINT execution_requests_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text))
);


--
-- Name: exit_plans; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.exit_plans (
    exit_plan_fingerprint text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    exit_plan_version text NOT NULL,
    plan_json jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT exit_plans_check CHECK (((plan_json ->> 'exit_plan_fingerprint'::text) = exit_plan_fingerprint)),
    CONSTRAINT exit_plans_plan_json_check CHECK ((jsonb_typeof(plan_json) = 'object'::text))
);


--
-- Name: notifications; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.notifications (
    notification_id text NOT NULL,
    strategy_attempt_id text NOT NULL,
    signal_id text NOT NULL,
    kind text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    reason text NOT NULL,
    requested_amount numeric,
    available_amount numeric,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT notifications_direction_check CHECK ((direction = ANY (ARRAY['LONG'::text, 'SHORT'::text]))),
    CONSTRAINT notifications_kind_check CHECK ((kind = ANY (ARRAY['INSUFFICIENT_AVAILABLE_FUNDS'::text, 'OPERATIONAL_SAFETY_BLOCKED'::text, 'STALE_OR_UNKNOWN_REQUIRED_STATE'::text, 'EXCHANGE_REJECTED'::text, 'EXECUTION_FAILURE'::text]))),
    CONSTRAINT notifications_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text))
);


--
-- Name: paper_orders; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.paper_orders (
    paper_order_id text NOT NULL,
    execution_request_id text,
    strategy_activation_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    exit_plan_fingerprint text NOT NULL,
    signal_id text NOT NULL,
    strategy_attempt_id text NOT NULL,
    entry_decision_id text,
    symbol text NOT NULL,
    direction text NOT NULL,
    state text NOT NULL,
    order_type text NOT NULL,
    requested_at timestamp with time zone NOT NULL,
    reference_price numeric NOT NULL,
    limit_price numeric,
    expires_at timestamp with time zone,
    filled_at timestamp with time zone,
    filled_price numeric,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT paper_orders_direction_check CHECK ((direction = ANY (ARRAY['LONG'::text, 'SHORT'::text]))),
    CONSTRAINT paper_orders_order_type_check CHECK ((order_type = ANY (ARRAY['MARKET'::text, 'LIMIT_OFFSET'::text]))),
    CONSTRAINT paper_orders_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT paper_orders_reference_price_check CHECK ((reference_price > (0)::numeric)),
    CONSTRAINT paper_orders_state_check CHECK ((state = ANY (ARRAY['PENDING'::text, 'FILLED'::text, 'EXPIRED'::text, 'CANCELLED'::text])))
);


--
-- Name: paper_position_events; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.paper_position_events (
    paper_event_id bigint NOT NULL,
    paper_position_id text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    event_type text NOT NULL,
    price numeric,
    pnl_usdt numeric,
    return_pct numeric,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT paper_position_events_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text))
);


--
-- Name: paper_position_events_paper_event_id_seq; Type: SEQUENCE; Schema: strategy_entry; Owner: -
--

ALTER TABLE strategy_entry.paper_position_events ALTER COLUMN paper_event_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME strategy_entry.paper_position_events_paper_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: paper_positions; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.paper_positions (
    paper_position_id text NOT NULL,
    paper_order_id text,
    parent_position_id text,
    leg_type text NOT NULL,
    strategy_activation_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    exit_plan_fingerprint text NOT NULL,
    signal_id text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    state text NOT NULL,
    opened_at timestamp with time zone NOT NULL,
    entry_price numeric NOT NULL,
    stake_usdt numeric NOT NULL,
    leverage integer NOT NULL,
    notional_usdt numeric NOT NULL,
    quantity numeric NOT NULL,
    best_price numeric NOT NULL,
    mfe_pct numeric DEFAULT 0 NOT NULL,
    mae_pct numeric DEFAULT 0 NOT NULL,
    active_stop_price numeric,
    trailing_active boolean DEFAULT false NOT NULL,
    hedge_opened boolean DEFAULT false NOT NULL,
    closed_at timestamp with time zone,
    exit_price numeric,
    exit_reason text,
    gross_pnl_usdt numeric,
    gross_return_pct numeric,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT paper_positions_best_price_check CHECK ((best_price > (0)::numeric)),
    CONSTRAINT paper_positions_direction_check CHECK ((direction = ANY (ARRAY['LONG'::text, 'SHORT'::text]))),
    CONSTRAINT paper_positions_entry_price_check CHECK ((entry_price > (0)::numeric)),
    CONSTRAINT paper_positions_leg_type_check CHECK ((leg_type = ANY (ARRAY['PRIMARY'::text, 'HEDGE'::text]))),
    CONSTRAINT paper_positions_leverage_check CHECK ((leverage > 0)),
    CONSTRAINT paper_positions_notional_usdt_check CHECK ((notional_usdt > (0)::numeric)),
    CONSTRAINT paper_positions_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT paper_positions_quantity_check CHECK ((quantity > (0)::numeric)),
    CONSTRAINT paper_positions_stake_usdt_check CHECK ((stake_usdt > (0)::numeric)),
    CONSTRAINT paper_positions_state_check CHECK ((state = ANY (ARRAY['OPEN'::text, 'CLOSED'::text])))
);


--
-- Name: reverse_transitions; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.reverse_transitions (
    reverse_transition_id text NOT NULL,
    strategy_position_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    exit_plan_fingerprint text NOT NULL,
    strategy_activation_id text NOT NULL,
    signal_id text NOT NULL,
    source_strategy_attempt_id text NOT NULL,
    symbol text NOT NULL,
    from_direction text NOT NULL,
    to_direction text NOT NULL,
    state text NOT NULL,
    close_command_id text,
    open_strategy_attempt_id text,
    open_entry_decision_id text,
    open_execution_request_id text,
    requested_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    completed_at timestamp with time zone,
    payload jsonb NOT NULL,
    CONSTRAINT reverse_transitions_check CHECK ((from_direction <> to_direction)),
    CONSTRAINT reverse_transitions_from_direction_check CHECK ((from_direction = ANY (ARRAY['LONG'::text, 'SHORT'::text]))),
    CONSTRAINT reverse_transitions_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT reverse_transitions_state_check CHECK ((state = ANY (ARRAY['REQUESTED'::text, 'CLOSE_DISPATCHED'::text, 'FLAT_CONFIRMED'::text, 'OPEN_REQUESTED'::text, 'OPEN_DISPATCHED'::text, 'COMPLETED'::text, 'RECONCILIATION_REQUIRED'::text, 'FAILED'::text]))),
    CONSTRAINT reverse_transitions_to_direction_check CHECK ((to_direction = ANY (ARRAY['LONG'::text, 'SHORT'::text])))
);


--
-- Name: sensor_links; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.sensor_links (
    sensor_link_id text NOT NULL,
    signal_id text NOT NULL,
    strategy_attempt_id text,
    link_type text NOT NULL,
    sensor_id text NOT NULL,
    sensor_observed_at timestamp with time zone,
    linked_at timestamp with time zone NOT NULL,
    age_seconds double precision,
    quality text,
    status text NOT NULL,
    source_refs jsonb NOT NULL,
    provenance jsonb NOT NULL,
    CONSTRAINT sensor_links_check CHECK (((link_type = 'OBSERVED_SENSOR'::text) OR ((link_type = 'CONSUMED_SENSOR'::text) AND (strategy_attempt_id IS NOT NULL) AND (sensor_observed_at IS NOT NULL)))),
    CONSTRAINT sensor_links_link_type_check CHECK ((link_type = ANY (ARRAY['OBSERVED_SENSOR'::text, 'CONSUMED_SENSOR'::text]))),
    CONSTRAINT sensor_links_provenance_check CHECK ((jsonb_typeof(provenance) = 'object'::text)),
    CONSTRAINT sensor_links_source_refs_check CHECK ((jsonb_typeof(source_refs) = 'array'::text))
);


--
-- Name: shadow_parity_events; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.shadow_parity_events (
    parity_event_id text NOT NULL,
    parity_run_id text NOT NULL,
    causal_key text NOT NULL,
    event_at timestamp with time zone NOT NULL,
    legacy_payload jsonb NOT NULL,
    universal_payload jsonb NOT NULL,
    equivalent boolean NOT NULL,
    difference jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    category text NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    source_refs jsonb NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    CONSTRAINT shadow_parity_events_difference_check CHECK ((jsonb_typeof(difference) = 'object'::text)),
    CONSTRAINT shadow_parity_events_legacy_payload_check CHECK ((jsonb_typeof(legacy_payload) = 'object'::text)),
    CONSTRAINT shadow_parity_events_source_refs_check CHECK ((jsonb_typeof(source_refs) = 'array'::text)),
    CONSTRAINT shadow_parity_events_universal_payload_check CHECK ((jsonb_typeof(universal_payload) = 'object'::text))
);


--
-- Name: shadow_parity_runs; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.shadow_parity_runs (
    parity_run_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    baseline_source_commit text NOT NULL,
    universal_source_commit text NOT NULL,
    status text NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    summary jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    calibration_sha256 text NOT NULL,
    calibration_size integer NOT NULL,
    fact_source_id text NOT NULL,
    service_instance_id text NOT NULL,
    CONSTRAINT shadow_parity_runs_calibration_sha_check CHECK ((calibration_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT shadow_parity_runs_calibration_size_check CHECK ((calibration_size > 0)),
    CONSTRAINT shadow_parity_runs_finished_check CHECK (((finished_at IS NULL) OR (finished_at >= started_at))),
    CONSTRAINT shadow_parity_runs_status_check CHECK ((status = ANY (ARRAY['WARMUP'::text, 'PARITY_COMPARABLE'::text, 'PASS'::text, 'FAIL'::text, 'NOT_COMPARABLE'::text]))),
    CONSTRAINT shadow_parity_runs_summary_check CHECK ((jsonb_typeof(summary) = 'object'::text))
);


--
-- Name: strategy_activation_events; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.strategy_activation_events (
    activation_event_id bigint NOT NULL,
    activation_id text NOT NULL,
    occurred_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    enabled boolean NOT NULL,
    enabled_at timestamp with time zone NOT NULL,
    disabled_at timestamp with time zone,
    operator text NOT NULL,
    source text NOT NULL,
    reason text NOT NULL,
    scope jsonb NOT NULL,
    CONSTRAINT strategy_activation_events_scope_check CHECK ((jsonb_typeof(scope) = 'object'::text))
);


--
-- Name: strategy_activation_events_activation_event_id_seq; Type: SEQUENCE; Schema: strategy_entry; Owner: -
--

ALTER TABLE strategy_entry.strategy_activation_events ALTER COLUMN activation_event_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME strategy_entry.strategy_activation_events_activation_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: strategy_activations; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.strategy_activations (
    activation_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    enabled boolean NOT NULL,
    enabled_at timestamp with time zone NOT NULL,
    disabled_at timestamp with time zone,
    scope jsonb DEFAULT '{}'::jsonb NOT NULL,
    operator text NOT NULL,
source text NOT NULL,
    change_reason text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT strategy_activations_check CHECK (((enabled AND (disabled_at IS NULL)) OR ((NOT enabled) AND (disabled_at IS NOT NULL)))),
    CONSTRAINT strategy_activations_scope_check CHECK ((jsonb_typeof(scope) = 'object'::text))
);


--
-- Name: strategy_attempts; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.strategy_attempts (
    strategy_attempt_id text NOT NULL,
    signal_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    strategy_activation_id text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    payload jsonb NOT NULL,
    CONSTRAINT strategy_attempts_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text))
);


--
-- Name: strategy_cards; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.strategy_cards (
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    name text NOT NULL,
    description text NOT NULL,
    card_json jsonb NOT NULL,
    approved_at timestamp with time zone NOT NULL,
    approved_source text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT strategy_cards_card_json_check CHECK ((jsonb_typeof(card_json) = 'object'::text)),
    CONSTRAINT strategy_cards_check CHECK (((card_json ->> 'strategy_id'::text) = strategy_id)),
    CONSTRAINT strategy_cards_check1 CHECK (((card_json ->> 'strategy_version'::text) = strategy_version)),
    CONSTRAINT strategy_cards_check2 CHECK (((card_json ->> 'strategy_config_fingerprint'::text) = strategy_config_fingerprint))
);


--
-- Name: strategy_execution_bindings; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.strategy_execution_bindings (
    strategy_execution_binding_id text NOT NULL,
    strategy_activation_id text NOT NULL,
    execution_leg text NOT NULL,
    execution_role text NOT NULL,
    trading_account_id text NOT NULL,
    allocation_mode text DEFAULT 'DEDICATED_STRATEGY'::text NOT NULL,
    binding_state text NOT NULL,
    effective_from timestamp with time zone NOT NULL,
    effective_to timestamp with time zone,
    operator text NOT NULL,
    source text NOT NULL,
    reason text NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT strategy_execution_bindings_allocation_mode_check CHECK ((allocation_mode = 'DEDICATED_STRATEGY'::text)),
    CONSTRAINT strategy_execution_bindings_binding_state_check CHECK ((binding_state = ANY (ARRAY['ACTIVE'::text, 'INACTIVE'::text, 'RETIRED'::text]))),
    CONSTRAINT strategy_execution_bindings_check CHECK (((effective_to IS NULL) OR (effective_to > effective_from))),
    CONSTRAINT strategy_execution_bindings_execution_leg_check CHECK ((btrim(execution_leg) <> ''::text)),
    CONSTRAINT strategy_execution_bindings_execution_role_check CHECK ((btrim(execution_role) <> ''::text))
);


--
-- Name: strategy_signals; Type: TABLE; Schema: strategy_entry; Owner: -
--

CREATE TABLE strategy_entry.strategy_signals (
    signal_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    strategy_activation_id text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    detected_at timestamp with time zone NOT NULL,
    fact_id text NOT NULL,
    source_refs jsonb NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT strategy_signals_direction_check CHECK ((direction = ANY (ARRAY['LONG'::text, 'SHORT'::text]))),
    CONSTRAINT strategy_signals_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT strategy_signals_source_refs_check CHECK ((jsonb_typeof(source_refs) = 'array'::text))
);


--
-- Name: execution_dispatches; Type: TABLE; Schema: strategy_exit; Owner: -
--

CREATE TABLE strategy_exit.execution_dispatches (
    dispatch_id text NOT NULL,
    execution_request_id text NOT NULL,
    command_id text,
    state text NOT NULL,
    reason text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT execution_dispatches_check CHECK ((((state = 'DISPATCHED'::text) AND (command_id IS NOT NULL)) OR ((state = 'BLOCKED'::text) AND (command_id IS NULL)))),
    CONSTRAINT execution_dispatches_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text)),
    CONSTRAINT execution_dispatches_state_check CHECK ((state = ANY (ARRAY['DISPATCHED'::text, 'BLOCKED'::text])))
);


--
-- Name: execution_materialization_blocks; Type: TABLE; Schema: strategy_exit; Owner: -
--

CREATE TABLE strategy_exit.execution_materialization_blocks (
    block_id text NOT NULL,
    exit_decision_id text NOT NULL,
    strategy_position_id text NOT NULL,
    reason text NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT execution_materialization_blocks_payload_check CHECK ((jsonb_typeof(payload) = 'object'::text))
);


--
-- Name: execution_requests; Type: TABLE; Schema: strategy_exit; Owner: -
--

CREATE TABLE strategy_exit.execution_requests (
    execution_request_id text NOT NULL,
    exit_decision_id text NOT NULL,
    strategy_position_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    exit_plan_fingerprint text NOT NULL,
    account_ref text NOT NULL,
    exchange_position_key text NOT NULL,
    position_idx integer NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL,
    action_kind text NOT NULL,
    requested_mutation jsonb NOT NULL,
    requested_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    strategy_execution_binding_id text,
    execution_leg text,
    trading_account_id text,
    identity_status text DEFAULT 'LEGACY_UNKNOWN'::text NOT NULL,
    CONSTRAINT execution_requests_action_kind_check CHECK ((action_kind = ANY (ARRAY['SET_STOP'::text, 'SET_TP'::text, 'SET_PROTECTION'::text, 'SET_TRAILING'::text, 'REDUCE'::text, 'CLOSE'::text]))),
    CONSTRAINT execution_requests_direction_check CHECK ((direction = ANY (ARRAY['LONG'::text, 'SHORT'::text]))),
    CONSTRAINT execution_requests_position_idx_check CHECK ((position_idx >= 0)),
    CONSTRAINT execution_requests_requested_mutation_check CHECK ((jsonb_typeof(requested_mutation) = 'object'::text))
);


--
-- Name: exit_decisions; Type: TABLE; Schema: strategy_exit; Owner: -
--

CREATE TABLE strategy_exit.exit_decisions (
    exit_decision_id text NOT NULL,
    strategy_position_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    exit_plan_fingerprint text NOT NULL,
    observation_id text NOT NULL,
    rule_id text NOT NULL,
    rule_priority integer NOT NULL,
    repeat_policy text NOT NULL,
    action_kind text NOT NULL,
    requested_mutation jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    decided_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT exit_decisions_action_kind_check CHECK ((action_kind = ANY (ARRAY['SET_STOP'::text, 'SET_TP'::text, 'SET_PROTECTION'::text, 'SET_TRAILING'::text, 'REDUCE'::text, 'CLOSE'::text]))),
    CONSTRAINT exit_decisions_repeat_policy_check CHECK ((repeat_policy = ANY (ARRAY['ONCE_PER_POSITION'::text, 'EACH_MATCH'::text]))),
    CONSTRAINT exit_decisions_requested_mutation_check CHECK ((jsonb_typeof(requested_mutation) = 'object'::text)),
    CONSTRAINT exit_decisions_source_refs_check CHECK ((jsonb_typeof(source_refs) = 'array'::text))
);


--
-- Name: exit_observations; Type: TABLE; Schema: strategy_exit; Owner: -
--

CREATE TABLE strategy_exit.exit_observations (
    observation_id text NOT NULL,
    strategy_position_id text NOT NULL,
    exit_plan_fingerprint text NOT NULL,
    symbol text NOT NULL,
    event_kind text NOT NULL,
    event_at timestamp with time zone NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    received_at timestamp with time zone NOT NULL,
    attributes jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT exit_observations_attributes_check CHECK ((jsonb_typeof(attributes) = 'object'::text)),
    CONSTRAINT exit_observations_source_refs_check CHECK ((jsonb_typeof(source_refs) = 'array'::text))
);


--
-- Name: shadow_evaluations; Type: TABLE; Schema: strategy_exit; Owner: -
--

CREATE TABLE strategy_exit.shadow_evaluations (
    evaluation_id text NOT NULL,
    observation_id text NOT NULL,
    strategy_position_id text NOT NULL,
    exit_plan_fingerprint text NOT NULL,
    status text NOT NULL,
    reason text NOT NULL,
    matched_rule_ids jsonb NOT NULL,
    exit_decision_id text,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT shadow_evaluations_check CHECK ((((status = 'DECISION_CREATED'::text) AND (exit_decision_id IS NOT NULL)) OR ((status <> 'DECISION_CREATED'::text) AND (exit_decision_id IS NULL)))),
    CONSTRAINT shadow_evaluations_matched_rule_ids_check CHECK ((jsonb_typeof(matched_rule_ids) = 'array'::text)),
    CONSTRAINT shadow_evaluations_status_check CHECK ((status = ANY (ARRAY['NO_EXECUTABLE_EXIT_RULES'::text, 'NO_MATCH'::text, 'DECISION_CREATED'::text, 'BLOCKED'::text])))
);


--
-- Name: dispatcher_hold_context; Type: TABLE; Schema: supervisor; Owner: -
--

CREATE TABLE supervisor.dispatcher_hold_context (
    position_id text NOT NULL,
    assessment_id text NOT NULL,
    mayak_snapshot_id text,
    assessment_observed_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    profile_id text NOT NULL,
    profile_version text NOT NULL,
    dispatcher_status text NOT NULL,
    supervisor_state text NOT NULL,
    action text NOT NULL,
    context_type text NOT NULL,
    trading_effect text NOT NULL,
    payload jsonb NOT NULL,
    CONSTRAINT dispatcher_hold_context_context_type_check CHECK ((context_type = 'CONSUMED_CONTEXT'::text)),
    CONSTRAINT dispatcher_hold_context_trading_effect_check CHECK ((trading_effect = 'FULL_LIVE_V1'::text))
);


--
-- Name: exit_decisions; Type: TABLE; Schema: supervisor; Owner: -
--

CREATE TABLE supervisor.exit_decisions (
    decision_id text NOT NULL,
    position_id text NOT NULL,
    symbol text NOT NULL,
    decided_at timestamp with time zone NOT NULL,
    internal_reason text NOT NULL,
    hold_assessment_id text,
    mayak_snapshot_id text,
    supervisor_snapshot_id bigint,
    structural_event_id text,
    economics_snapshot_id bigint,
    close_command_id text,
    decision_json jsonb NOT NULL
);


--
-- Name: exit_economics_snapshots; Type: TABLE; Schema: supervisor; Owner: -
--

CREATE TABLE supervisor.exit_economics_snapshots (
    id bigint NOT NULL,
    position_id text NOT NULL,
    symbol text NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    purpose text NOT NULL,
    economics_json jsonb NOT NULL
);


--
-- Name: exit_economics_snapshots_id_seq; Type: SEQUENCE; Schema: supervisor; Owner: -
--

ALTER TABLE supervisor.exit_economics_snapshots ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME supervisor.exit_economics_snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: position_structure_handoff; Type: TABLE; Schema: supervisor; Owner: -
--

CREATE TABLE supervisor.position_structure_handoff (
    event_id text NOT NULL,
    position_id text NOT NULL,
    symbol text NOT NULL,
    structural_event text NOT NULL,
    observed_at timestamp with time zone NOT NULL,
    zone_id text NOT NULL,
    zone_role text NOT NULL,
    lower_price numeric NOT NULL,
    upper_price numeric NOT NULL,
    source_timeframe text NOT NULL,
    geometry_version text NOT NULL,
    signal_at timestamp with time zone NOT NULL,
    fill_at timestamp with time zone NOT NULL,
    provenance jsonb NOT NULL,
    CONSTRAINT position_structure_handoff_check CHECK ((observed_at >= signal_at)),
    CONSTRAINT position_structure_handoff_check1 CHECK ((observed_at >= fill_at)),
    CONSTRAINT position_structure_handoff_check2 CHECK ((lower_price <= upper_price)),
    CONSTRAINT position_structure_handoff_structural_event_check CHECK ((structural_event = ANY (ARRAY['protective_clean_break_against'::text, 'protective_hold_reclaim'::text, 'obstacle_clean_break_with'::text, 'obstacle_rejection_against'::text])))
);


--
-- Name: snapshots; Type: TABLE; Schema: supervisor; Owner: -
--

CREATE TABLE supervisor.snapshots (
    id bigint NOT NULL,
    observed_at_epoch_ms bigint NOT NULL,
    position_id text NOT NULL,
    symbol text NOT NULL,
    state text NOT NULL,
    shadow_action text NOT NULL,
    snapshot_json jsonb NOT NULL
);


--
-- Name: snapshots_id_seq; Type: SEQUENCE; Schema: supervisor; Owner: -
--

ALTER TABLE supervisor.snapshots ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME supervisor.snapshots_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: transitions; Type: TABLE; Schema: supervisor; Owner: -
--

CREATE TABLE supervisor.transitions (
    id bigint NOT NULL,
    observed_at_epoch_ms bigint NOT NULL,
    position_id text NOT NULL,
    symbol text NOT NULL,
    old_state text,
    new_state text NOT NULL,
    reason text NOT NULL,
    shadow_action text NOT NULL,
    snapshot_json jsonb NOT NULL
);


--
-- Name: transitions_id_seq; Type: SEQUENCE; Schema: supervisor; Owner: -
--

ALTER TABLE supervisor.transitions ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME supervisor.transitions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: execution_gate_events id; Type: DEFAULT; Schema: control; Owner: -
--

ALTER TABLE ONLY control.execution_gate_events ALTER COLUMN id SET DEFAULT nextval('control.execution_gate_events_id_seq'::regclass);


--
-- Name: events id; Type: DEFAULT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.events ALTER COLUMN id SET DEFAULT nextval('mayak_v2.events_id_seq'::regclass);


--
-- Name: snapshots id; Type: DEFAULT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.snapshots ALTER COLUMN id SET DEFAULT nextval('mayak_v2.snapshots_id_seq'::regclass);


--
-- Name: state_events id; Type: DEFAULT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.state_events ALTER COLUMN id SET DEFAULT nextval('mayak_v2.state_events_id_seq'::regclass);


--
-- Name: lifecycle_events lifecycle_events_pkey; Type: CONSTRAINT; Schema: analyst; Owner: -
--

ALTER TABLE ONLY analyst.lifecycle_events
    ADD CONSTRAINT lifecycle_events_pkey PRIMARY KEY (trade_id, event_type, event_id);


--
-- Name: trade_lifecycles trade_lifecycles_pkey; Type: CONSTRAINT; Schema: analyst; Owner: -
--

ALTER TABLE ONLY analyst.trade_lifecycles
    ADD CONSTRAINT trade_lifecycles_pkey PRIMARY KEY (trade_id);


--
-- Name: counterfactual_candidates counterfactual_candidates_pkey; Type: CONSTRAINT; Schema: analytics; Owner: -
--

ALTER TABLE ONLY analytics.counterfactual_candidates
    ADD CONSTRAINT counterfactual_candidates_pkey PRIMARY KEY (counterfactual_id);


--
-- Name: counterfactual_outcomes counterfactual_outcomes_counterfactual_id_evaluated_at_stat_key; Type: CONSTRAINT; Schema: analytics; Owner: -
--

ALTER TABLE ONLY analytics.counterfactual_outcomes
    ADD CONSTRAINT counterfactual_outcomes_counterfactual_id_evaluated_at_stat_key UNIQUE (counterfactual_id, evaluated_at, status);


--
-- Name: counterfactual_outcomes counterfactual_outcomes_pkey; Type: CONSTRAINT; Schema: analytics; Owner: -
--

ALTER TABLE ONLY analytics.counterfactual_outcomes
    ADD CONSTRAINT counterfactual_outcomes_pkey PRIMARY KEY (outcome_id);


--
-- Name: market_minutes market_minutes_pkey; Type: CONSTRAINT; Schema: analytics; Owner: -
--

ALTER TABLE ONLY analytics.market_minutes
    ADD CONSTRAINT market_minutes_pkey PRIMARY KEY (market_source_id, instrument_id, observed_minute);


--
-- Name: market_minutes market_minutes_r8c_linear_only; Type: CHECK CONSTRAINT; Schema: analytics; Owner: -
--

ALTER TABLE analytics.market_minutes
    ADD CONSTRAINT market_minutes_r8c_linear_only CHECK (((product_category = 'LINEAR'::text) OR ((open_interest_value IS NULL) AND (open_interest_change_15m_pct IS NULL) AND (open_interest_change_30m_pct IS NULL) AND (open_interest_change_60m_pct IS NULL) AND (open_interest_speed_5m_pct_per_min IS NULL) AND (open_interest_acceleration_5m_pct_per_min2 IS NULL) AND (funding_rate_change_from_previous IS NULL)))) NOT VALID;


--
-- Name: bot_events bot_events_pkey; Type: CONSTRAINT; Schema: control; Owner: -
--

ALTER TABLE ONLY control.bot_events
    ADD CONSTRAINT bot_events_pkey PRIMARY KEY (id);


--
-- Name: bots bots_pkey; Type: CONSTRAINT; Schema: control; Owner: -
--

ALTER TABLE ONLY control.bots
    ADD CONSTRAINT bots_pkey PRIMARY KEY (id);


--
-- Name: command_requests command_requests_idempotency_key_key; Type: CONSTRAINT; Schema: control; Owner: -
--

ALTER TABLE ONLY control.command_requests
    ADD CONSTRAINT command_requests_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: command_requests command_requests_pkey; Type: CONSTRAINT; Schema: control; Owner: -
--

ALTER TABLE ONLY control.command_requests
    ADD CONSTRAINT command_requests_pkey PRIMARY KEY (id);


--
-- Name: command_timings command_timings_pkey; Type: CONSTRAINT; Schema: control; Owner: -
--

ALTER TABLE ONLY control.command_timings
    ADD CONSTRAINT command_timings_pkey PRIMARY KEY (command_id);


--
-- Name: execution_gate_events execution_gate_events_pkey; Type: CONSTRAINT; Schema: control; Owner: -
--

ALTER TABLE ONLY control.execution_gate_events
    ADD CONSTRAINT execution_gate_events_pkey PRIMARY KEY (id);


--
-- Name: execution_gates execution_gates_pkey; Type: CONSTRAINT; Schema: control; Owner: -
--

ALTER TABLE ONLY control.execution_gates
    ADD CONSTRAINT execution_gates_pkey PRIMARY KEY (mode);


--
-- Name: live_arm_evidence live_arm_evidence_pkey; Type: CONSTRAINT; Schema: control; Owner: -
--

ALTER TABLE ONLY control.live_arm_evidence
    ADD CONSTRAINT live_arm_evidence_pkey PRIMARY KEY (evidence_id);


--
-- Name: live_arm_retirement_events live_arm_retirement_events_live_arm_session_id_key; Type: CONSTRAINT; Schema: control; Owner: -
--

ALTER TABLE ONLY control.live_arm_retirement_events
    ADD CONSTRAINT live_arm_retirement_events_live_arm_session_id_key UNIQUE (live_arm_session_id);


--
-- Name: live_arm_retirement_events live_arm_retirement_events_pkey; Type: CONSTRAINT; Schema: control; Owner: -
--

ALTER TABLE ONLY control.live_arm_retirement_events
    ADD CONSTRAINT live_arm_retirement_events_pkey PRIMARY KEY (event_id);


--
-- Name: live_arm_sessions live_arm_sessions_pkey; Type: CONSTRAINT; Schema: control; Owner: -
--

ALTER TABLE ONLY control.live_arm_sessions
    ADD CONSTRAINT live_arm_sessions_pkey PRIMARY KEY (live_arm_session_id);


--
-- Name: release_arm_incidents release_arm_incidents_pkey; Type: CONSTRAINT; Schema: control; Owner: -
--

ALTER TABLE ONLY control.release_arm_incidents
    ADD CONSTRAINT release_arm_incidents_pkey PRIMARY KEY (incident_id);


--
-- Name: coin_market_contexts coin_market_contexts_content_hash_key; Type: CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.coin_market_contexts
    ADD CONSTRAINT coin_market_contexts_content_hash_key UNIQUE (content_hash);


--
-- Name: coin_market_contexts coin_market_contexts_global_context_id_symbol_key; Type: CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.coin_market_contexts
    ADD CONSTRAINT coin_market_contexts_global_context_id_symbol_key UNIQUE (global_context_id, symbol);


--
-- Name: coin_market_contexts coin_market_contexts_pkey; Type: CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.coin_market_contexts
    ADD CONSTRAINT coin_market_contexts_pkey PRIMARY KEY (coin_context_id);


--
-- Name: coin_market_contexts coin_market_contexts_source_coin_context_id_key; Type: CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.coin_market_contexts
    ADD CONSTRAINT coin_market_contexts_source_coin_context_id_key UNIQUE (source_coin_context_id);


--
-- Name: global_market_contexts global_market_contexts_content_hash_key; Type: CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.global_market_contexts
    ADD CONSTRAINT global_market_contexts_content_hash_key UNIQUE (content_hash);


--
-- Name: global_market_contexts global_market_contexts_pkey; Type: CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.global_market_contexts
    ADD CONSTRAINT global_market_contexts_pkey PRIMARY KEY (global_context_id);


--
-- Name: global_market_contexts global_market_contexts_source_market_context_id_key; Type: CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.global_market_contexts
    ADD CONSTRAINT global_market_contexts_source_market_context_id_key UNIQUE (source_market_context_id);


--
-- Name: trading_capacity_snapshots trading_capacity_snapshots_content_hash_key; Type: CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.trading_capacity_snapshots
    ADD CONSTRAINT trading_capacity_snapshots_content_hash_key UNIQUE (content_hash);


--
-- Name: trading_capacity_snapshots trading_capacity_snapshots_identity_status_check; Type: CHECK CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE dispatcher_v2.trading_capacity_snapshots
    ADD CONSTRAINT trading_capacity_snapshots_identity_status_check CHECK ((identity_status = ANY (ARRAY['PROVEN'::text, 'LEGACY_UNKNOWN'::text]))) NOT VALID;


--
-- Name: trading_capacity_snapshots trading_capacity_snapshots_pkey; Type: CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.trading_capacity_snapshots
    ADD CONSTRAINT trading_capacity_snapshots_pkey PRIMARY KEY (capacity_snapshot_id);


--
-- Name: account_connectors account_connectors_account_connector_id_trading_account_id_key; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.account_connectors
    ADD CONSTRAINT account_connectors_account_connector_id_trading_account_id_key UNIQUE (account_connector_id, trading_account_id);


--
-- Name: account_connectors account_connectors_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.account_connectors
    ADD CONSTRAINT account_connectors_pkey PRIMARY KEY (account_connector_id);


--
-- Name: instruments instruments_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.instruments
    ADD CONSTRAINT instruments_pkey PRIMARY KEY (instrument_id);


--
-- Name: market_sources market_sources_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.market_sources
    ADD CONSTRAINT market_sources_pkey PRIMARY KEY (market_source_id);


--
-- Name: trading_accounts trading_accounts_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.trading_accounts
    ADD CONSTRAINT trading_accounts_pkey PRIMARY KEY (trading_account_id);


--
-- Name: venues venues_pkey; Type: CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.venues
    ADD CONSTRAINT venues_pkey PRIMARY KEY (venue_id);


--
-- Name: coin_market_contexts coin_market_contexts_content_hash_key; Type: CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.coin_market_contexts
    ADD CONSTRAINT coin_market_contexts_content_hash_key UNIQUE (content_hash);


--
-- Name: coin_market_contexts coin_market_contexts_pkey; Type: CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.coin_market_contexts
    ADD CONSTRAINT coin_market_contexts_pkey PRIMARY KEY (coin_context_id);


--
-- Name: coin_minutes coin_minutes_pkey; Type: CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.coin_minutes
    ADD CONSTRAINT coin_minutes_pkey PRIMARY KEY (observed_at, symbol);


--
-- Name: continuity_gaps continuity_gaps_pkey; Type: CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.continuity_gaps
    ADD CONSTRAINT continuity_gaps_pkey PRIMARY KEY (source, scope, gap_started_at, gap_ended_at);


--
-- Name: events events_event_type_reference_id_key; Type: CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.events
    ADD CONSTRAINT events_event_type_reference_id_key UNIQUE (event_type, reference_id);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: liquidations liquidations_pkey; Type: CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.liquidations
    ADD CONSTRAINT liquidations_pkey PRIMARY KEY (occurred_at, symbol, position_side, bankruptcy_price, executed_size);


--
-- Name: market_observation_alert_deliveries market_observation_alert_deliveries_alert_id_key; Type: CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.market_observation_alert_deliveries
    ADD CONSTRAINT market_observation_alert_deliveries_alert_id_key UNIQUE (alert_id);


--
-- Name: market_observation_alert_deliveries market_observation_alert_deliveries_pkey; Type: CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.market_observation_alert_deliveries
    ADD CONSTRAINT market_observation_alert_deliveries_pkey PRIMARY KEY (delivery_id);


--
-- Name: market_observation_alerts market_observation_alerts_content_hash_key; Type: CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.market_observation_alerts
    ADD CONSTRAINT market_observation_alerts_content_hash_key UNIQUE (content_hash);


--
-- Name: market_observation_alerts market_observation_alerts_pkey; Type: CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.market_observation_alerts
    ADD CONSTRAINT market_observation_alerts_pkey PRIMARY KEY (alert_id);


--
-- Name: observation_journal observation_journal_pkey; Type: CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.observation_journal
    ADD CONSTRAINT observation_journal_pkey PRIMARY KEY (snapshot_id);


--
-- Name: shared_market_contexts shared_market_contexts_content_hash_key; Type: CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.shared_market_contexts
    ADD CONSTRAINT shared_market_contexts_content_hash_key UNIQUE (content_hash);


--
-- Name: shared_market_contexts shared_market_contexts_pkey; Type: CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.shared_market_contexts
    ADD CONSTRAINT shared_market_contexts_pkey PRIMARY KEY (market_context_id);


--
-- Name: snapshots snapshots_pkey; Type: CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.snapshots
    ADD CONSTRAINT snapshots_pkey PRIMARY KEY (id);


--
-- Name: state_events state_events_pkey; Type: CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.state_events
    ADD CONSTRAINT state_events_pkey PRIMARY KEY (id);


--
-- Name: entry_dispatcher_shadow_decisions entry_dispatcher_shadow_decisions_pkey; Type: CONSTRAINT; Schema: monitoring; Owner: -
--

ALTER TABLE ONLY monitoring.entry_dispatcher_shadow_decisions
    ADD CONSTRAINT entry_dispatcher_shadow_decisions_pkey PRIMARY KEY (chain_id);


--
-- Name: entry_geometry_handoffs entry_geometry_handoffs_geometry_hash_key; Type: CONSTRAINT; Schema: monitoring; Owner: -
--

ALTER TABLE ONLY monitoring.entry_geometry_handoffs
    ADD CONSTRAINT entry_geometry_handoffs_geometry_hash_key UNIQUE (geometry_hash);


--
-- Name: entry_geometry_handoffs entry_geometry_handoffs_pkey; Type: CONSTRAINT; Schema: monitoring; Owner: -
--

ALTER TABLE ONLY monitoring.entry_geometry_handoffs
    ADD CONSTRAINT entry_geometry_handoffs_pkey PRIMARY KEY (geometry_handoff_id);


--
-- Name: entry_geometry_handoffs entry_geometry_handoffs_signal_id_key; Type: CONSTRAINT; Schema: monitoring; Owner: -
--

ALTER TABLE ONLY monitoring.entry_geometry_handoffs
    ADD CONSTRAINT entry_geometry_handoffs_signal_id_key UNIQUE (signal_id);


--
-- Name: health_events health_events_pkey; Type: CONSTRAINT; Schema: monitoring; Owner: -
--

ALTER TABLE ONLY monitoring.health_events
    ADD CONSTRAINT health_events_pkey PRIMARY KEY (id);


--
-- Name: opportunities opportunities_pkey; Type: CONSTRAINT; Schema: monitoring; Owner: -
--

ALTER TABLE ONLY monitoring.opportunities
    ADD CONSTRAINT opportunities_pkey PRIMARY KEY (signal_id);


--
-- Name: opportunity_events opportunity_events_pkey; Type: CONSTRAINT; Schema: monitoring; Owner: -
--

ALTER TABLE ONLY monitoring.opportunity_events
    ADD CONSTRAINT opportunity_events_pkey PRIMARY KEY (id);


--
-- Name: dispatcher_v2_event_links dispatcher_v2_event_links_identity_status_check; Type: CHECK CONSTRAINT; Schema: research_context; Owner: -
--

ALTER TABLE research_context.dispatcher_v2_event_links
    ADD CONSTRAINT dispatcher_v2_event_links_identity_status_check CHECK ((((identity_status = 'LEGACY_UNKNOWN'::text) AND (trading_account_id IS NULL)) OR ((identity_status = 'PROVEN'::text) AND (trading_account_id IS NOT NULL)))) NOT VALID;


--
-- Name: dispatcher_v2_event_links dispatcher_v2_event_links_pkey; Type: CONSTRAINT; Schema: research_context; Owner: -
--

ALTER TABLE ONLY research_context.dispatcher_v2_event_links
    ADD CONSTRAINT dispatcher_v2_event_links_pkey PRIMARY KEY (event_type, reference_id);


--
-- Name: event_links event_links_pkey; Type: CONSTRAINT; Schema: research_context; Owner: -
--

ALTER TABLE ONLY research_context.event_links
    ADD CONSTRAINT event_links_pkey PRIMARY KEY (event_type, reference_id);


--
-- Name: account_state_generations account_state_generations_identity_status_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.account_state_generations
    ADD CONSTRAINT account_state_generations_identity_status_check CHECK ((identity_status = ANY (ARRAY['PROVEN'::text, 'LEGACY_UNKNOWN'::text]))) NOT VALID;


--
-- Name: account_state_generations account_state_generations_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.account_state_generations
    ADD CONSTRAINT account_state_generations_pkey PRIMARY KEY (generation_id);


--
-- Name: capital_reservations capital_reservations_binding_identity_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.capital_reservations
    ADD CONSTRAINT capital_reservations_binding_identity_check CHECK ((((identity_status = 'LEGACY_UNKNOWN'::text) AND (strategy_execution_binding_id IS NULL) AND (execution_leg IS NULL) AND (trading_account_id IS NULL)) OR ((identity_status = 'PROVEN'::text) AND (strategy_execution_binding_id IS NOT NULL) AND (execution_leg IS NOT NULL) AND (trading_account_id IS NOT NULL)))) NOT VALID;


--
-- Name: capital_reservations capital_reservations_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.capital_reservations
    ADD CONSTRAINT capital_reservations_pkey PRIMARY KEY (reservation_id);


--
-- Name: capital_reservations capital_reservations_strategy_attempt_id_key; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.capital_reservations
    ADD CONSTRAINT capital_reservations_strategy_attempt_id_key UNIQUE (strategy_attempt_id);


--
-- Name: connection_events connection_events_identity_status_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.connection_events
    ADD CONSTRAINT connection_events_identity_status_check CHECK ((identity_status = ANY (ARRAY['PROVEN'::text, 'LEGACY_UNKNOWN'::text]))) NOT VALID;


--
-- Name: connection_events connection_events_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.connection_events
    ADD CONSTRAINT connection_events_pkey PRIMARY KEY (id);


--
-- Name: effective_protection_evidence effective_protection_evidence_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.effective_protection_evidence
    ADD CONSTRAINT effective_protection_evidence_pkey PRIMARY KEY (evidence_id);


--
-- Name: entry_cancel_intents entry_cancel_intents_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.entry_cancel_intents
    ADD CONSTRAINT entry_cancel_intents_pkey PRIMARY KEY (order_id, command_id);


--
-- Name: entry_decision_events entry_decision_events_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.entry_decision_events
    ADD CONSTRAINT entry_decision_events_pkey PRIMARY KEY (id);


--
-- Name: entry_decisions entry_decisions_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.entry_decisions
    ADD CONSTRAINT entry_decisions_pkey PRIMARY KEY (signal_id);


--
-- Name: entry_geometry_bindings entry_geometry_bindings_geometry_handoff_id_key; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.entry_geometry_bindings
    ADD CONSTRAINT entry_geometry_bindings_geometry_handoff_id_key UNIQUE (geometry_handoff_id);


--
-- Name: entry_geometry_bindings entry_geometry_bindings_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.entry_geometry_bindings
    ADD CONSTRAINT entry_geometry_bindings_pkey PRIMARY KEY (entry_command_id);


--
-- Name: entry_geometry_bindings entry_geometry_bindings_signal_id_key; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.entry_geometry_bindings
    ADD CONSTRAINT entry_geometry_bindings_signal_id_key UNIQUE (signal_id);


--
-- Name: exchange_order_history exchange_order_history_account_identity_consistency_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.exchange_order_history
    ADD CONSTRAINT exchange_order_history_account_identity_consistency_check CHECK ((((identity_status = 'PROVEN'::text) AND (trading_account_id IS NOT NULL) AND (account_connector_id IS NOT NULL)) OR ((identity_status = 'LEGACY_UNKNOWN'::text) AND (trading_account_id IS NULL) AND (account_connector_id IS NULL)))) NOT VALID;


--
-- Name: exchange_order_history exchange_order_history_identity_status_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.exchange_order_history
    ADD CONSTRAINT exchange_order_history_identity_status_check CHECK ((identity_status = ANY (ARRAY['PROVEN'::text, 'LEGACY_UNKNOWN'::text]))) NOT VALID;


--
-- Name: exchange_order_history exchange_order_history_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.exchange_order_history
    ADD CONSTRAINT exchange_order_history_pkey PRIMARY KEY (order_id);


--
-- Name: exchange_position_slot_claims exchange_position_slot_claims_binding_identity_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.exchange_position_slot_claims
    ADD CONSTRAINT exchange_position_slot_claims_binding_identity_check CHECK ((((identity_status = 'LEGACY_UNKNOWN'::text) AND (strategy_execution_binding_id IS NULL) AND (execution_leg IS NULL) AND (trading_account_id IS NULL)) OR ((identity_status = 'PROVEN'::text) AND (strategy_execution_binding_id IS NOT NULL) AND (execution_leg IS NOT NULL) AND (trading_account_id IS NOT NULL)))) NOT VALID;


--
-- Name: exchange_position_slot_claims exchange_position_slot_claims_capital_reservation_id_key; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.exchange_position_slot_claims
    ADD CONSTRAINT exchange_position_slot_claims_capital_reservation_id_key UNIQUE (capital_reservation_id);


--
-- Name: exchange_position_slot_claims exchange_position_slot_claims_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.exchange_position_slot_claims
    ADD CONSTRAINT exchange_position_slot_claims_pkey PRIMARY KEY (exchange_position_slot_claim_id);


--
-- Name: exchange_position_slot_claims exchange_position_slot_claims_strategy_attempt_id_key; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.exchange_position_slot_claims
    ADD CONSTRAINT exchange_position_slot_claims_strategy_attempt_id_key UNIQUE (strategy_attempt_id);


--
-- Name: exchange_position_slot_claims exchange_position_slot_claims_strategy_position_id_key; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.exchange_position_slot_claims
    ADD CONSTRAINT exchange_position_slot_claims_strategy_position_id_key UNIQUE (strategy_position_id);


--
-- Name: executions executions_account_identity_consistency_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.executions
    ADD CONSTRAINT executions_account_identity_consistency_check CHECK ((((identity_status = 'PROVEN'::text) AND (trading_account_id IS NOT NULL) AND (account_connector_id IS NOT NULL)) OR ((identity_status = 'LEGACY_UNKNOWN'::text) AND (trading_account_id IS NULL) AND (account_connector_id IS NULL)))) NOT VALID;


--
-- Name: executions executions_identity_status_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.executions
    ADD CONSTRAINT executions_identity_status_check CHECK ((identity_status = ANY (ARRAY['PROVEN'::text, 'LEGACY_UNKNOWN'::text]))) NOT VALID;


--
-- Name: executions executions_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.executions
    ADD CONSTRAINT executions_pkey PRIMARY KEY (exec_id);


--
-- Name: hot_orders hot_orders_account_identity_consistency_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.hot_orders
    ADD CONSTRAINT hot_orders_account_identity_consistency_check CHECK ((((identity_status = 'PROVEN'::text) AND (trading_account_id IS NOT NULL) AND (account_connector_id IS NOT NULL)) OR ((identity_status = 'LEGACY_UNKNOWN'::text) AND (trading_account_id IS NULL) AND (account_connector_id IS NULL)))) NOT VALID;


--
-- Name: hot_orders hot_orders_identity_status_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.hot_orders
    ADD CONSTRAINT hot_orders_identity_status_check CHECK ((identity_status = ANY (ARRAY['PROVEN'::text, 'LEGACY_UNKNOWN'::text]))) NOT VALID;


--
-- Name: hot_orders hot_orders_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.hot_orders
    ADD CONSTRAINT hot_orders_pkey PRIMARY KEY (order_id);


--
-- Name: hot_positions hot_positions_account_identity_consistency_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.hot_positions
    ADD CONSTRAINT hot_positions_account_identity_consistency_check CHECK ((((identity_status = 'PROVEN'::text) AND (trading_account_id IS NOT NULL) AND (account_connector_id IS NOT NULL)) OR ((identity_status = 'LEGACY_UNKNOWN'::text) AND (trading_account_id IS NULL) AND (account_connector_id IS NULL)))) NOT VALID;


--
-- Name: hot_positions hot_positions_identity_status_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.hot_positions
    ADD CONSTRAINT hot_positions_identity_status_check CHECK ((identity_status = ANY (ARRAY['PROVEN'::text, 'LEGACY_UNKNOWN'::text]))) NOT VALID;


--
-- Name: hot_positions hot_positions_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.hot_positions
    ADD CONSTRAINT hot_positions_pkey PRIMARY KEY (symbol, position_idx);


--
-- Name: lifecycle_fault_deliveries lifecycle_fault_deliveries_fault_id_key; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.lifecycle_fault_deliveries
    ADD CONSTRAINT lifecycle_fault_deliveries_fault_id_key UNIQUE (fault_id);


--
-- Name: lifecycle_fault_deliveries lifecycle_fault_deliveries_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.lifecycle_fault_deliveries
    ADD CONSTRAINT lifecycle_fault_deliveries_pkey PRIMARY KEY (delivery_id);


--
-- Name: lifecycle_faults lifecycle_faults_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.lifecycle_faults
    ADD CONSTRAINT lifecycle_faults_pkey PRIMARY KEY (fault_id);


--
-- Name: m3_consumed_context m3_consumed_context_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.m3_consumed_context
    ADD CONSTRAINT m3_consumed_context_pkey PRIMARY KEY (signal_id);


--
-- Name: owner_manual_interventions owner_manual_interventions_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.owner_manual_interventions
    ADD CONSTRAINT owner_manual_interventions_pkey PRIMARY KEY (intervention_id);


--
-- Name: plan_consumptions plan_consumptions_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.plan_consumptions
    ADD CONSTRAINT plan_consumptions_pkey PRIMARY KEY (plan_consumption_id);


--
-- Name: plan_consumptions plan_consumptions_plan_kind_entry_plan_fingerprint_exit_pla_key; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.plan_consumptions
    ADD CONSTRAINT plan_consumptions_plan_kind_entry_plan_fingerprint_exit_pla_key UNIQUE NULLS NOT DISTINCT (plan_kind, entry_plan_fingerprint, exit_plan_fingerprint, strategy_activation_id, consumer_kind, consumer_instance_id);


--
-- Name: position_exit_attribution position_exit_attribution_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_exit_attribution
    ADD CONSTRAINT position_exit_attribution_pkey PRIMARY KEY (attribution_id);


--
-- Name: position_exit_attribution position_exit_attribution_position_id_key; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_exit_attribution
    ADD CONSTRAINT position_exit_attribution_position_id_key UNIQUE (position_id);


--
-- Name: position_exit_attribution position_exit_attribution_trade_id_key; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_exit_attribution
    ADD CONSTRAINT position_exit_attribution_trade_id_key UNIQUE (trade_id);


--
-- Name: position_exit_claims position_exit_claims_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_exit_claims
    ADD CONSTRAINT position_exit_claims_pkey PRIMARY KEY (claim_id);


--
-- Name: position_exit_claims position_exit_claims_strategy_position_id_key; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_exit_claims
    ADD CONSTRAINT position_exit_claims_strategy_position_id_key UNIQUE (strategy_position_id);


--
-- Name: position_lifecycle_events position_lifecycle_events_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_lifecycle_events
    ADD CONSTRAINT position_lifecycle_events_pkey PRIMARY KEY (lifecycle_event_id);


--
-- Name: position_mode_states position_mode_states_identity_status_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.position_mode_states
    ADD CONSTRAINT position_mode_states_identity_status_check CHECK ((identity_status = ANY (ARRAY['PROVEN'::text, 'LEGACY_UNKNOWN'::text]))) NOT VALID;


--
-- Name: position_mode_states position_mode_states_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_mode_states
    ADD CONSTRAINT position_mode_states_pkey PRIMARY KEY (position_mode_state_ref);


--
-- Name: position_ownership position_ownership_binding_identity_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.position_ownership
    ADD CONSTRAINT position_ownership_binding_identity_check CHECK ((((identity_status = 'LEGACY_UNKNOWN'::text) AND (strategy_execution_binding_id IS NULL) AND (execution_leg IS NULL) AND (trading_account_id IS NULL)) OR ((identity_status = 'PROVEN'::text) AND (strategy_execution_binding_id IS NOT NULL) AND (execution_leg IS NOT NULL) AND (trading_account_id IS NOT NULL)))) NOT VALID;


--
-- Name: position_ownership position_ownership_entry_command_id_key; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_ownership
    ADD CONSTRAINT position_ownership_entry_command_id_key UNIQUE (entry_command_id);


--
-- Name: position_ownership position_ownership_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_ownership
    ADD CONSTRAINT position_ownership_pkey PRIMARY KEY (position_id);


--
-- Name: position_ownership position_ownership_trade_id_key; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_ownership
    ADD CONSTRAINT position_ownership_trade_id_key UNIQUE (trade_id);


--
-- Name: private_events private_events_identity_status_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.private_events
    ADD CONSTRAINT private_events_identity_status_check CHECK ((identity_status = ANY (ARRAY['PROVEN'::text, 'LEGACY_UNKNOWN'::text]))) NOT VALID;


--
-- Name: private_events private_events_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.private_events
    ADD CONSTRAINT private_events_pkey PRIMARY KEY (id);


--
-- Name: protection_events protection_events_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.protection_events
    ADD CONSTRAINT protection_events_pkey PRIMARY KEY (protection_event_id);


--
-- Name: reconciliation_runs reconciliation_runs_identity_status_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.reconciliation_runs
    ADD CONSTRAINT reconciliation_runs_identity_status_check CHECK ((identity_status = ANY (ARRAY['PROVEN'::text, 'LEGACY_UNKNOWN'::text]))) NOT VALID;


--
-- Name: reconciliation_runs reconciliation_runs_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.reconciliation_runs
    ADD CONSTRAINT reconciliation_runs_pkey PRIMARY KEY (id);


--
-- Name: recovered_closed_entry_cycles recovered_closed_entry_cycles_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recovered_closed_entry_cycles
    ADD CONSTRAINT recovered_closed_entry_cycles_pkey PRIMARY KEY (reservation_id);


--
-- Name: schema_contract schema_contract_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.schema_contract
    ADD CONSTRAINT schema_contract_pkey PRIMARY KEY (component);


--
-- Name: trade_commands trade_commands_account_identity_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.trade_commands
    ADD CONSTRAINT trade_commands_account_identity_check CHECK ((((identity_status = 'LEGACY_UNKNOWN'::text) AND (trading_account_id IS NULL) AND (strategy_execution_binding_id IS NULL) AND (execution_leg IS NULL)) OR ((identity_status = 'PROVEN'::text) AND (trading_account_id IS NOT NULL) AND (((strategy_execution_binding_id IS NULL) AND (execution_leg IS NULL)) OR ((strategy_execution_binding_id IS NOT NULL) AND (execution_leg IS NOT NULL)))))) NOT VALID;


--
-- Name: trade_commands trade_commands_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.trade_commands
    ADD CONSTRAINT trade_commands_pkey PRIMARY KEY (command_id);


--
-- Name: trade_lifecycle_events trade_lifecycle_events_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.trade_lifecycle_events
    ADD CONSTRAINT trade_lifecycle_events_pkey PRIMARY KEY (lifecycle_event_id);


--
-- Name: trade_settings_history trade_settings_history_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.trade_settings_history
    ADD CONSTRAINT trade_settings_history_pkey PRIMARY KEY (id);


--
-- Name: trade_settings trade_settings_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.trade_settings
    ADD CONSTRAINT trade_settings_pkey PRIMARY KEY (singleton);


--
-- Name: wallet_latest wallet_latest_account_identity_consistency_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.wallet_latest
    ADD CONSTRAINT wallet_latest_account_identity_consistency_check CHECK ((((identity_status = 'PROVEN'::text) AND (trading_account_id IS NOT NULL) AND (account_connector_id IS NOT NULL)) OR ((identity_status = 'LEGACY_UNKNOWN'::text) AND (trading_account_id IS NULL) AND (account_connector_id IS NULL)))) NOT VALID;


--
-- Name: wallet_latest wallet_latest_identity_status_check; Type: CHECK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE runtime.wallet_latest
    ADD CONSTRAINT wallet_latest_identity_status_check CHECK ((identity_status = ANY (ARRAY['PROVEN'::text, 'LEGACY_UNKNOWN'::text]))) NOT VALID;


--
-- Name: wallet_latest wallet_latest_pkey; Type: CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.wallet_latest
    ADD CONSTRAINT wallet_latest_pkey PRIMARY KEY (singleton);


--
-- Name: exchange_snapshots exchange_snapshots_pkey; Type: CONSTRAINT; Schema: safety; Owner: -
--

ALTER TABLE ONLY safety.exchange_snapshots
    ADD CONSTRAINT exchange_snapshots_pkey PRIMARY KEY (id);


--
-- Name: system_events system_events_pkey; Type: CONSTRAINT; Schema: safety; Owner: -
--

ALTER TABLE ONLY safety.system_events
    ADD CONSTRAINT system_events_pkey PRIMARY KEY (id);


--
-- Name: assessments assessments_pkey; Type: CONSTRAINT; Schema: strategy_dispatcher; Owner: -
--

ALTER TABLE ONLY strategy_dispatcher.assessments
    ADD CONSTRAINT assessments_pkey PRIMARY KEY (assessment_id);


--
-- Name: runs runs_pkey; Type: CONSTRAINT; Schema: strategy_dispatcher; Owner: -
--

ALTER TABLE ONLY strategy_dispatcher.runs
    ADD CONSTRAINT runs_pkey PRIMARY KEY (snapshot_id);


--
-- Name: context_links context_links_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.context_links
    ADD CONSTRAINT context_links_pkey PRIMARY KEY (context_link_id);


--
-- Name: context_links context_links_signal_id_strategy_attempt_id_link_type_requi_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.context_links
    ADD CONSTRAINT context_links_signal_id_strategy_attempt_id_link_type_requi_key UNIQUE NULLS NOT DISTINCT (signal_id, strategy_attempt_id, link_type, requirement_id, context_id);


--
-- Name: entry_decisions entry_decisions_binding_identity_check; Type: CHECK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE strategy_entry.entry_decisions
    ADD CONSTRAINT entry_decisions_binding_identity_check CHECK ((((identity_status = 'LEGACY_UNKNOWN'::text) AND (strategy_execution_binding_id IS NULL) AND (execution_leg IS NULL) AND (trading_account_id IS NULL)) OR ((identity_status = 'PROVEN'::text) AND (strategy_execution_binding_id IS NOT NULL) AND (execution_leg IS NOT NULL) AND (trading_account_id IS NOT NULL)))) NOT VALID;


--
-- Name: entry_decisions entry_decisions_entry_decision_id_strategy_attempt_id_signa_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.entry_decisions
    ADD CONSTRAINT entry_decisions_entry_decision_id_strategy_attempt_id_signa_key UNIQUE (entry_decision_id, strategy_attempt_id, signal_id);


--
-- Name: entry_decisions entry_decisions_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.entry_decisions
    ADD CONSTRAINT entry_decisions_pkey PRIMARY KEY (entry_decision_id);


--
-- Name: entry_plans entry_plans_entry_plan_fingerprint_strategy_id_strategy_ver_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.entry_plans
    ADD CONSTRAINT entry_plans_entry_plan_fingerprint_strategy_id_strategy_ver_key UNIQUE (entry_plan_fingerprint, strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: entry_plans entry_plans_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.entry_plans
    ADD CONSTRAINT entry_plans_pkey PRIMARY KEY (entry_plan_fingerprint);


--
-- Name: execution_dispatches execution_dispatches_command_id_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_dispatches
    ADD CONSTRAINT execution_dispatches_command_id_key UNIQUE (command_id);


--
-- Name: execution_dispatches execution_dispatches_execution_request_id_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_dispatches
    ADD CONSTRAINT execution_dispatches_execution_request_id_key UNIQUE (execution_request_id);


--
-- Name: execution_dispatches execution_dispatches_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_dispatches
    ADD CONSTRAINT execution_dispatches_pkey PRIMARY KEY (dispatch_id);


--
-- Name: execution_permission_events execution_permission_events_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_permission_events
    ADD CONSTRAINT execution_permission_events_pkey PRIMARY KEY (execution_permission_event_id);


--
-- Name: execution_permissions execution_permissions_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_permissions
    ADD CONSTRAINT execution_permissions_pkey PRIMARY KEY (execution_permission_id);


--
-- Name: execution_permissions execution_permissions_strategy_id_strategy_version_strategy_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_permissions
    ADD CONSTRAINT execution_permissions_strategy_id_strategy_version_strategy_key UNIQUE (strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: execution_request_state_events execution_request_state_events_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_request_state_events
    ADD CONSTRAINT execution_request_state_events_pkey PRIMARY KEY (request_state_event_id);


--
-- Name: execution_requests execution_requests_binding_identity_check; Type: CHECK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE strategy_entry.execution_requests
    ADD CONSTRAINT execution_requests_binding_identity_check CHECK ((((identity_status = 'LEGACY_UNKNOWN'::text) AND (strategy_execution_binding_id IS NULL) AND (execution_leg IS NULL) AND (trading_account_id IS NULL)) OR ((identity_status = 'PROVEN'::text) AND (strategy_execution_binding_id IS NOT NULL) AND (execution_leg IS NOT NULL) AND (trading_account_id IS NOT NULL)))) NOT VALID;


--
-- Name: execution_requests execution_requests_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_requests
    ADD CONSTRAINT execution_requests_pkey PRIMARY KEY (execution_request_id);


--
-- Name: exit_plans exit_plans_exit_plan_fingerprint_strategy_id_strategy_versi_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.exit_plans
    ADD CONSTRAINT exit_plans_exit_plan_fingerprint_strategy_id_strategy_versi_key UNIQUE (exit_plan_fingerprint, strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: exit_plans exit_plans_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.exit_plans
    ADD CONSTRAINT exit_plans_pkey PRIMARY KEY (exit_plan_fingerprint);


--
-- Name: notifications notifications_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.notifications
    ADD CONSTRAINT notifications_pkey PRIMARY KEY (notification_id);


--
-- Name: paper_orders paper_orders_execution_request_id_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.paper_orders
    ADD CONSTRAINT paper_orders_execution_request_id_key UNIQUE (execution_request_id);


--
-- Name: paper_orders paper_orders_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.paper_orders
    ADD CONSTRAINT paper_orders_pkey PRIMARY KEY (paper_order_id);


--
-- Name: paper_position_events paper_position_events_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.paper_position_events
    ADD CONSTRAINT paper_position_events_pkey PRIMARY KEY (paper_event_id);


--
-- Name: paper_positions paper_positions_paper_order_id_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.paper_positions
    ADD CONSTRAINT paper_positions_paper_order_id_key UNIQUE (paper_order_id);


--
-- Name: paper_positions paper_positions_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.paper_positions
    ADD CONSTRAINT paper_positions_pkey PRIMARY KEY (paper_position_id);


--
-- Name: reverse_transitions reverse_transitions_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.reverse_transitions
    ADD CONSTRAINT reverse_transitions_pkey PRIMARY KEY (reverse_transition_id);


--
-- Name: reverse_transitions reverse_transitions_source_strategy_attempt_id_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.reverse_transitions
    ADD CONSTRAINT reverse_transitions_source_strategy_attempt_id_key UNIQUE (source_strategy_attempt_id);


--
-- Name: sensor_links sensor_links_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.sensor_links
    ADD CONSTRAINT sensor_links_pkey PRIMARY KEY (sensor_link_id);


--
-- Name: sensor_links sensor_links_signal_id_strategy_attempt_id_link_type_sensor_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.sensor_links
    ADD CONSTRAINT sensor_links_signal_id_strategy_attempt_id_link_type_sensor_key UNIQUE NULLS NOT DISTINCT (signal_id, strategy_attempt_id, link_type, sensor_id);


--
-- Name: shadow_parity_events shadow_parity_events_parity_run_id_causal_key_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.shadow_parity_events
    ADD CONSTRAINT shadow_parity_events_parity_run_id_causal_key_key UNIQUE (parity_run_id, causal_key);


--
-- Name: shadow_parity_events shadow_parity_events_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.shadow_parity_events
    ADD CONSTRAINT shadow_parity_events_pkey PRIMARY KEY (parity_event_id);


--
-- Name: shadow_parity_runs shadow_parity_runs_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.shadow_parity_runs
    ADD CONSTRAINT shadow_parity_runs_pkey PRIMARY KEY (parity_run_id);


--
-- Name: strategy_activation_events strategy_activation_events_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_activation_events
    ADD CONSTRAINT strategy_activation_events_pkey PRIMARY KEY (activation_event_id);


--
-- Name: strategy_activations strategy_activations_activation_id_strategy_id_strategy_ver_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_activations
    ADD CONSTRAINT strategy_activations_activation_id_strategy_id_strategy_ver_key UNIQUE (activation_id, strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: strategy_activations strategy_activations_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_activations
    ADD CONSTRAINT strategy_activations_pkey PRIMARY KEY (activation_id);


--
-- Name: strategy_attempts strategy_attempts_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_attempts
    ADD CONSTRAINT strategy_attempts_pkey PRIMARY KEY (strategy_attempt_id);


--
-- Name: strategy_attempts strategy_attempts_strategy_attempt_id_signal_id_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_attempts
    ADD CONSTRAINT strategy_attempts_strategy_attempt_id_signal_id_key UNIQUE (strategy_attempt_id, signal_id);


--
-- Name: strategy_attempts strategy_attempts_strategy_attempt_id_signal_id_strategy_i_key1; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_attempts
    ADD CONSTRAINT strategy_attempts_strategy_attempt_id_signal_id_strategy_i_key1 UNIQUE (strategy_attempt_id, signal_id, strategy_id, strategy_version);


--
-- Name: strategy_attempts strategy_attempts_strategy_attempt_id_signal_id_strategy_id_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_attempts
    ADD CONSTRAINT strategy_attempts_strategy_attempt_id_signal_id_strategy_id_key UNIQUE (strategy_attempt_id, signal_id, strategy_id, strategy_version, strategy_config_fingerprint, entry_plan_fingerprint);


--
-- Name: strategy_cards strategy_cards_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_cards
    ADD CONSTRAINT strategy_cards_pkey PRIMARY KEY (strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: strategy_cards strategy_cards_strategy_id_strategy_version_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_cards
    ADD CONSTRAINT strategy_cards_strategy_id_strategy_version_key UNIQUE (strategy_id, strategy_version);


--
-- Name: strategy_execution_bindings strategy_execution_bindings_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_execution_bindings
    ADD CONSTRAINT strategy_execution_bindings_pkey PRIMARY KEY (strategy_execution_binding_id);


--
-- Name: strategy_signals strategy_signals_pkey; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_signals
    ADD CONSTRAINT strategy_signals_pkey PRIMARY KEY (signal_id);


--
-- Name: strategy_signals strategy_signals_signal_id_strategy_id_strategy_version_st_key1; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_signals
    ADD CONSTRAINT strategy_signals_signal_id_strategy_id_strategy_version_st_key1 UNIQUE (signal_id, strategy_id, strategy_version, strategy_config_fingerprint, entry_plan_fingerprint);


--
-- Name: strategy_signals strategy_signals_signal_id_strategy_id_strategy_version_str_key; Type: CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_signals
    ADD CONSTRAINT strategy_signals_signal_id_strategy_id_strategy_version_str_key UNIQUE (signal_id, strategy_id, strategy_version, strategy_config_fingerprint, entry_plan_fingerprint, strategy_activation_id);


--
-- Name: execution_dispatches execution_dispatches_command_id_key; Type: CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.execution_dispatches
    ADD CONSTRAINT execution_dispatches_command_id_key UNIQUE (command_id);


--
-- Name: execution_dispatches execution_dispatches_execution_request_id_key; Type: CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.execution_dispatches
    ADD CONSTRAINT execution_dispatches_execution_request_id_key UNIQUE (execution_request_id);


--
-- Name: execution_dispatches execution_dispatches_pkey; Type: CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.execution_dispatches
    ADD CONSTRAINT execution_dispatches_pkey PRIMARY KEY (dispatch_id);


--
-- Name: execution_materialization_blocks execution_materialization_blocks_exit_decision_id_key; Type: CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.execution_materialization_blocks
    ADD CONSTRAINT execution_materialization_blocks_exit_decision_id_key UNIQUE (exit_decision_id);


--
-- Name: execution_materialization_blocks execution_materialization_blocks_pkey; Type: CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.execution_materialization_blocks
    ADD CONSTRAINT execution_materialization_blocks_pkey PRIMARY KEY (block_id);


--
-- Name: execution_requests execution_requests_execution_request_id_exit_decision_id_st_key; Type: CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.execution_requests
    ADD CONSTRAINT execution_requests_execution_request_id_exit_decision_id_st_key UNIQUE (execution_request_id, exit_decision_id, strategy_position_id);


--
-- Name: execution_requests execution_requests_pkey; Type: CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.execution_requests
    ADD CONSTRAINT execution_requests_pkey PRIMARY KEY (execution_request_id);


--
-- Name: exit_decisions exit_decisions_exit_decision_id_strategy_position_id_strate_key; Type: CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.exit_decisions
    ADD CONSTRAINT exit_decisions_exit_decision_id_strategy_position_id_strate_key UNIQUE (exit_decision_id, strategy_position_id, strategy_id, strategy_version, strategy_config_fingerprint, exit_plan_fingerprint);


--
-- Name: exit_decisions exit_decisions_pkey; Type: CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.exit_decisions
    ADD CONSTRAINT exit_decisions_pkey PRIMARY KEY (exit_decision_id);


--
-- Name: execution_requests exit_execution_requests_identity_check; Type: CHECK CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE strategy_exit.execution_requests
    ADD CONSTRAINT exit_execution_requests_identity_check CHECK ((((identity_status = 'LEGACY_UNKNOWN'::text) AND (strategy_execution_binding_id IS NULL) AND (execution_leg IS NULL) AND (trading_account_id IS NULL)) OR ((identity_status = 'PROVEN'::text) AND (strategy_execution_binding_id IS NOT NULL) AND (execution_leg IS NOT NULL) AND (trading_account_id IS NOT NULL)))) NOT VALID;


--
-- Name: exit_observations exit_observations_observation_id_strategy_position_id_exit__key; Type: CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.exit_observations
    ADD CONSTRAINT exit_observations_observation_id_strategy_position_id_exit__key UNIQUE (observation_id, strategy_position_id, exit_plan_fingerprint);


--
-- Name: exit_observations exit_observations_pkey; Type: CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.exit_observations
    ADD CONSTRAINT exit_observations_pkey PRIMARY KEY (observation_id);


--
-- Name: shadow_evaluations shadow_evaluations_pkey; Type: CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.shadow_evaluations
    ADD CONSTRAINT shadow_evaluations_pkey PRIMARY KEY (evaluation_id);


--
-- Name: dispatcher_hold_context dispatcher_hold_context_pkey; Type: CONSTRAINT; Schema: supervisor; Owner: -
--

ALTER TABLE ONLY supervisor.dispatcher_hold_context
    ADD CONSTRAINT dispatcher_hold_context_pkey PRIMARY KEY (position_id, assessment_id, supervisor_state);


--
-- Name: exit_decisions exit_decisions_pkey; Type: CONSTRAINT; Schema: supervisor; Owner: -
--

ALTER TABLE ONLY supervisor.exit_decisions
    ADD CONSTRAINT exit_decisions_pkey PRIMARY KEY (decision_id);


--
-- Name: exit_economics_snapshots exit_economics_snapshots_pkey; Type: CONSTRAINT; Schema: supervisor; Owner: -
--

ALTER TABLE ONLY supervisor.exit_economics_snapshots
    ADD CONSTRAINT exit_economics_snapshots_pkey PRIMARY KEY (id);


--
-- Name: position_structure_handoff position_structure_handoff_pkey; Type: CONSTRAINT; Schema: supervisor; Owner: -
--

ALTER TABLE ONLY supervisor.position_structure_handoff
    ADD CONSTRAINT position_structure_handoff_pkey PRIMARY KEY (event_id);


--
-- Name: snapshots snapshots_pkey; Type: CONSTRAINT; Schema: supervisor; Owner: -
--

ALTER TABLE ONLY supervisor.snapshots
    ADD CONSTRAINT snapshots_pkey PRIMARY KEY (id);


--
-- Name: transitions transitions_pkey; Type: CONSTRAINT; Schema: supervisor; Owner: -
--

ALTER TABLE ONLY supervisor.transitions
    ADD CONSTRAINT transitions_pkey PRIMARY KEY (id);


--
-- Name: analytics_market_minutes_instrument_at; Type: INDEX; Schema: analytics; Owner: -
--

CREATE INDEX analytics_market_minutes_instrument_at ON analytics.market_minutes USING btree (instrument_id, observed_minute DESC);


--
-- Name: analytics_market_minutes_source_at; Type: INDEX; Schema: analytics; Owner: -
--

CREATE INDEX analytics_market_minutes_source_at ON analytics.market_minutes USING btree (market_source_id, observed_minute DESC);


--
-- Name: execution_gate_events_at; Type: INDEX; Schema: control; Owner: -
--

CREATE INDEX execution_gate_events_at ON control.execution_gate_events USING btree (at_epoch_ms DESC);


--
-- Name: ix_live_arm_evidence_latest; Type: INDEX; Schema: control; Owner: -
--

CREATE INDEX ix_live_arm_evidence_latest ON control.live_arm_evidence USING btree (check_code, scope_type, scope_key, checked_at DESC, created_at DESC);


--
-- Name: ux_live_arm_sessions_active_scope; Type: INDEX; Schema: control; Owner: -
--

CREATE UNIQUE INDEX ux_live_arm_sessions_active_scope ON control.live_arm_sessions USING btree (strategy_id, strategy_version, strategy_config_fingerprint, strategy_activation_id, symbol) WHERE (state = 'ACTIVE'::text);


--
-- Name: ux_release_arm_incidents_one_open_release; Type: INDEX; Schema: control; Owner: -
--

CREATE UNIQUE INDEX ux_release_arm_incidents_one_open_release ON control.release_arm_incidents USING btree (release_commit) WHERE (state = 'OPEN'::text);


--
-- Name: dispatcher_v2_capacity_at; Type: INDEX; Schema: dispatcher_v2; Owner: -
--

CREATE INDEX dispatcher_v2_capacity_at ON dispatcher_v2.trading_capacity_snapshots USING btree (observed_at DESC);


--
-- Name: dispatcher_v2_coin_market_identity_at; Type: INDEX; Schema: dispatcher_v2; Owner: -
--

CREATE INDEX dispatcher_v2_coin_market_identity_at ON dispatcher_v2.coin_market_contexts USING btree (market_source_id, linear_instrument_id, observed_at DESC);


--
-- Name: dispatcher_v2_coin_source_snapshot; Type: INDEX; Schema: dispatcher_v2; Owner: -
--

CREATE INDEX dispatcher_v2_coin_source_snapshot ON dispatcher_v2.coin_market_contexts USING btree (source_mayak_snapshot_id, symbol);


--
-- Name: dispatcher_v2_coin_symbol_at; Type: INDEX; Schema: dispatcher_v2; Owner: -
--

CREATE INDEX dispatcher_v2_coin_symbol_at ON dispatcher_v2.coin_market_contexts USING btree (symbol, observed_at DESC);


--
-- Name: dispatcher_v2_global_at; Type: INDEX; Schema: dispatcher_v2; Owner: -
--

CREATE INDEX dispatcher_v2_global_at ON dispatcher_v2.global_market_contexts USING btree (observed_at DESC);


--
-- Name: identity_instruments_venue_product_symbol; Type: INDEX; Schema: identity; Owner: -
--

CREATE UNIQUE INDEX identity_instruments_venue_product_symbol ON identity.instruments USING btree (instrument_id, venue_id, product_category, external_symbol);


--
-- Name: identity_market_sources_venue_pair; Type: INDEX; Schema: identity; Owner: -
--

CREATE UNIQUE INDEX identity_market_sources_venue_pair ON identity.market_sources USING btree (market_source_id, venue_id);


--
-- Name: mayak_v2_coin_market_context_snapshot; Type: INDEX; Schema: mayak_v2; Owner: -
--

CREATE INDEX mayak_v2_coin_market_context_snapshot ON mayak_v2.coin_market_contexts USING btree (mayak_snapshot_id, symbol);


--
-- Name: mayak_v2_coin_market_context_symbol_at; Type: INDEX; Schema: mayak_v2; Owner: -
--

CREATE INDEX mayak_v2_coin_market_context_symbol_at ON mayak_v2.coin_market_contexts USING btree (symbol, observed_at DESC);


--
-- Name: mayak_v2_coin_market_identity_at; Type: INDEX; Schema: mayak_v2; Owner: -
--

CREATE INDEX mayak_v2_coin_market_identity_at ON mayak_v2.coin_market_contexts USING btree (market_source_id, linear_instrument_id, observed_at DESC);


--
-- Name: mayak_v2_coin_symbol_at; Type: INDEX; Schema: mayak_v2; Owner: -
--

CREATE INDEX mayak_v2_coin_symbol_at ON mayak_v2.coin_minutes USING btree (symbol, observed_at DESC);


--
-- Name: mayak_v2_continuity_gaps_started; Type: INDEX; Schema: mayak_v2; Owner: -
--

CREATE INDEX mayak_v2_continuity_gaps_started ON mayak_v2.continuity_gaps USING btree (gap_started_at DESC);


--
-- Name: mayak_v2_liquidations_at; Type: INDEX; Schema: mayak_v2; Owner: -
--

CREATE INDEX mayak_v2_liquidations_at ON mayak_v2.liquidations USING btree (occurred_at DESC);


--
-- Name: mayak_v2_market_observation_alert_class_at; Type: INDEX; Schema: mayak_v2; Owner: -
--

CREATE INDEX mayak_v2_market_observation_alert_class_at ON mayak_v2.market_observation_alerts USING btree (alert_class, observed_at DESC);


--
-- Name: mayak_v2_market_observation_alert_scope_at; Type: INDEX; Schema: mayak_v2; Owner: -
--

CREATE INDEX mayak_v2_market_observation_alert_scope_at ON mayak_v2.market_observation_alerts USING btree (scope_key, observed_at DESC);


--
-- Name: mayak_v2_observation_journal_at; Type: INDEX; Schema: mayak_v2; Owner: -
--

CREATE INDEX mayak_v2_observation_journal_at ON mayak_v2.observation_journal USING btree (observed_at DESC);


--
-- Name: mayak_v2_one_regular_per_minute; Type: INDEX; Schema: mayak_v2; Owner: -
--

CREATE UNIQUE INDEX mayak_v2_one_regular_per_minute ON mayak_v2.snapshots USING btree (regular_minute) WHERE (snapshot_kind = 'REGULAR'::text);


--
-- Name: mayak_v2_snapshots_at; Type: INDEX; Schema: mayak_v2; Owner: -
--

CREATE INDEX mayak_v2_snapshots_at ON mayak_v2.snapshots USING btree (observed_at DESC);


--
-- Name: entry_dispatcher_shadow_signal_at; Type: INDEX; Schema: monitoring; Owner: -
--

CREATE INDEX entry_dispatcher_shadow_signal_at ON monitoring.entry_dispatcher_shadow_decisions USING btree (signal_at DESC);


--
-- Name: ix_opportunities_state_symbol; Type: INDEX; Schema: monitoring; Owner: -
--

CREATE INDEX ix_opportunities_state_symbol ON monitoring.opportunities USING btree (state, symbol);


--
-- Name: dispatcher_v2_event_links_at; Type: INDEX; Schema: research_context; Owner: -
--

CREATE INDEX dispatcher_v2_event_links_at ON research_context.dispatcher_v2_event_links USING btree (occurred_at DESC);


--
-- Name: dispatcher_v2_event_links_global; Type: INDEX; Schema: research_context; Owner: -
--

CREATE INDEX dispatcher_v2_event_links_global ON research_context.dispatcher_v2_event_links USING btree (global_context_id);


--
-- Name: dispatcher_v2_event_links_signal; Type: INDEX; Schema: research_context; Owner: -
--

CREATE INDEX dispatcher_v2_event_links_signal ON research_context.dispatcher_v2_event_links USING btree (signal_id, occurred_at DESC);


--
-- Name: dispatcher_v2_event_links_symbol; Type: INDEX; Schema: research_context; Owner: -
--

CREATE INDEX dispatcher_v2_event_links_symbol ON research_context.dispatcher_v2_event_links USING btree (symbol, occurred_at DESC);


--
-- Name: research_context_event_links_at; Type: INDEX; Schema: research_context; Owner: -
--

CREATE INDEX research_context_event_links_at ON research_context.event_links USING btree (occurred_at DESC);


--
-- Name: entry_decision_events_signal_time; Type: INDEX; Schema: runtime; Owner: -
--

CREATE INDEX entry_decision_events_signal_time ON runtime.entry_decision_events USING btree (signal_id, observed_at_epoch_ms);


--
-- Name: ix_account_state_generations_account_started; Type: INDEX; Schema: runtime; Owner: -
--

CREATE INDEX ix_account_state_generations_account_started ON runtime.account_state_generations USING btree (account_ref, started_at DESC);


--
-- Name: ix_capital_reservations_account_active; Type: INDEX; Schema: runtime; Owner: -
--

CREATE INDEX ix_capital_reservations_account_active ON runtime.capital_reservations USING btree (account_ref, state, created_at) WHERE (state = ANY (ARRAY['RESERVED'::text, 'DISPATCHED'::text, 'PENDING_EXCHANGE_REFLECTION'::text, 'CONSUMED'::text, 'RECONCILIATION_REQUIRED'::text]));


--
-- Name: ix_capital_reservations_reserved_expiry; Type: INDEX; Schema: runtime; Owner: -
--

CREATE INDEX ix_capital_reservations_reserved_expiry ON runtime.capital_reservations USING btree (pre_dispatch_expires_at) WHERE (state = 'RESERVED'::text);


--
-- Name: ix_effective_protection_command; Type: INDEX; Schema: runtime; Owner: -
--

CREATE INDEX ix_effective_protection_command ON runtime.effective_protection_evidence USING btree (command_id, observed_at DESC) WHERE (command_id IS NOT NULL);


--
-- Name: ix_effective_protection_exact_scope_time; Type: INDEX; Schema: runtime; Owner: -
--

CREATE INDEX ix_effective_protection_exact_scope_time ON runtime.effective_protection_evidence USING btree (account_ref, symbol, position_idx, observed_at DESC, recorded_at DESC);


--
-- Name: ix_exchange_position_slot_claims_position; Type: INDEX; Schema: runtime; Owner: -
--

CREATE INDEX ix_exchange_position_slot_claims_position ON runtime.exchange_position_slot_claims USING btree (strategy_position_id) WHERE (strategy_position_id IS NOT NULL);


--
-- Name: ix_position_mode_states_scope_observed; Type: INDEX; Schema: runtime; Owner: -
--

CREATE INDEX ix_position_mode_states_scope_observed ON runtime.position_mode_states USING btree (account_ref, product_category, instrument, observed_at DESC);


--
-- Name: ix_recovered_closed_entry_at; Type: INDEX; Schema: runtime; Owner: -
--

CREATE INDEX ix_recovered_closed_entry_at ON runtime.recovered_closed_entry_cycles USING btree (recovered_at DESC);


--
-- Name: ux_exchange_order_history_trading_account_order; Type: INDEX; Schema: runtime; Owner: -
--

CREATE UNIQUE INDEX ux_exchange_order_history_trading_account_order ON runtime.exchange_order_history USING btree (trading_account_id, order_id) NULLS NOT DISTINCT;


--
-- Name: ux_exchange_position_slot_claims_active_slot; Type: INDEX; Schema: runtime; Owner: -
--

CREATE UNIQUE INDEX ux_exchange_position_slot_claims_active_slot ON runtime.exchange_position_slot_claims USING btree (exchange_position_key) WHERE (claim_state = ANY (ARRAY['CLAIMED'::text, 'BOUND'::text, 'RECONCILIATION_REQUIRED'::text]));


--
-- Name: ux_executions_trading_account_exec; Type: INDEX; Schema: runtime; Owner: -
--

CREATE UNIQUE INDEX ux_executions_trading_account_exec ON runtime.executions USING btree (trading_account_id, exec_id) NULLS NOT DISTINCT;


--
-- Name: ux_hot_orders_trading_account_order; Type: INDEX; Schema: runtime; Owner: -
--

CREATE UNIQUE INDEX ux_hot_orders_trading_account_order ON runtime.hot_orders USING btree (trading_account_id, order_id) NULLS NOT DISTINCT;


--
-- Name: ux_hot_positions_trading_account_slot; Type: INDEX; Schema: runtime; Owner: -
--

CREATE UNIQUE INDEX ux_hot_positions_trading_account_slot ON runtime.hot_positions USING btree (trading_account_id, symbol, position_idx) NULLS NOT DISTINCT;


--
-- Name: ux_position_ownership_active_exchange_slot; Type: INDEX; Schema: runtime; Owner: -
--

CREATE UNIQUE INDEX ux_position_ownership_active_exchange_slot ON runtime.position_ownership USING btree (exchange_position_key) WHERE ((state = ANY (ARRAY['OPEN'::text, 'RECONCILIATION_REQUIRED'::text])) AND (exchange_position_key IS NOT NULL));


--
-- Name: ux_recovered_closed_entry_order; Type: INDEX; Schema: runtime; Owner: -
--

CREATE UNIQUE INDEX ux_recovered_closed_entry_order ON runtime.recovered_closed_entry_cycles USING btree (account_ref, entry_order_id);


--
-- Name: ux_wallet_latest_trading_account_singleton; Type: INDEX; Schema: runtime; Owner: -
--

CREATE UNIQUE INDEX ux_wallet_latest_trading_account_singleton ON runtime.wallet_latest USING btree (trading_account_id, singleton) NULLS NOT DISTINCT;


--
-- Name: ix_safety_events_at; Type: INDEX; Schema: safety; Owner: -
--

CREATE INDEX ix_safety_events_at ON safety.system_events USING btree (at_epoch_ms);


--
-- Name: ix_safety_snapshots_at; Type: INDEX; Schema: safety; Owner: -
--

CREATE INDEX ix_safety_snapshots_at ON safety.exchange_snapshots USING btree (at_epoch_ms);


--
-- Name: strategy_dispatcher_assessments_at; Type: INDEX; Schema: strategy_dispatcher; Owner: -
--

CREATE INDEX strategy_dispatcher_assessments_at ON strategy_dispatcher.assessments USING btree (observed_at DESC);


--
-- Name: strategy_dispatcher_runs_at; Type: INDEX; Schema: strategy_dispatcher; Owner: -
--

CREATE INDEX strategy_dispatcher_runs_at ON strategy_dispatcher.runs USING btree (observed_at DESC);


--
-- Name: ix_execution_request_state_events_request_time; Type: INDEX; Schema: strategy_entry; Owner: -
--

CREATE INDEX ix_execution_request_state_events_request_time ON strategy_entry.execution_request_state_events USING btree (execution_request_id, occurred_at);


--
-- Name: ix_paper_orders_pending_symbol; Type: INDEX; Schema: strategy_entry; Owner: -
--

CREATE INDEX ix_paper_orders_pending_symbol ON strategy_entry.paper_orders USING btree (state, symbol, requested_at);


--
-- Name: ix_paper_positions_open_symbol; Type: INDEX; Schema: strategy_entry; Owner: -
--

CREATE INDEX ix_paper_positions_open_symbol ON strategy_entry.paper_positions USING btree (state, symbol, opened_at);


--
-- Name: ix_paper_positions_strategy; Type: INDEX; Schema: strategy_entry; Owner: -
--

CREATE INDEX ix_paper_positions_strategy ON strategy_entry.paper_positions USING btree (strategy_id, strategy_version, state, opened_at);


--
-- Name: ix_strategy_entry_activations_enabled; Type: INDEX; Schema: strategy_entry; Owner: -
--

CREATE INDEX ix_strategy_entry_activations_enabled ON strategy_entry.strategy_activations USING btree (enabled, strategy_id, strategy_version);


--
-- Name: ix_strategy_entry_attempts_signal; Type: INDEX; Schema: strategy_entry; Owner: -
--

CREATE INDEX ix_strategy_entry_attempts_signal ON strategy_entry.strategy_attempts USING btree (signal_id, strategy_attempt_id);


--
-- Name: ix_strategy_entry_context_signal; Type: INDEX; Schema: strategy_entry; Owner: -
--

CREATE INDEX ix_strategy_entry_context_signal ON strategy_entry.context_links USING btree (signal_id, link_type, requirement_id);


--
-- Name: ix_strategy_entry_decisions_attempt; Type: INDEX; Schema: strategy_entry; Owner: -
--

CREATE INDEX ix_strategy_entry_decisions_attempt ON strategy_entry.entry_decisions USING btree (strategy_attempt_id, decided_at);


--
-- Name: ix_strategy_entry_execution_dispatches_command; Type: INDEX; Schema: strategy_entry; Owner: -
--

CREATE INDEX ix_strategy_entry_execution_dispatches_command ON strategy_entry.execution_dispatches USING btree (command_id) WHERE (command_id IS NOT NULL);


--
-- Name: ix_strategy_entry_sensor_signal; Type: INDEX; Schema: strategy_entry; Owner: -
--

CREATE INDEX ix_strategy_entry_sensor_signal ON strategy_entry.sensor_links USING btree (signal_id, link_type, sensor_id);


--
-- Name: ix_strategy_entry_signals_symbol_time; Type: INDEX; Schema: strategy_entry; Owner: -
--

CREATE INDEX ix_strategy_entry_signals_symbol_time ON strategy_entry.strategy_signals USING btree (symbol, detected_at, signal_id);


--
-- Name: reverse_transitions_one_active_position; Type: INDEX; Schema: strategy_entry; Owner: -
--

CREATE UNIQUE INDEX reverse_transitions_one_active_position ON strategy_entry.reverse_transitions USING btree (strategy_position_id) WHERE (state <> ALL (ARRAY['COMPLETED'::text, 'FAILED'::text]));


--
-- Name: reverse_transitions_state_requested; Type: INDEX; Schema: strategy_entry; Owner: -
--

CREATE INDEX reverse_transitions_state_requested ON strategy_entry.reverse_transitions USING btree (state, requested_at);


--
-- Name: ux_paper_orders_strategy_attempt; Type: INDEX; Schema: strategy_entry; Owner: -
--

CREATE UNIQUE INDEX ux_paper_orders_strategy_attempt ON strategy_entry.paper_orders USING btree (strategy_attempt_id);


--
-- Name: ux_strategy_execution_bindings_active_leg; Type: INDEX; Schema: strategy_entry; Owner: -
--

CREATE UNIQUE INDEX ux_strategy_execution_bindings_active_leg ON strategy_entry.strategy_execution_bindings USING btree (strategy_activation_id, execution_leg) WHERE (binding_state = 'ACTIVE'::text);


--
-- Name: ux_strategy_execution_bindings_dedicated_account; Type: INDEX; Schema: strategy_entry; Owner: -
--

CREATE UNIQUE INDEX ux_strategy_execution_bindings_dedicated_account ON strategy_entry.strategy_execution_bindings USING btree (trading_account_id) WHERE ((binding_state = 'ACTIVE'::text) AND (allocation_mode = 'DEDICATED_STRATEGY'::text));


--
-- Name: supervisor_snapshots_position_time; Type: INDEX; Schema: supervisor; Owner: -
--

CREATE INDEX supervisor_snapshots_position_time ON supervisor.snapshots USING btree (position_id, observed_at_epoch_ms DESC);


--
-- Name: counterfactual_candidates counterfactual_candidates_immutable; Type: TRIGGER; Schema: analytics; Owner: -
--

CREATE TRIGGER counterfactual_candidates_immutable BEFORE DELETE OR UPDATE ON analytics.counterfactual_candidates FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: counterfactual_outcomes counterfactual_outcomes_immutable; Type: TRIGGER; Schema: analytics; Owner: -
--

CREATE TRIGGER counterfactual_outcomes_immutable BEFORE DELETE OR UPDATE ON analytics.counterfactual_outcomes FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: coin_market_contexts coin_market_contexts_immutable; Type: TRIGGER; Schema: dispatcher_v2; Owner: -
--

CREATE TRIGGER coin_market_contexts_immutable BEFORE DELETE OR UPDATE ON dispatcher_v2.coin_market_contexts FOR EACH ROW EXECUTE FUNCTION dispatcher_v2.reject_mutation();


--
-- Name: global_market_contexts global_market_contexts_immutable; Type: TRIGGER; Schema: dispatcher_v2; Owner: -
--

CREATE TRIGGER global_market_contexts_immutable BEFORE DELETE OR UPDATE ON dispatcher_v2.global_market_contexts FOR EACH ROW EXECUTE FUNCTION dispatcher_v2.reject_mutation();


--
-- Name: trading_capacity_snapshots trading_capacity_snapshots_immutable; Type: TRIGGER; Schema: dispatcher_v2; Owner: -
--

CREATE TRIGGER trading_capacity_snapshots_immutable BEFORE DELETE OR UPDATE ON dispatcher_v2.trading_capacity_snapshots FOR EACH ROW EXECUTE FUNCTION dispatcher_v2.reject_mutation();


--
-- Name: coin_market_contexts coin_market_contexts_immutable; Type: TRIGGER; Schema: mayak_v2; Owner: -
--

CREATE TRIGGER coin_market_contexts_immutable BEFORE DELETE OR UPDATE ON mayak_v2.coin_market_contexts FOR EACH ROW EXECUTE FUNCTION runtime.reject_immutable_change();


--
-- Name: market_observation_alerts market_observation_alerts_immutable; Type: TRIGGER; Schema: mayak_v2; Owner: -
--

CREATE TRIGGER market_observation_alerts_immutable BEFORE DELETE OR UPDATE ON mayak_v2.market_observation_alerts FOR EACH ROW EXECUTE FUNCTION runtime.reject_immutable_change();


--
-- Name: shared_market_contexts shared_market_contexts_immutable; Type: TRIGGER; Schema: mayak_v2; Owner: -
--

CREATE TRIGGER shared_market_contexts_immutable BEFORE DELETE OR UPDATE ON mayak_v2.shared_market_contexts FOR EACH ROW EXECUTE FUNCTION runtime.reject_immutable_change();


--
-- Name: entry_geometry_handoffs entry_geometry_handoffs_immutable; Type: TRIGGER; Schema: monitoring; Owner: -
--

CREATE TRIGGER entry_geometry_handoffs_immutable BEFORE DELETE OR UPDATE ON monitoring.entry_geometry_handoffs FOR EACH ROW EXECUTE FUNCTION runtime.reject_immutable_change();


--
-- Name: dispatcher_v2_event_links dispatcher_v2_event_links_immutable; Type: TRIGGER; Schema: research_context; Owner: -
--

CREATE TRIGGER dispatcher_v2_event_links_immutable BEFORE DELETE OR UPDATE ON research_context.dispatcher_v2_event_links FOR EACH ROW EXECUTE FUNCTION research_context.reject_dispatcher_v2_event_link_mutation();


--
-- Name: capital_reservations capital_reservations_guard_update; Type: TRIGGER; Schema: runtime; Owner: -
--

CREATE TRIGGER capital_reservations_guard_update BEFORE UPDATE ON runtime.capital_reservations FOR EACH ROW EXECUTE FUNCTION runtime.guard_capital_reservation_update();


--
-- Name: lifecycle_faults lifecycle_faults_guard_update; Type: TRIGGER; Schema: runtime; Owner: -
--

CREATE TRIGGER lifecycle_faults_guard_update BEFORE UPDATE ON runtime.lifecycle_faults FOR EACH ROW EXECUTE FUNCTION runtime.guard_lifecycle_fault_update();


--
-- Name: plan_consumptions plan_consumptions_guard_update; Type: TRIGGER; Schema: runtime; Owner: -
--

CREATE TRIGGER plan_consumptions_guard_update BEFORE UPDATE ON runtime.plan_consumptions FOR EACH ROW EXECUTE FUNCTION runtime.guard_plan_consumption_update();


--
-- Name: position_exit_claims position_exit_claims_guard_update; Type: TRIGGER; Schema: runtime; Owner: -
--

CREATE TRIGGER position_exit_claims_guard_update BEFORE UPDATE ON runtime.position_exit_claims FOR EACH ROW EXECUTE FUNCTION runtime.guard_position_exit_claim_update();


--
-- Name: trade_lifecycle_events trade_lifecycle_events_immutable; Type: TRIGGER; Schema: runtime; Owner: -
--

CREATE TRIGGER trade_lifecycle_events_immutable BEFORE DELETE OR UPDATE ON runtime.trade_lifecycle_events FOR EACH ROW EXECUTE FUNCTION runtime.reject_immutable_change();


--
-- Name: context_links context_links_immutable; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER context_links_immutable BEFORE DELETE OR UPDATE ON strategy_entry.context_links FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: entry_decisions entry_decisions_immutable; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER entry_decisions_immutable BEFORE DELETE OR UPDATE ON strategy_entry.entry_decisions FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: entry_plans entry_plans_immutable; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER entry_plans_immutable BEFORE DELETE OR UPDATE ON strategy_entry.entry_plans FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: execution_dispatches execution_dispatches_immutable; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER execution_dispatches_immutable BEFORE DELETE OR UPDATE ON strategy_entry.execution_dispatches FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: execution_permissions execution_permissions_guard_update; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER execution_permissions_guard_update BEFORE UPDATE ON strategy_entry.execution_permissions FOR EACH ROW EXECUTE FUNCTION strategy_entry.guard_execution_permission_update();


--
-- Name: execution_permissions execution_permissions_journal; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER execution_permissions_journal AFTER INSERT OR UPDATE ON strategy_entry.execution_permissions FOR EACH ROW EXECUTE FUNCTION strategy_entry.log_execution_permission_event();


--
-- Name: execution_requests execution_requests_immutable; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER execution_requests_immutable BEFORE DELETE OR UPDATE ON strategy_entry.execution_requests FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: exit_plans exit_plans_immutable; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER exit_plans_immutable BEFORE DELETE OR UPDATE ON strategy_entry.exit_plans FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: notifications notifications_immutable; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER notifications_immutable BEFORE DELETE OR UPDATE ON strategy_entry.notifications FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: sensor_links sensor_links_immutable; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER sensor_links_immutable BEFORE DELETE OR UPDATE ON strategy_entry.sensor_links FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: shadow_parity_events shadow_parity_events_immutable; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER shadow_parity_events_immutable BEFORE DELETE OR UPDATE ON strategy_entry.shadow_parity_events FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: shadow_parity_runs shadow_parity_runs_delete_immutable; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER shadow_parity_runs_delete_immutable BEFORE DELETE ON strategy_entry.shadow_parity_runs FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: shadow_parity_runs shadow_parity_runs_guard_update; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER shadow_parity_runs_guard_update BEFORE UPDATE ON strategy_entry.shadow_parity_runs FOR EACH ROW EXECUTE FUNCTION strategy_entry.guard_shadow_parity_run_update();


--
-- Name: strategy_activation_events strategy_activation_events_immutable; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER strategy_activation_events_immutable BEFORE DELETE OR UPDATE ON strategy_entry.strategy_activation_events FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: strategy_activations strategy_activations_guard_update; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER strategy_activations_guard_update BEFORE UPDATE ON strategy_entry.strategy_activations FOR EACH ROW EXECUTE FUNCTION strategy_entry.guard_activation_update();


--
-- Name: strategy_activations strategy_activations_journal; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER strategy_activations_journal AFTER INSERT OR UPDATE ON strategy_entry.strategy_activations FOR EACH ROW EXECUTE FUNCTION strategy_entry.log_activation_event();


--
-- Name: strategy_attempts strategy_attempts_immutable; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER strategy_attempts_immutable BEFORE DELETE OR UPDATE ON strategy_entry.strategy_attempts FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: strategy_cards strategy_cards_immutable; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER strategy_cards_immutable BEFORE DELETE OR UPDATE ON strategy_entry.strategy_cards FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: strategy_signals strategy_signals_immutable; Type: TRIGGER; Schema: strategy_entry; Owner: -
--

CREATE TRIGGER strategy_signals_immutable BEFORE DELETE OR UPDATE ON strategy_entry.strategy_signals FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: exit_decisions exit_decisions_immutable; Type: TRIGGER; Schema: strategy_exit; Owner: -
--

CREATE TRIGGER exit_decisions_immutable BEFORE DELETE OR UPDATE ON strategy_exit.exit_decisions FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: execution_dispatches exit_execution_dispatches_immutable; Type: TRIGGER; Schema: strategy_exit; Owner: -
--

CREATE TRIGGER exit_execution_dispatches_immutable BEFORE DELETE OR UPDATE ON strategy_exit.execution_dispatches FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: execution_materialization_blocks exit_execution_materialization_blocks_immutable; Type: TRIGGER; Schema: strategy_exit; Owner: -
--

CREATE TRIGGER exit_execution_materialization_blocks_immutable BEFORE DELETE OR UPDATE ON strategy_exit.execution_materialization_blocks FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: execution_requests exit_execution_requests_immutable; Type: TRIGGER; Schema: strategy_exit; Owner: -
--

CREATE TRIGGER exit_execution_requests_immutable BEFORE DELETE OR UPDATE ON strategy_exit.execution_requests FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: exit_observations exit_observations_immutable; Type: TRIGGER; Schema: strategy_exit; Owner: -
--

CREATE TRIGGER exit_observations_immutable BEFORE DELETE OR UPDATE ON strategy_exit.exit_observations FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: shadow_evaluations exit_shadow_evaluations_immutable; Type: TRIGGER; Schema: strategy_exit; Owner: -
--

CREATE TRIGGER exit_shadow_evaluations_immutable BEFORE DELETE OR UPDATE ON strategy_exit.shadow_evaluations FOR EACH ROW EXECUTE FUNCTION strategy_entry.reject_immutable_change();


--
-- Name: counterfactual_candidates counterfactual_candidates_entry_decision_id_strategy_attem_fkey; Type: FK CONSTRAINT; Schema: analytics; Owner: -
--

ALTER TABLE ONLY analytics.counterfactual_candidates
    ADD CONSTRAINT counterfactual_candidates_entry_decision_id_strategy_attem_fkey FOREIGN KEY (entry_decision_id, strategy_attempt_id, signal_id) REFERENCES strategy_entry.entry_decisions(entry_decision_id, strategy_attempt_id, signal_id);


--
-- Name: counterfactual_candidates counterfactual_candidates_entry_plan_fingerprint_strategy__fkey; Type: FK CONSTRAINT; Schema: analytics; Owner: -
--

ALTER TABLE ONLY analytics.counterfactual_candidates
    ADD CONSTRAINT counterfactual_candidates_entry_plan_fingerprint_strategy__fkey FOREIGN KEY (entry_plan_fingerprint, strategy_id, strategy_version, strategy_config_fingerprint) REFERENCES strategy_entry.entry_plans(entry_plan_fingerprint, strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: counterfactual_candidates counterfactual_candidates_exit_plan_fingerprint_strategy_i_fkey; Type: FK CONSTRAINT; Schema: analytics; Owner: -
--

ALTER TABLE ONLY analytics.counterfactual_candidates
    ADD CONSTRAINT counterfactual_candidates_exit_plan_fingerprint_strategy_i_fkey FOREIGN KEY (exit_plan_fingerprint, strategy_id, strategy_version, strategy_config_fingerprint) REFERENCES strategy_entry.exit_plans(exit_plan_fingerprint, strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: counterfactual_candidates counterfactual_candidates_strategy_activation_id_fkey; Type: FK CONSTRAINT; Schema: analytics; Owner: -
--

ALTER TABLE ONLY analytics.counterfactual_candidates
    ADD CONSTRAINT counterfactual_candidates_strategy_activation_id_fkey FOREIGN KEY (strategy_activation_id) REFERENCES strategy_entry.strategy_activations(activation_id);


--
-- Name: counterfactual_outcomes counterfactual_outcomes_counterfactual_id_fkey; Type: FK CONSTRAINT; Schema: analytics; Owner: -
--

ALTER TABLE ONLY analytics.counterfactual_outcomes
    ADD CONSTRAINT counterfactual_outcomes_counterfactual_id_fkey FOREIGN KEY (counterfactual_id) REFERENCES analytics.counterfactual_candidates(counterfactual_id);


--
-- Name: market_minutes market_minutes_instrument_id_venue_id_product_category_ext_fkey; Type: FK CONSTRAINT; Schema: analytics; Owner: -
--

ALTER TABLE ONLY analytics.market_minutes
    ADD CONSTRAINT market_minutes_instrument_id_venue_id_product_category_ext_fkey FOREIGN KEY (instrument_id, venue_id, product_category, external_symbol) REFERENCES identity.instruments(instrument_id, venue_id, product_category, external_symbol);


--
-- Name: market_minutes market_minutes_market_source_id_venue_id_fkey; Type: FK CONSTRAINT; Schema: analytics; Owner: -
--

ALTER TABLE ONLY analytics.market_minutes
    ADD CONSTRAINT market_minutes_market_source_id_venue_id_fkey FOREIGN KEY (market_source_id, venue_id) REFERENCES identity.market_sources(market_source_id, venue_id);


--
-- Name: command_timings command_timings_command_id_fkey; Type: FK CONSTRAINT; Schema: control; Owner: -
--

ALTER TABLE ONLY control.command_timings
    ADD CONSTRAINT command_timings_command_id_fkey FOREIGN KEY (command_id) REFERENCES control.command_requests(id);


--
-- Name: live_arm_retirement_events live_arm_retirement_events_live_arm_session_id_fkey; Type: FK CONSTRAINT; Schema: control; Owner: -
--

ALTER TABLE ONLY control.live_arm_retirement_events
    ADD CONSTRAINT live_arm_retirement_events_live_arm_session_id_fkey FOREIGN KEY (live_arm_session_id) REFERENCES control.live_arm_sessions(live_arm_session_id);


--
-- Name: coin_market_contexts coin_market_contexts_global_context_id_fkey; Type: FK CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.coin_market_contexts
    ADD CONSTRAINT coin_market_contexts_global_context_id_fkey FOREIGN KEY (global_context_id) REFERENCES dispatcher_v2.global_market_contexts(global_context_id);


--
-- Name: coin_market_contexts coin_market_contexts_source_coin_context_id_fkey; Type: FK CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.coin_market_contexts
    ADD CONSTRAINT coin_market_contexts_source_coin_context_id_fkey FOREIGN KEY (source_coin_context_id) REFERENCES mayak_v2.coin_market_contexts(coin_context_id);


--
-- Name: coin_market_contexts coin_market_contexts_source_mayak_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.coin_market_contexts
    ADD CONSTRAINT coin_market_contexts_source_mayak_snapshot_id_fkey FOREIGN KEY (source_mayak_snapshot_id) REFERENCES mayak_v2.snapshots(id);


--
-- Name: coin_market_contexts dispatcher_coin_market_contexts_linear_instrument_id_fkey; Type: FK CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.coin_market_contexts
    ADD CONSTRAINT dispatcher_coin_market_contexts_linear_instrument_id_fkey FOREIGN KEY (linear_instrument_id) REFERENCES identity.instruments(instrument_id) NOT VALID;


--
-- Name: coin_market_contexts dispatcher_coin_market_contexts_market_source_id_fkey; Type: FK CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.coin_market_contexts
    ADD CONSTRAINT dispatcher_coin_market_contexts_market_source_id_fkey FOREIGN KEY (market_source_id) REFERENCES identity.market_sources(market_source_id) NOT VALID;


--
-- Name: coin_market_contexts dispatcher_coin_market_contexts_spot_instrument_id_fkey; Type: FK CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.coin_market_contexts
    ADD CONSTRAINT dispatcher_coin_market_contexts_spot_instrument_id_fkey FOREIGN KEY (spot_instrument_id) REFERENCES identity.instruments(instrument_id) NOT VALID;


--
-- Name: global_market_contexts global_market_contexts_market_source_id_fkey; Type: FK CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.global_market_contexts
    ADD CONSTRAINT global_market_contexts_market_source_id_fkey FOREIGN KEY (market_source_id) REFERENCES identity.market_sources(market_source_id) NOT VALID;


--
-- Name: global_market_contexts global_market_contexts_source_market_context_id_fkey; Type: FK CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.global_market_contexts
    ADD CONSTRAINT global_market_contexts_source_market_context_id_fkey FOREIGN KEY (source_market_context_id) REFERENCES mayak_v2.shared_market_contexts(market_context_id);


--
-- Name: global_market_contexts global_market_contexts_source_mayak_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.global_market_contexts
    ADD CONSTRAINT global_market_contexts_source_mayak_snapshot_id_fkey FOREIGN KEY (source_mayak_snapshot_id) REFERENCES mayak_v2.snapshots(id);


--
-- Name: trading_capacity_snapshots trading_capacity_snapshots_account_connector_fkey; Type: FK CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.trading_capacity_snapshots
    ADD CONSTRAINT trading_capacity_snapshots_account_connector_fkey FOREIGN KEY (account_connector_id, trading_account_id) REFERENCES identity.account_connectors(account_connector_id, trading_account_id) NOT VALID;


--
-- Name: trading_capacity_snapshots trading_capacity_snapshots_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: dispatcher_v2; Owner: -
--

ALTER TABLE ONLY dispatcher_v2.trading_capacity_snapshots
    ADD CONSTRAINT trading_capacity_snapshots_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: account_connectors account_connectors_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.account_connectors
    ADD CONSTRAINT account_connectors_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id);


--
-- Name: instruments instruments_venue_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.instruments
    ADD CONSTRAINT instruments_venue_id_fkey FOREIGN KEY (venue_id) REFERENCES identity.venues(venue_id);


--
-- Name: market_sources market_sources_venue_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.market_sources
    ADD CONSTRAINT market_sources_venue_id_fkey FOREIGN KEY (venue_id) REFERENCES identity.venues(venue_id);


--
-- Name: trading_accounts trading_accounts_parent_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.trading_accounts
    ADD CONSTRAINT trading_accounts_parent_trading_account_id_fkey FOREIGN KEY (parent_trading_account_id) REFERENCES identity.trading_accounts(trading_account_id);


--
-- Name: trading_accounts trading_accounts_venue_id_fkey; Type: FK CONSTRAINT; Schema: identity; Owner: -
--

ALTER TABLE ONLY identity.trading_accounts
    ADD CONSTRAINT trading_accounts_venue_id_fkey FOREIGN KEY (venue_id) REFERENCES identity.venues(venue_id);


--
-- Name: coin_market_contexts coin_market_contexts_linear_instrument_id_fkey; Type: FK CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.coin_market_contexts
    ADD CONSTRAINT coin_market_contexts_linear_instrument_id_fkey FOREIGN KEY (linear_instrument_id) REFERENCES identity.instruments(instrument_id) NOT VALID;


--
-- Name: coin_market_contexts coin_market_contexts_market_source_id_fkey; Type: FK CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.coin_market_contexts
    ADD CONSTRAINT coin_market_contexts_market_source_id_fkey FOREIGN KEY (market_source_id) REFERENCES identity.market_sources(market_source_id) NOT VALID;


--
-- Name: coin_market_contexts coin_market_contexts_mayak_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.coin_market_contexts
    ADD CONSTRAINT coin_market_contexts_mayak_snapshot_id_fkey FOREIGN KEY (mayak_snapshot_id) REFERENCES mayak_v2.snapshots(id);


--
-- Name: coin_market_contexts coin_market_contexts_spot_instrument_id_fkey; Type: FK CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.coin_market_contexts
    ADD CONSTRAINT coin_market_contexts_spot_instrument_id_fkey FOREIGN KEY (spot_instrument_id) REFERENCES identity.instruments(instrument_id) NOT VALID;


--
-- Name: coin_minutes coin_minutes_linear_instrument_id_fkey; Type: FK CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.coin_minutes
    ADD CONSTRAINT coin_minutes_linear_instrument_id_fkey FOREIGN KEY (linear_instrument_id) REFERENCES identity.instruments(instrument_id) NOT VALID;


--
-- Name: coin_minutes coin_minutes_market_source_id_fkey; Type: FK CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.coin_minutes
    ADD CONSTRAINT coin_minutes_market_source_id_fkey FOREIGN KEY (market_source_id) REFERENCES identity.market_sources(market_source_id) NOT VALID;


--
-- Name: coin_minutes coin_minutes_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.coin_minutes
    ADD CONSTRAINT coin_minutes_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES mayak_v2.snapshots(id);


--
-- Name: coin_minutes coin_minutes_spot_instrument_id_fkey; Type: FK CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.coin_minutes
    ADD CONSTRAINT coin_minutes_spot_instrument_id_fkey FOREIGN KEY (spot_instrument_id) REFERENCES identity.instruments(instrument_id) NOT VALID;


--
-- Name: events events_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.events
    ADD CONSTRAINT events_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES mayak_v2.snapshots(id);


--
-- Name: liquidations liquidations_instrument_id_fkey; Type: FK CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.liquidations
    ADD CONSTRAINT liquidations_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES identity.instruments(instrument_id) NOT VALID;


--
-- Name: liquidations liquidations_market_source_id_fkey; Type: FK CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.liquidations
    ADD CONSTRAINT liquidations_market_source_id_fkey FOREIGN KEY (market_source_id) REFERENCES identity.market_sources(market_source_id) NOT VALID;


--
-- Name: market_observation_alert_deliveries market_observation_alert_deliveries_alert_id_fkey; Type: FK CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.market_observation_alert_deliveries
    ADD CONSTRAINT market_observation_alert_deliveries_alert_id_fkey FOREIGN KEY (alert_id) REFERENCES mayak_v2.market_observation_alerts(alert_id);


--
-- Name: observation_journal observation_journal_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.observation_journal
    ADD CONSTRAINT observation_journal_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES mayak_v2.snapshots(id);


--
-- Name: shared_market_contexts shared_market_contexts_market_source_id_fkey; Type: FK CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.shared_market_contexts
    ADD CONSTRAINT shared_market_contexts_market_source_id_fkey FOREIGN KEY (market_source_id) REFERENCES identity.market_sources(market_source_id) NOT VALID;


--
-- Name: shared_market_contexts shared_market_contexts_mayak_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.shared_market_contexts
    ADD CONSTRAINT shared_market_contexts_mayak_snapshot_id_fkey FOREIGN KEY (mayak_snapshot_id) REFERENCES mayak_v2.snapshots(id);


--
-- Name: state_events state_events_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: mayak_v2; Owner: -
--

ALTER TABLE ONLY mayak_v2.state_events
    ADD CONSTRAINT state_events_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES mayak_v2.snapshots(id);


--
-- Name: opportunity_events opportunity_events_signal_id_fkey; Type: FK CONSTRAINT; Schema: monitoring; Owner: -
--

ALTER TABLE ONLY monitoring.opportunity_events
    ADD CONSTRAINT opportunity_events_signal_id_fkey FOREIGN KEY (signal_id) REFERENCES monitoring.opportunities(signal_id);


--
-- Name: dispatcher_v2_event_links dispatcher_v2_event_links_capacity_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: research_context; Owner: -
--

ALTER TABLE ONLY research_context.dispatcher_v2_event_links
    ADD CONSTRAINT dispatcher_v2_event_links_capacity_snapshot_id_fkey FOREIGN KEY (capacity_snapshot_id) REFERENCES dispatcher_v2.trading_capacity_snapshots(capacity_snapshot_id);


--
-- Name: dispatcher_v2_event_links dispatcher_v2_event_links_coin_context_id_fkey; Type: FK CONSTRAINT; Schema: research_context; Owner: -
--

ALTER TABLE ONLY research_context.dispatcher_v2_event_links
    ADD CONSTRAINT dispatcher_v2_event_links_coin_context_id_fkey FOREIGN KEY (coin_context_id) REFERENCES dispatcher_v2.coin_market_contexts(coin_context_id);


--
-- Name: dispatcher_v2_event_links dispatcher_v2_event_links_global_context_id_fkey; Type: FK CONSTRAINT; Schema: research_context; Owner: -
--

ALTER TABLE ONLY research_context.dispatcher_v2_event_links
    ADD CONSTRAINT dispatcher_v2_event_links_global_context_id_fkey FOREIGN KEY (global_context_id) REFERENCES dispatcher_v2.global_market_contexts(global_context_id);


--
-- Name: dispatcher_v2_event_links dispatcher_v2_event_links_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: research_context; Owner: -
--

ALTER TABLE ONLY research_context.dispatcher_v2_event_links
    ADD CONSTRAINT dispatcher_v2_event_links_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: event_links event_links_observed_dispatcher_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: research_context; Owner: -
--

ALTER TABLE ONLY research_context.event_links
    ADD CONSTRAINT event_links_observed_dispatcher_snapshot_id_fkey FOREIGN KEY (observed_dispatcher_snapshot_id) REFERENCES strategy_dispatcher.runs(snapshot_id);


--
-- Name: event_links event_links_observed_mayak_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: research_context; Owner: -
--

ALTER TABLE ONLY research_context.event_links
    ADD CONSTRAINT event_links_observed_mayak_snapshot_id_fkey FOREIGN KEY (observed_mayak_snapshot_id) REFERENCES mayak_v2.snapshots(id);


--
-- Name: account_state_generations account_state_generations_account_connector_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.account_state_generations
    ADD CONSTRAINT account_state_generations_account_connector_fkey FOREIGN KEY (account_connector_id, trading_account_id) REFERENCES identity.account_connectors(account_connector_id, trading_account_id) NOT VALID;


--
-- Name: account_state_generations account_state_generations_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.account_state_generations
    ADD CONSTRAINT account_state_generations_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: capital_reservations capital_reservations_strategy_attempt_id_signal_id_strateg_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.capital_reservations
    ADD CONSTRAINT capital_reservations_strategy_attempt_id_signal_id_strateg_fkey FOREIGN KEY (strategy_attempt_id, signal_id, strategy_id, strategy_version, strategy_config_fingerprint, entry_plan_fingerprint) REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id, signal_id, strategy_id, strategy_version, strategy_config_fingerprint, entry_plan_fingerprint) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: capital_reservations capital_reservations_strategy_execution_binding_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.capital_reservations
    ADD CONSTRAINT capital_reservations_strategy_execution_binding_fkey FOREIGN KEY (strategy_execution_binding_id) REFERENCES strategy_entry.strategy_execution_bindings(strategy_execution_binding_id) NOT VALID;


--
-- Name: capital_reservations capital_reservations_strategy_position_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.capital_reservations
    ADD CONSTRAINT capital_reservations_strategy_position_fkey FOREIGN KEY (strategy_position_id) REFERENCES runtime.position_ownership(position_id);


--
-- Name: capital_reservations capital_reservations_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.capital_reservations
    ADD CONSTRAINT capital_reservations_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: connection_events connection_events_account_connector_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.connection_events
    ADD CONSTRAINT connection_events_account_connector_fkey FOREIGN KEY (account_connector_id, trading_account_id) REFERENCES identity.account_connectors(account_connector_id, trading_account_id) NOT VALID;


--
-- Name: connection_events connection_events_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.connection_events
    ADD CONSTRAINT connection_events_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: entry_geometry_bindings entry_geometry_bindings_geometry_handoff_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.entry_geometry_bindings
    ADD CONSTRAINT entry_geometry_bindings_geometry_handoff_id_fkey FOREIGN KEY (geometry_handoff_id) REFERENCES monitoring.entry_geometry_handoffs(geometry_handoff_id);


--
-- Name: exchange_order_history exchange_order_history_account_connector_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.exchange_order_history
    ADD CONSTRAINT exchange_order_history_account_connector_fkey FOREIGN KEY (account_connector_id, trading_account_id) REFERENCES identity.account_connectors(account_connector_id, trading_account_id) NOT VALID;


--
-- Name: exchange_order_history exchange_order_history_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.exchange_order_history
    ADD CONSTRAINT exchange_order_history_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: exchange_position_slot_claims exchange_position_slot_claims_capital_reservation_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.exchange_position_slot_claims
    ADD CONSTRAINT exchange_position_slot_claims_capital_reservation_id_fkey FOREIGN KEY (capital_reservation_id) REFERENCES runtime.capital_reservations(reservation_id);


--
-- Name: exchange_position_slot_claims exchange_position_slot_claims_position_mode_state_ref_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.exchange_position_slot_claims
    ADD CONSTRAINT exchange_position_slot_claims_position_mode_state_ref_fkey FOREIGN KEY (position_mode_state_ref) REFERENCES runtime.position_mode_states(position_mode_state_ref);


--
-- Name: exchange_position_slot_claims exchange_position_slot_claims_strategy_attempt_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.exchange_position_slot_claims
    ADD CONSTRAINT exchange_position_slot_claims_strategy_attempt_id_fkey FOREIGN KEY (strategy_attempt_id) REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: exchange_position_slot_claims exchange_position_slot_claims_strategy_execution_binding_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.exchange_position_slot_claims
    ADD CONSTRAINT exchange_position_slot_claims_strategy_execution_binding_fkey FOREIGN KEY (strategy_execution_binding_id) REFERENCES strategy_entry.strategy_execution_bindings(strategy_execution_binding_id) NOT VALID;


--
-- Name: exchange_position_slot_claims exchange_position_slot_claims_strategy_position_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.exchange_position_slot_claims
    ADD CONSTRAINT exchange_position_slot_claims_strategy_position_id_fkey FOREIGN KEY (strategy_position_id) REFERENCES runtime.position_ownership(position_id);


--
-- Name: exchange_position_slot_claims exchange_position_slot_claims_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.exchange_position_slot_claims
    ADD CONSTRAINT exchange_position_slot_claims_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: executions executions_account_connector_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.executions
    ADD CONSTRAINT executions_account_connector_fkey FOREIGN KEY (account_connector_id, trading_account_id) REFERENCES identity.account_connectors(account_connector_id, trading_account_id) NOT VALID;


--
-- Name: executions executions_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.executions
    ADD CONSTRAINT executions_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: hot_orders hot_orders_account_connector_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.hot_orders
    ADD CONSTRAINT hot_orders_account_connector_fkey FOREIGN KEY (account_connector_id, trading_account_id) REFERENCES identity.account_connectors(account_connector_id, trading_account_id) NOT VALID;


--
-- Name: hot_orders hot_orders_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.hot_orders
    ADD CONSTRAINT hot_orders_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: hot_positions hot_positions_account_connector_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.hot_positions
    ADD CONSTRAINT hot_positions_account_connector_fkey FOREIGN KEY (account_connector_id, trading_account_id) REFERENCES identity.account_connectors(account_connector_id, trading_account_id) NOT VALID;


--
-- Name: hot_positions hot_positions_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.hot_positions
    ADD CONSTRAINT hot_positions_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: lifecycle_fault_deliveries lifecycle_fault_deliveries_fault_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.lifecycle_fault_deliveries
    ADD CONSTRAINT lifecycle_fault_deliveries_fault_id_fkey FOREIGN KEY (fault_id) REFERENCES runtime.lifecycle_faults(fault_id);

--
-- Name: lifecycle_faults lifecycle_faults_strategy_position_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.lifecycle_faults
    ADD CONSTRAINT lifecycle_faults_strategy_position_id_fkey FOREIGN KEY (strategy_position_id) REFERENCES runtime.position_ownership(position_id);


--
-- Name: owner_manual_interventions owner_manual_interventions_position_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.owner_manual_interventions
    ADD CONSTRAINT owner_manual_interventions_position_id_fkey FOREIGN KEY (position_id) REFERENCES runtime.position_ownership(position_id);


--
-- Name: plan_consumptions plan_consumptions_entry_plan_fingerprint_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.plan_consumptions
    ADD CONSTRAINT plan_consumptions_entry_plan_fingerprint_fkey FOREIGN KEY (entry_plan_fingerprint) REFERENCES strategy_entry.entry_plans(entry_plan_fingerprint);


--
-- Name: plan_consumptions plan_consumptions_exit_plan_fingerprint_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.plan_consumptions
    ADD CONSTRAINT plan_consumptions_exit_plan_fingerprint_fkey FOREIGN KEY (exit_plan_fingerprint) REFERENCES strategy_entry.exit_plans(exit_plan_fingerprint);


--
-- Name: plan_consumptions plan_consumptions_strategy_activation_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.plan_consumptions
    ADD CONSTRAINT plan_consumptions_strategy_activation_id_fkey FOREIGN KEY (strategy_activation_id) REFERENCES strategy_entry.strategy_activations(activation_id);


--
-- Name: position_exit_attribution position_exit_attribution_position_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_exit_attribution
    ADD CONSTRAINT position_exit_attribution_position_id_fkey FOREIGN KEY (position_id) REFERENCES runtime.position_ownership(position_id);


--
-- Name: position_exit_claims position_exit_claims_exit_plan_fingerprint_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_exit_claims
    ADD CONSTRAINT position_exit_claims_exit_plan_fingerprint_fkey FOREIGN KEY (exit_plan_fingerprint) REFERENCES strategy_entry.exit_plans(exit_plan_fingerprint);


--
-- Name: position_exit_claims position_exit_claims_strategy_activation_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_exit_claims
    ADD CONSTRAINT position_exit_claims_strategy_activation_id_fkey FOREIGN KEY (strategy_activation_id) REFERENCES strategy_entry.strategy_activations(activation_id);


--
-- Name: position_exit_claims position_exit_claims_strategy_position_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_exit_claims
    ADD CONSTRAINT position_exit_claims_strategy_position_id_fkey FOREIGN KEY (strategy_position_id) REFERENCES runtime.position_ownership(position_id);


--
-- Name: position_lifecycle_events position_lifecycle_events_position_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_lifecycle_events
    ADD CONSTRAINT position_lifecycle_events_position_id_fkey FOREIGN KEY (position_id) REFERENCES runtime.position_ownership(position_id);


--
-- Name: position_mode_states position_mode_states_account_connector_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_mode_states
    ADD CONSTRAINT position_mode_states_account_connector_fkey FOREIGN KEY (account_connector_id, trading_account_id) REFERENCES identity.account_connectors(account_connector_id, trading_account_id) NOT VALID;


--
-- Name: position_mode_states position_mode_states_instrument_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_mode_states
    ADD CONSTRAINT position_mode_states_instrument_id_fkey FOREIGN KEY (instrument_id) REFERENCES identity.instruments(instrument_id) NOT VALID;


--
-- Name: position_mode_states position_mode_states_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_mode_states
    ADD CONSTRAINT position_mode_states_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: position_ownership position_ownership_entry_decision_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_ownership
    ADD CONSTRAINT position_ownership_entry_decision_fkey FOREIGN KEY (entry_decision_id) REFERENCES strategy_entry.entry_decisions(entry_decision_id);


--
-- Name: position_ownership position_ownership_entry_plan_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_ownership
    ADD CONSTRAINT position_ownership_entry_plan_fkey FOREIGN KEY (entry_plan_fingerprint) REFERENCES strategy_entry.entry_plans(entry_plan_fingerprint);


--
-- Name: position_ownership position_ownership_entry_request_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_ownership
    ADD CONSTRAINT position_ownership_entry_request_fkey FOREIGN KEY (entry_execution_request_id) REFERENCES strategy_entry.execution_requests(execution_request_id);


--
-- Name: position_ownership position_ownership_exit_plan_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_ownership
    ADD CONSTRAINT position_ownership_exit_plan_fkey FOREIGN KEY (exit_plan_fingerprint) REFERENCES strategy_entry.exit_plans(exit_plan_fingerprint);


--
-- Name: position_ownership position_ownership_geometry_handoff_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_ownership
    ADD CONSTRAINT position_ownership_geometry_handoff_id_fkey FOREIGN KEY (geometry_handoff_id) REFERENCES monitoring.entry_geometry_handoffs(geometry_handoff_id);


--
-- Name: position_ownership position_ownership_position_mode_state_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_ownership
    ADD CONSTRAINT position_ownership_position_mode_state_fkey FOREIGN KEY (position_mode_state_ref) REFERENCES runtime.position_mode_states(position_mode_state_ref) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: position_ownership position_ownership_slot_claim_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_ownership
    ADD CONSTRAINT position_ownership_slot_claim_fkey FOREIGN KEY (exchange_position_slot_claim_id) REFERENCES runtime.exchange_position_slot_claims(exchange_position_slot_claim_id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: position_ownership position_ownership_strategy_activation_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_ownership
    ADD CONSTRAINT position_ownership_strategy_activation_fkey FOREIGN KEY (strategy_activation_id) REFERENCES strategy_entry.strategy_activations(activation_id);


--
-- Name: position_ownership position_ownership_strategy_attempt_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_ownership
    ADD CONSTRAINT position_ownership_strategy_attempt_fkey FOREIGN KEY (strategy_attempt_id) REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id);


--
-- Name: position_ownership position_ownership_strategy_execution_binding_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_ownership
    ADD CONSTRAINT position_ownership_strategy_execution_binding_fkey FOREIGN KEY (strategy_execution_binding_id) REFERENCES strategy_entry.strategy_execution_bindings(strategy_execution_binding_id) NOT VALID;


--
-- Name: position_ownership position_ownership_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.position_ownership
    ADD CONSTRAINT position_ownership_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: private_events private_events_account_connector_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.private_events
    ADD CONSTRAINT private_events_account_connector_fkey FOREIGN KEY (account_connector_id, trading_account_id) REFERENCES identity.account_connectors(account_connector_id, trading_account_id) NOT VALID;


--
-- Name: private_events private_events_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.private_events
    ADD CONSTRAINT private_events_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: protection_events protection_events_position_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.protection_events
    ADD CONSTRAINT protection_events_position_id_fkey FOREIGN KEY (position_id) REFERENCES runtime.position_ownership(position_id);


--
-- Name: reconciliation_runs reconciliation_runs_account_connector_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.reconciliation_runs
    ADD CONSTRAINT reconciliation_runs_account_connector_fkey FOREIGN KEY (account_connector_id, trading_account_id) REFERENCES identity.account_connectors(account_connector_id, trading_account_id) NOT VALID;


--
-- Name: reconciliation_runs reconciliation_runs_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.reconciliation_runs
    ADD CONSTRAINT reconciliation_runs_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: recovered_closed_entry_cycles recovered_closed_entry_cycles_reservation_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.recovered_closed_entry_cycles
    ADD CONSTRAINT recovered_closed_entry_cycles_reservation_id_fkey FOREIGN KEY (reservation_id) REFERENCES runtime.capital_reservations(reservation_id);


--
-- Name: trade_commands trade_commands_strategy_execution_binding_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.trade_commands
    ADD CONSTRAINT trade_commands_strategy_execution_binding_fkey FOREIGN KEY (strategy_execution_binding_id) REFERENCES strategy_entry.strategy_execution_bindings(strategy_execution_binding_id) NOT VALID;


--
-- Name: trade_commands trade_commands_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.trade_commands
    ADD CONSTRAINT trade_commands_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: trade_lifecycle_events trade_lifecycle_events_strategy_position_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.trade_lifecycle_events
    ADD CONSTRAINT trade_lifecycle_events_strategy_position_id_fkey FOREIGN KEY (strategy_position_id) REFERENCES runtime.position_ownership(position_id);


--
-- Name: wallet_latest wallet_latest_account_connector_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.wallet_latest
    ADD CONSTRAINT wallet_latest_account_connector_fkey FOREIGN KEY (account_connector_id, trading_account_id) REFERENCES identity.account_connectors(account_connector_id, trading_account_id) NOT VALID;


--
-- Name: wallet_latest wallet_latest_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: runtime; Owner: -
--

ALTER TABLE ONLY runtime.wallet_latest
    ADD CONSTRAINT wallet_latest_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: assessments assessments_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: strategy_dispatcher; Owner: -
--

ALTER TABLE ONLY strategy_dispatcher.assessments
    ADD CONSTRAINT assessments_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES strategy_dispatcher.runs(snapshot_id);


--
-- Name: context_links context_links_signal_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.context_links
    ADD CONSTRAINT context_links_signal_id_fkey FOREIGN KEY (signal_id) REFERENCES strategy_entry.strategy_signals(signal_id);


--
-- Name: context_links context_links_strategy_attempt_id_signal_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.context_links
    ADD CONSTRAINT context_links_strategy_attempt_id_signal_id_fkey FOREIGN KEY (strategy_attempt_id, signal_id) REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id, signal_id);


--
-- Name: entry_decisions entry_decisions_capital_reservation_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.entry_decisions
    ADD CONSTRAINT entry_decisions_capital_reservation_fkey FOREIGN KEY (capital_reservation_id) REFERENCES runtime.capital_reservations(reservation_id);


--
-- Name: entry_decisions entry_decisions_position_mode_state_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.entry_decisions
    ADD CONSTRAINT entry_decisions_position_mode_state_fkey FOREIGN KEY (position_mode_state_ref) REFERENCES runtime.position_mode_states(position_mode_state_ref) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: entry_decisions entry_decisions_slot_claim_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.entry_decisions
    ADD CONSTRAINT entry_decisions_slot_claim_fkey FOREIGN KEY (exchange_position_slot_claim_id) REFERENCES runtime.exchange_position_slot_claims(exchange_position_slot_claim_id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: entry_decisions entry_decisions_strategy_attempt_id_signal_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.entry_decisions
    ADD CONSTRAINT entry_decisions_strategy_attempt_id_signal_id_fkey FOREIGN KEY (strategy_attempt_id, signal_id) REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id, signal_id);


--
-- Name: entry_decisions entry_decisions_strategy_execution_binding_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.entry_decisions
    ADD CONSTRAINT entry_decisions_strategy_execution_binding_fkey FOREIGN KEY (strategy_execution_binding_id) REFERENCES strategy_entry.strategy_execution_bindings(strategy_execution_binding_id) NOT VALID;


--
-- Name: entry_decisions entry_decisions_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.entry_decisions
    ADD CONSTRAINT entry_decisions_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: entry_plans entry_plans_strategy_id_strategy_version_strategy_config_f_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.entry_plans
    ADD CONSTRAINT entry_plans_strategy_id_strategy_version_strategy_config_f_fkey FOREIGN KEY (strategy_id, strategy_version, strategy_config_fingerprint) REFERENCES strategy_entry.strategy_cards(strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: execution_dispatches execution_dispatches_capital_reservation_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_dispatches
    ADD CONSTRAINT execution_dispatches_capital_reservation_fkey FOREIGN KEY (capital_reservation_id) REFERENCES runtime.capital_reservations(reservation_id);


--
-- Name: execution_dispatches execution_dispatches_entry_decision_id_strategy_attempt_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_dispatches
    ADD CONSTRAINT execution_dispatches_entry_decision_id_strategy_attempt_id_fkey FOREIGN KEY (entry_decision_id, strategy_attempt_id, signal_id) REFERENCES strategy_entry.entry_decisions(entry_decision_id, strategy_attempt_id, signal_id);


--
-- Name: execution_dispatches execution_dispatches_execution_request_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_dispatches
    ADD CONSTRAINT execution_dispatches_execution_request_id_fkey FOREIGN KEY (execution_request_id) REFERENCES strategy_entry.execution_requests(execution_request_id);


--
-- Name: execution_dispatches execution_dispatches_exit_plan_fingerprint_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_dispatches
    ADD CONSTRAINT execution_dispatches_exit_plan_fingerprint_fkey FOREIGN KEY (exit_plan_fingerprint) REFERENCES strategy_entry.exit_plans(exit_plan_fingerprint);


--
-- Name: execution_dispatches execution_dispatches_strategy_attempt_id_signal_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_dispatches
    ADD CONSTRAINT execution_dispatches_strategy_attempt_id_signal_id_fkey FOREIGN KEY (strategy_attempt_id, signal_id) REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id, signal_id);


--
-- Name: execution_permission_events execution_permission_events_execution_permission_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_permission_events
    ADD CONSTRAINT execution_permission_events_execution_permission_id_fkey FOREIGN KEY (execution_permission_id) REFERENCES strategy_entry.execution_permissions(execution_permission_id);


--
-- Name: execution_permissions execution_permissions_strategy_id_strategy_version_strateg_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_permissions
    ADD CONSTRAINT execution_permissions_strategy_id_strategy_version_strateg_fkey FOREIGN KEY (strategy_id, strategy_version, strategy_config_fingerprint) REFERENCES strategy_entry.strategy_cards(strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: execution_request_state_events execution_request_state_events_execution_request_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_request_state_events
    ADD CONSTRAINT execution_request_state_events_execution_request_id_fkey FOREIGN KEY (execution_request_id) REFERENCES strategy_entry.execution_requests(execution_request_id);


--
-- Name: execution_requests execution_requests_capital_reservation_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_requests
    ADD CONSTRAINT execution_requests_capital_reservation_fkey FOREIGN KEY (capital_reservation_id) REFERENCES runtime.capital_reservations(reservation_id);


--
-- Name: execution_requests execution_requests_entry_decision_id_strategy_attempt_id_s_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_requests
    ADD CONSTRAINT execution_requests_entry_decision_id_strategy_attempt_id_s_fkey FOREIGN KEY (entry_decision_id, strategy_attempt_id, signal_id) REFERENCES strategy_entry.entry_decisions(entry_decision_id, strategy_attempt_id, signal_id);


--
-- Name: execution_requests execution_requests_exit_plan_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_requests
    ADD CONSTRAINT execution_requests_exit_plan_fkey FOREIGN KEY (exit_plan_fingerprint) REFERENCES strategy_entry.exit_plans(exit_plan_fingerprint);


--
-- Name: execution_requests execution_requests_position_mode_state_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_requests
    ADD CONSTRAINT execution_requests_position_mode_state_fkey FOREIGN KEY (position_mode_state_ref) REFERENCES runtime.position_mode_states(position_mode_state_ref) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: execution_requests execution_requests_slot_claim_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_requests
    ADD CONSTRAINT execution_requests_slot_claim_fkey FOREIGN KEY (exchange_position_slot_claim_id) REFERENCES runtime.exchange_position_slot_claims(exchange_position_slot_claim_id) DEFERRABLE INITIALLY DEFERRED;


--
-- Name: execution_requests execution_requests_strategy_attempt_id_signal_id_strategy__fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_requests
    ADD CONSTRAINT execution_requests_strategy_attempt_id_signal_id_strategy__fkey FOREIGN KEY (strategy_attempt_id, signal_id, strategy_id, strategy_version, strategy_config_fingerprint, entry_plan_fingerprint) REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id, signal_id, strategy_id, strategy_version, strategy_config_fingerprint, entry_plan_fingerprint);


--
-- Name: execution_requests execution_requests_strategy_execution_binding_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_requests
    ADD CONSTRAINT execution_requests_strategy_execution_binding_fkey FOREIGN KEY (strategy_execution_binding_id) REFERENCES strategy_entry.strategy_execution_bindings(strategy_execution_binding_id) NOT VALID;


--
-- Name: execution_requests execution_requests_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.execution_requests
    ADD CONSTRAINT execution_requests_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: exit_plans exit_plans_strategy_id_strategy_version_strategy_config_fi_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.exit_plans
    ADD CONSTRAINT exit_plans_strategy_id_strategy_version_strategy_config_fi_fkey FOREIGN KEY (strategy_id, strategy_version, strategy_config_fingerprint) REFERENCES strategy_entry.strategy_cards(strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: notifications notifications_strategy_attempt_id_signal_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.notifications
    ADD CONSTRAINT notifications_strategy_attempt_id_signal_id_fkey FOREIGN KEY (strategy_attempt_id, signal_id) REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id, signal_id);


--
-- Name: notifications notifications_strategy_attempt_id_signal_id_strategy_id_st_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.notifications
    ADD CONSTRAINT notifications_strategy_attempt_id_signal_id_strategy_id_st_fkey FOREIGN KEY (strategy_attempt_id, signal_id, strategy_id, strategy_version) REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id, signal_id, strategy_id, strategy_version);


--
-- Name: paper_orders paper_orders_execution_request_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.paper_orders
    ADD CONSTRAINT paper_orders_execution_request_id_fkey FOREIGN KEY (execution_request_id) REFERENCES strategy_entry.execution_requests(execution_request_id);


--
-- Name: paper_orders paper_orders_strategy_activation_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.paper_orders
    ADD CONSTRAINT paper_orders_strategy_activation_id_fkey FOREIGN KEY (strategy_activation_id) REFERENCES strategy_entry.strategy_activations(activation_id);


--
-- Name: paper_position_events paper_position_events_paper_position_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.paper_position_events
    ADD CONSTRAINT paper_position_events_paper_position_id_fkey FOREIGN KEY (paper_position_id) REFERENCES strategy_entry.paper_positions(paper_position_id);


--
-- Name: paper_positions paper_positions_paper_order_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.paper_positions
    ADD CONSTRAINT paper_positions_paper_order_id_fkey FOREIGN KEY (paper_order_id) REFERENCES strategy_entry.paper_orders(paper_order_id);


--
-- Name: paper_positions paper_positions_parent_position_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.paper_positions

ADD CONSTRAINT paper_positions_parent_position_id_fkey FOREIGN KEY (parent_position_id) REFERENCES strategy_entry.paper_positions(paper_position_id);


--
-- Name: reverse_transitions reverse_transitions_signal_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.reverse_transitions
    ADD CONSTRAINT reverse_transitions_signal_id_fkey FOREIGN KEY (signal_id) REFERENCES strategy_entry.strategy_signals(signal_id);


--
-- Name: reverse_transitions reverse_transitions_source_strategy_attempt_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.reverse_transitions
    ADD CONSTRAINT reverse_transitions_source_strategy_attempt_id_fkey FOREIGN KEY (source_strategy_attempt_id) REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id);


--
-- Name: reverse_transitions reverse_transitions_strategy_position_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.reverse_transitions
    ADD CONSTRAINT reverse_transitions_strategy_position_id_fkey FOREIGN KEY (strategy_position_id) REFERENCES runtime.position_ownership(position_id);


--
-- Name: sensor_links sensor_links_signal_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.sensor_links
    ADD CONSTRAINT sensor_links_signal_id_fkey FOREIGN KEY (signal_id) REFERENCES strategy_entry.strategy_signals(signal_id);


--
-- Name: sensor_links sensor_links_strategy_attempt_id_signal_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.sensor_links
    ADD CONSTRAINT sensor_links_strategy_attempt_id_signal_id_fkey FOREIGN KEY (strategy_attempt_id, signal_id) REFERENCES strategy_entry.strategy_attempts(strategy_attempt_id, signal_id);


--
-- Name: shadow_parity_events shadow_parity_events_parity_run_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.shadow_parity_events
    ADD CONSTRAINT shadow_parity_events_parity_run_id_fkey FOREIGN KEY (parity_run_id) REFERENCES strategy_entry.shadow_parity_runs(parity_run_id);


--
-- Name: shadow_parity_runs shadow_parity_runs_entry_plan_identity_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.shadow_parity_runs
    ADD CONSTRAINT shadow_parity_runs_entry_plan_identity_fkey FOREIGN KEY (entry_plan_fingerprint, strategy_id, strategy_version, strategy_config_fingerprint) REFERENCES strategy_entry.entry_plans(entry_plan_fingerprint, strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: shadow_parity_runs shadow_parity_runs_strategy_id_strategy_version_strategy_c_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.shadow_parity_runs
    ADD CONSTRAINT shadow_parity_runs_strategy_id_strategy_version_strategy_c_fkey FOREIGN KEY (strategy_id, strategy_version, strategy_config_fingerprint) REFERENCES strategy_entry.strategy_cards(strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: strategy_activation_events strategy_activation_events_activation_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_activation_events
    ADD CONSTRAINT strategy_activation_events_activation_id_fkey FOREIGN KEY (activation_id) REFERENCES strategy_entry.strategy_activations(activation_id);


--
-- Name: strategy_activations strategy_activations_strategy_id_strategy_version_strategy_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_activations
    ADD CONSTRAINT strategy_activations_strategy_id_strategy_version_strategy_fkey FOREIGN KEY (strategy_id, strategy_version, strategy_config_fingerprint) REFERENCES strategy_entry.strategy_cards(strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: strategy_attempts strategy_attempts_signal_id_strategy_id_strategy_version_s_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_attempts
    ADD CONSTRAINT strategy_attempts_signal_id_strategy_id_strategy_version_s_fkey FOREIGN KEY (signal_id, strategy_id, strategy_version, strategy_config_fingerprint, entry_plan_fingerprint, strategy_activation_id) REFERENCES strategy_entry.strategy_signals(signal_id, strategy_id, strategy_version, strategy_config_fingerprint, entry_plan_fingerprint, strategy_activation_id);


--
-- Name: strategy_execution_bindings strategy_execution_bindings_strategy_activation_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_execution_bindings
    ADD CONSTRAINT strategy_execution_bindings_strategy_activation_id_fkey FOREIGN KEY (strategy_activation_id) REFERENCES strategy_entry.strategy_activations(activation_id);


--
-- Name: strategy_execution_bindings strategy_execution_bindings_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_execution_bindings
    ADD CONSTRAINT strategy_execution_bindings_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id);


--
-- Name: strategy_signals strategy_signals_entry_plan_fingerprint_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_signals
    ADD CONSTRAINT strategy_signals_entry_plan_fingerprint_fkey FOREIGN KEY (entry_plan_fingerprint) REFERENCES strategy_entry.entry_plans(entry_plan_fingerprint);


--
-- Name: strategy_signals strategy_signals_entry_plan_fingerprint_strategy_id_strate_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_signals
    ADD CONSTRAINT strategy_signals_entry_plan_fingerprint_strategy_id_strate_fkey FOREIGN KEY (entry_plan_fingerprint, strategy_id, strategy_version, strategy_config_fingerprint) REFERENCES strategy_entry.entry_plans(entry_plan_fingerprint, strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: strategy_signals strategy_signals_strategy_activation_id_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_signals
    ADD CONSTRAINT strategy_signals_strategy_activation_id_fkey FOREIGN KEY (strategy_activation_id) REFERENCES strategy_entry.strategy_activations(activation_id);


--
-- Name: strategy_signals strategy_signals_strategy_activation_id_strategy_id_strate_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_signals
    ADD CONSTRAINT strategy_signals_strategy_activation_id_strategy_id_strate_fkey FOREIGN KEY (strategy_activation_id, strategy_id, strategy_version, strategy_config_fingerprint) REFERENCES strategy_entry.strategy_activations(activation_id, strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: strategy_signals strategy_signals_strategy_id_strategy_version_strategy_con_fkey; Type: FK CONSTRAINT; Schema: strategy_entry; Owner: -
--

ALTER TABLE ONLY strategy_entry.strategy_signals
    ADD CONSTRAINT strategy_signals_strategy_id_strategy_version_strategy_con_fkey FOREIGN KEY (strategy_id, strategy_version, strategy_config_fingerprint) REFERENCES strategy_entry.strategy_cards(strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: execution_dispatches execution_dispatches_execution_request_id_fkey; Type: FK CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.execution_dispatches
    ADD CONSTRAINT execution_dispatches_execution_request_id_fkey FOREIGN KEY (execution_request_id) REFERENCES strategy_exit.execution_requests(execution_request_id);


--
-- Name: execution_materialization_blocks execution_materialization_blocks_exit_decision_id_fkey; Type: FK CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.execution_materialization_blocks
    ADD CONSTRAINT execution_materialization_blocks_exit_decision_id_fkey FOREIGN KEY (exit_decision_id) REFERENCES strategy_exit.exit_decisions(exit_decision_id);


--
-- Name: execution_materialization_blocks execution_materialization_blocks_strategy_position_id_fkey; Type: FK CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.execution_materialization_blocks
    ADD CONSTRAINT execution_materialization_blocks_strategy_position_id_fkey FOREIGN KEY (strategy_position_id) REFERENCES runtime.position_ownership(position_id);


--
-- Name: execution_requests execution_requests_exit_decision_id_strategy_position_id_s_fkey; Type: FK CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.execution_requests
    ADD CONSTRAINT execution_requests_exit_decision_id_strategy_position_id_s_fkey FOREIGN KEY (exit_decision_id, strategy_position_id, strategy_id, strategy_version, strategy_config_fingerprint, exit_plan_fingerprint) REFERENCES strategy_exit.exit_decisions(exit_decision_id, strategy_position_id, strategy_id, strategy_version, strategy_config_fingerprint, exit_plan_fingerprint);


--
-- Name: exit_decisions exit_decisions_exit_plan_fingerprint_strategy_id_strategy__fkey; Type: FK CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.exit_decisions
    ADD CONSTRAINT exit_decisions_exit_plan_fingerprint_strategy_id_strategy__fkey FOREIGN KEY (exit_plan_fingerprint, strategy_id, strategy_version, strategy_config_fingerprint) REFERENCES strategy_entry.exit_plans(exit_plan_fingerprint, strategy_id, strategy_version, strategy_config_fingerprint);


--
-- Name: exit_decisions exit_decisions_observation_fkey; Type: FK CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.exit_decisions
    ADD CONSTRAINT exit_decisions_observation_fkey FOREIGN KEY (observation_id, strategy_position_id, exit_plan_fingerprint) REFERENCES strategy_exit.exit_observations(observation_id, strategy_position_id, exit_plan_fingerprint);


--
-- Name: exit_decisions exit_decisions_strategy_position_id_fkey; Type: FK CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.exit_decisions
    ADD CONSTRAINT exit_decisions_strategy_position_id_fkey FOREIGN KEY (strategy_position_id) REFERENCES runtime.position_ownership(position_id);


--
-- Name: execution_requests exit_execution_requests_strategy_execution_binding_fkey; Type: FK CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.execution_requests
    ADD CONSTRAINT exit_execution_requests_strategy_execution_binding_fkey FOREIGN KEY (strategy_execution_binding_id) REFERENCES strategy_entry.strategy_execution_bindings(strategy_execution_binding_id) NOT VALID;


--
-- Name: execution_requests exit_execution_requests_trading_account_id_fkey; Type: FK CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.execution_requests
    ADD CONSTRAINT exit_execution_requests_trading_account_id_fkey FOREIGN KEY (trading_account_id) REFERENCES identity.trading_accounts(trading_account_id) NOT VALID;


--
-- Name: exit_observations exit_observations_exit_plan_fingerprint_fkey; Type: FK CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.exit_observations
    ADD CONSTRAINT exit_observations_exit_plan_fingerprint_fkey FOREIGN KEY (exit_plan_fingerprint) REFERENCES strategy_entry.exit_plans(exit_plan_fingerprint);


--
-- Name: exit_observations exit_observations_strategy_position_id_fkey; Type: FK CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.exit_observations
    ADD CONSTRAINT exit_observations_strategy_position_id_fkey FOREIGN KEY (strategy_position_id) REFERENCES runtime.position_ownership(position_id);


--
-- Name: shadow_evaluations shadow_evaluations_exit_decision_id_fkey; Type: FK CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.shadow_evaluations
    ADD CONSTRAINT shadow_evaluations_exit_decision_id_fkey FOREIGN KEY (exit_decision_id) REFERENCES strategy_exit.exit_decisions(exit_decision_id);


--
-- Name: shadow_evaluations shadow_evaluations_observation_id_strategy_position_id_exi_fkey; Type: FK CONSTRAINT; Schema: strategy_exit; Owner: -
--

ALTER TABLE ONLY strategy_exit.shadow_evaluations
    ADD CONSTRAINT shadow_evaluations_observation_id_strategy_position_id_exi_fkey FOREIGN KEY (observation_id, strategy_position_id, exit_plan_fingerprint) REFERENCES strategy_exit.exit_observations(observation_id, strategy_position_id, exit_plan_fingerprint);


--
-- Name: exit_decisions exit_decisions_economics_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: supervisor; Owner: -
--

ALTER TABLE ONLY supervisor.exit_decisions
    ADD CONSTRAINT exit_decisions_economics_snapshot_id_fkey FOREIGN KEY (economics_snapshot_id) REFERENCES supervisor.exit_economics_snapshots(id);


--
-- Name: SCHEMA analytics; Type: ACL; Schema: -; Owner: -
--

GRANT USAGE ON SCHEMA analytics TO cripta;


--
-- Name: SCHEMA dispatcher_v2; Type: ACL; Schema: -; Owner: -
--

GRANT USAGE ON SCHEMA dispatcher_v2 TO cripta;


--
-- Name: SCHEMA identity; Type: ACL; Schema: -; Owner: -
--

GRANT USAGE ON SCHEMA identity TO cripta;


--
-- Name: SCHEMA strategy_entry; Type: ACL; Schema: -; Owner: -
--

GRANT USAGE ON SCHEMA strategy_entry TO cripta;


--
-- Name: SCHEMA strategy_exit; Type: ACL; Schema: -; Owner: -
--

GRANT USAGE ON SCHEMA strategy_exit TO cripta;


--
-- Name: FUNCTION reject_immutable_change(); Type: ACL; Schema: runtime; Owner: -
--

GRANT ALL ON FUNCTION runtime.reject_immutable_change() TO cripta;


--
-- Name: TABLE counterfactual_candidates; Type: ACL; Schema: analytics; Owner: -
--

GRANT SELECT,INSERT ON TABLE analytics.counterfactual_candidates TO cripta;


--
-- Name: TABLE counterfactual_outcomes; Type: ACL; Schema: analytics; Owner: -
--

GRANT SELECT,INSERT ON TABLE analytics.counterfactual_outcomes TO cripta;


--
-- Name: TABLE entry_advisory_contexts; Type: ACL; Schema: analytics; Owner: -
--

GRANT SELECT ON TABLE analytics.entry_advisory_contexts TO cripta;


--
-- Name: TABLE market_minutes; Type: ACL; Schema: analytics; Owner: -
--

GRANT SELECT,INSERT ON TABLE analytics.market_minutes TO cripta;


--
-- Name: TABLE position_exit_attribution; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT ON TABLE runtime.position_exit_attribution TO cripta;


--
-- Name: TABLE position_ownership; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE runtime.position_ownership TO cripta;


--
-- Name: TABLE position_exit_attribution_v1; Type: ACL; Schema: analytics; Owner: -
--

GRANT SELECT ON TABLE analytics.position_exit_attribution_v1 TO cripta;


--
-- Name: TABLE entry_geometry_handoffs; Type: ACL; Schema: monitoring; Owner: -
--

GRANT SELECT,INSERT ON TABLE monitoring.entry_geometry_handoffs TO cripta;


--
-- Name: TABLE position_lifecycle_identity; Type: ACL; Schema: analytics; Owner: -
--

GRANT SELECT ON TABLE analytics.position_lifecycle_identity TO cripta;


--
-- Name: TABLE shared_market_contexts; Type: ACL; Schema: mayak_v2; Owner: -
--

GRANT SELECT,INSERT ON TABLE mayak_v2.shared_market_contexts TO cripta;


--
-- Name: TABLE shared_market_context_consumption; Type: ACL; Schema: analytics; Owner: -
--

GRANT SELECT ON TABLE analytics.shared_market_context_consumption TO cripta;


--
-- Name: TABLE execution_gate_events; Type: ACL; Schema: control; Owner: -
--

GRANT SELECT,INSERT ON TABLE control.execution_gate_events TO cripta;


--
-- Name: SEQUENCE execution_gate_events_id_seq; Type: ACL; Schema: control; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE control.execution_gate_events_id_seq TO cripta;


--
-- Name: TABLE live_arm_evidence; Type: ACL; Schema: control; Owner: -
--

GRANT SELECT,INSERT ON TABLE control.live_arm_evidence TO cripta;


--
-- Name: TABLE live_arm_retirement_events; Type: ACL; Schema: control; Owner: -
--

GRANT SELECT,INSERT ON TABLE control.live_arm_retirement_events TO cripta;


--
-- Name: TABLE live_arm_sessions; Type: ACL; Schema: control; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE control.live_arm_sessions TO cripta;


--
-- Name: TABLE release_arm_incidents; Type: ACL; Schema: control; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE control.release_arm_incidents TO cripta;


--
-- Name: TABLE coin_market_contexts; Type: ACL; Schema: dispatcher_v2; Owner: -
--

GRANT SELECT,INSERT ON TABLE dispatcher_v2.coin_market_contexts TO cripta;


--
-- Name: TABLE global_market_contexts; Type: ACL; Schema: dispatcher_v2; Owner: -
--

GRANT SELECT,INSERT ON TABLE dispatcher_v2.global_market_contexts TO cripta;


--
-- Name: TABLE trading_capacity_snapshots; Type: ACL; Schema: dispatcher_v2; Owner: -
--

GRANT SELECT,INSERT ON TABLE dispatcher_v2.trading_capacity_snapshots TO cripta;


--
-- Name: TABLE account_connectors; Type: ACL; Schema: identity; Owner: -
--

GRANT SELECT ON TABLE identity.account_connectors TO cripta;


--
-- Name: TABLE instruments; Type: ACL; Schema: identity; Owner: -
--

GRANT SELECT ON TABLE identity.instruments TO cripta;


--
-- Name: TABLE market_sources; Type: ACL; Schema: identity; Owner: -
--

GRANT SELECT ON TABLE identity.market_sources TO cripta;


--
-- Name: TABLE trading_accounts; Type: ACL; Schema: identity; Owner: -
--

GRANT SELECT ON TABLE identity.trading_accounts TO cripta;


--
-- Name: TABLE venues; Type: ACL; Schema: identity; Owner: -
--

GRANT SELECT ON TABLE identity.venues TO cripta;


--
-- Name: TABLE coin_market_contexts; Type: ACL; Schema: mayak_v2; Owner: -
--

GRANT SELECT,INSERT ON TABLE mayak_v2.coin_market_contexts TO cripta;


--
-- Name: TABLE market_observation_alert_deliveries; Type: ACL; Schema: mayak_v2; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE mayak_v2.market_observation_alert_deliveries TO cripta;


--
-- Name: TABLE market_observation_alerts; Type: ACL; Schema: mayak_v2; Owner: -
--

GRANT SELECT,INSERT ON TABLE mayak_v2.market_observation_alerts TO cripta;


--
-- Name: TABLE dispatcher_v2_event_links; Type: ACL; Schema: research_context; Owner: -
--

GRANT SELECT,INSERT ON TABLE research_context.dispatcher_v2_event_links TO cripta;


--
-- Name: TABLE account_state_generations; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE runtime.account_state_generations TO cripta;


--
-- Name: TABLE capital_reservations; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE runtime.capital_reservations TO cripta;


--
-- Name: TABLE effective_protection_evidence; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT ON TABLE runtime.effective_protection_evidence TO cripta;


--
-- Name: TABLE entry_cancel_intents; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT ON TABLE runtime.entry_cancel_intents TO cripta;


--
-- Name: TABLE entry_geometry_bindings; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE runtime.entry_geometry_bindings TO cripta;


--
-- Name: TABLE exchange_order_history; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE runtime.exchange_order_history TO cripta;


--
-- Name: TABLE exchange_position_slot_claims; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE runtime.exchange_position_slot_claims TO cripta;


--
-- Name: TABLE lifecycle_fault_deliveries; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE runtime.lifecycle_fault_deliveries TO cripta;


--
-- Name: TABLE lifecycle_faults; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE runtime.lifecycle_faults TO cripta;


--
-- Name: TABLE owner_manual_interventions; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT ON TABLE runtime.owner_manual_interventions TO cripta;


--
-- Name: TABLE plan_consumptions; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE runtime.plan_consumptions TO cripta;


--
-- Name: TABLE position_exit_claims; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE runtime.position_exit_claims TO cripta;


--
-- Name: TABLE position_lifecycle_events; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT ON TABLE runtime.position_lifecycle_events TO cripta;


--
-- Name: TABLE position_mode_states; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT ON TABLE runtime.position_mode_states TO cripta;


--
-- Name: TABLE protection_events; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT ON TABLE runtime.protection_events TO cripta;


--
-- Name: TABLE recovered_closed_entry_cycles; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT ON TABLE runtime.recovered_closed_entry_cycles TO cripta;


--
-- Name: TABLE trade_lifecycle_events; Type: ACL; Schema: runtime; Owner: -
--

GRANT SELECT,INSERT ON TABLE runtime.trade_lifecycle_events TO cripta;


--
-- Name: TABLE context_links; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_entry.context_links TO cripta;


--
-- Name: TABLE entry_decisions; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_entry.entry_decisions TO cripta;


--
-- Name: TABLE entry_plans; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_entry.entry_plans TO cripta;


--
-- Name: TABLE execution_dispatches; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_entry.execution_dispatches TO cripta;


--
-- Name: TABLE execution_permission_events; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT ON TABLE strategy_entry.execution_permission_events TO cripta;


--
-- Name: SEQUENCE execution_permission_events_execution_permission_event_id_seq; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE strategy_entry.execution_permission_events_execution_permission_event_id_seq TO cripta;


--
-- Name: TABLE execution_permissions; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE strategy_entry.execution_permissions TO cripta;


--
-- Name: TABLE execution_request_state_events; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_entry.execution_request_state_events TO cripta;


--
-- Name: TABLE execution_requests; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_entry.execution_requests TO cripta;


--
-- Name: TABLE exit_plans; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_entry.exit_plans TO cripta;


--
-- Name: TABLE notifications; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_entry.notifications TO cripta;


--
-- Name: TABLE paper_orders; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE strategy_entry.paper_orders TO cripta;


--
-- Name: TABLE paper_position_events; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_entry.paper_position_events TO cripta;


--
-- Name: SEQUENCE paper_position_events_paper_event_id_seq; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,USAGE ON SEQUENCE strategy_entry.paper_position_events_paper_event_id_seq TO cripta;


--
-- Name: TABLE paper_positions; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE strategy_entry.paper_positions TO cripta;


--
-- Name: TABLE reverse_transitions; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE strategy_entry.reverse_transitions TO cripta;


--
-- Name: TABLE sensor_links; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_entry.sensor_links TO cripta;


--
-- Name: TABLE shadow_parity_events; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_entry.shadow_parity_events TO cripta;


--
-- Name: TABLE shadow_parity_runs; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_entry.shadow_parity_runs TO cripta;


--
-- Name: COLUMN shadow_parity_runs.status; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT UPDATE(status) ON TABLE strategy_entry.shadow_parity_runs TO cripta;


--
-- Name: COLUMN shadow_parity_runs.finished_at; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT UPDATE(finished_at) ON TABLE strategy_entry.shadow_parity_runs TO cripta;


--
-- Name: COLUMN shadow_parity_runs.summary; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT UPDATE(summary) ON TABLE strategy_entry.shadow_parity_runs TO cripta;


--
-- Name: TABLE strategy_activation_events; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT ON TABLE strategy_entry.strategy_activation_events TO cripta;


--
-- Name: TABLE strategy_activations; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT,UPDATE ON TABLE strategy_entry.strategy_activations TO cripta;


--
-- Name: TABLE strategy_attempts; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_entry.strategy_attempts TO cripta;


--
-- Name: TABLE strategy_cards; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_entry.strategy_cards TO cripta;


--
-- Name: TABLE strategy_execution_bindings; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT ON TABLE strategy_entry.strategy_execution_bindings TO cripta;


--
-- Name: TABLE strategy_signals; Type: ACL; Schema: strategy_entry; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_entry.strategy_signals TO cripta;


--
-- Name: TABLE execution_dispatches; Type: ACL; Schema: strategy_exit; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_exit.execution_dispatches TO cripta;


--
-- Name: TABLE execution_materialization_blocks; Type: ACL; Schema: strategy_exit; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_exit.execution_materialization_blocks TO cripta;


--
-- Name: TABLE execution_requests; Type: ACL; Schema: strategy_exit; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_exit.execution_requests TO cripta;


--
-- Name: TABLE exit_decisions; Type: ACL; Schema: strategy_exit; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_exit.exit_decisions TO cripta;


--
-- Name: TABLE exit_observations; Type: ACL; Schema: strategy_exit; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_exit.exit_observations TO cripta;


--
-- Name: TABLE shadow_evaluations; Type: ACL; Schema: strategy_exit; Owner: -
--

GRANT SELECT,INSERT ON TABLE strategy_exit.shadow_evaluations TO cripta;


--
-- PostgreSQL database dump complete
--


