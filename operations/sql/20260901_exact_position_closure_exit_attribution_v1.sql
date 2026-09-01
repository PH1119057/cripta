BEGIN;

ALTER TABLE runtime.position_ownership
    ADD COLUMN IF NOT EXISTS exchange_position_key text;
ALTER TABLE runtime.position_ownership
    ADD COLUMN IF NOT EXISTS position_idx integer NOT NULL DEFAULT 0;
ALTER TABLE runtime.position_ownership
    ADD COLUMN IF NOT EXISTS closed_at timestamptz;
ALTER TABLE runtime.position_ownership
    ADD COLUMN IF NOT EXISTS exit_order_id text;
ALTER TABLE runtime.position_ownership
    ADD COLUMN IF NOT EXISTS exit_order_ids jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE runtime.position_ownership
    ADD COLUMN IF NOT EXISTS exit_execution_ids jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE runtime.position_ownership
    ADD COLUMN IF NOT EXISTS close_link_status text NOT NULL DEFAULT 'OPEN';

CREATE TABLE IF NOT EXISTS runtime.protection_events (
    protection_event_id text PRIMARY KEY,
    position_id text NOT NULL REFERENCES runtime.position_ownership(position_id),
    trade_id text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
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
    exchange_order_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    execution_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    source_payload jsonb NOT NULL,
    provenance jsonb NOT NULL,
    CHECK (protection_kind IN (
        'INITIAL_HARD_STOP','PROFIT_PROTECTION_STOP','TRAILING_STOP',
        'TAKE_PROFIT','OWNER_MODIFIED_STOP','UNKNOWN')),
    CHECK (initiator IN ('ALGORITHM','OWNER','TECHNICAL_SAFETY','EXCHANGE','UNKNOWN'))
);

CREATE TABLE IF NOT EXISTS runtime.owner_manual_interventions (
    intervention_id text PRIMARY KEY,
    position_id text REFERENCES runtime.position_ownership(position_id),
    trade_id text,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    action text NOT NULL,
    command_id text,
    exchange_order_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    before_state jsonb NOT NULL,
    after_state jsonb NOT NULL,
    provenance jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime.position_exit_attribution (
    attribution_id text PRIMARY KEY,
    position_id text UNIQUE NOT NULL REFERENCES runtime.position_ownership(position_id),
    trade_id text UNIQUE NOT NULL,
    closed_at timestamptz NOT NULL,
    link_status text NOT NULL,
    link_method text NOT NULL,
    exit_owner text NOT NULL,
    exit_mechanism text NOT NULL,
    exit_order_id text,
    exit_order_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
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
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK (link_status IN ('EXACT','UNRESOLVED_EXACT_LINK')),
    CHECK (exit_owner IN ('ALGORITHM','OWNER','TECHNICAL_SAFETY','EXCHANGE','UNKNOWN')),
    CHECK (exit_mechanism IN (
        'INITIAL_HARD_STOP','PROFIT_PROTECTION_STOP','TRAILING_STOP','TAKE_PROFIT',
        'STRATEGY_EXIT','OWNER_MANUAL_STOP','OWNER_MANUAL_CLOSE','TECHNICAL_CLOSE','UNKNOWN'))
);
ALTER TABLE runtime.position_exit_attribution
    ADD COLUMN IF NOT EXISTS exit_order_ids jsonb NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS runtime.position_lifecycle_events (
    lifecycle_event_id text PRIMARY KEY,
    position_id text NOT NULL REFERENCES runtime.position_ownership(position_id),
    trade_id text NOT NULL,
    event_type text NOT NULL,
    occurred_at timestamptz NOT NULL,
    exact_ids jsonb NOT NULL,
    payload jsonb NOT NULL,
    provenance jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime.exchange_order_history (
    order_id text PRIMARY KEY,
    order_link_id text NOT NULL DEFAULT '',
    symbol text NOT NULL,
    side text NOT NULL,
    order_status text NOT NULL,
    updated_at_epoch_ms bigint,
    payload_json jsonb NOT NULL,
    refreshed_at_epoch_ms bigint NOT NULL
);

CREATE OR REPLACE VIEW analytics.position_exit_attribution_v1 AS
SELECT o.signal_id, o.strategy_id, o.strategy_version, o.entry_command_id,
       o.geometry_handoff_id, o.position_id, o.trade_id, o.symbol, o.side,
       o.actual_avg_fill, o.actual_qty, o.fill_at, o.state,
       a.closed_at, a.link_status, a.link_method, a.exit_owner,
       a.exit_mechanism, a.exit_order_id, a.exit_execution_ids,
       a.intended_initial_hard_stop, a.actual_exchange_stop_before_exit,
       a.exchange_trigger_price, a.trigger_by, a.actual_exit_avg_fill,
       a.actual_exit_qty, a.entry_to_exit_price_move_pct,
       a.trigger_to_fill_slippage_pct, a.gross_pnl, a.entry_fee_actual,
       a.exit_fee_actual, a.funding, a.actual_net_without_funding,
       a.actual_net_pnl, a.economics_completeness, a.evidence,
       a.exit_order_ids
FROM runtime.position_ownership o
LEFT JOIN runtime.position_exit_attribution a USING(position_id, trade_id);

GRANT SELECT, INSERT ON runtime.protection_events,
    runtime.owner_manual_interventions, runtime.position_exit_attribution,
    runtime.position_lifecycle_events, runtime.exchange_order_history TO cripta;
GRANT UPDATE ON runtime.exchange_order_history TO cripta;
GRANT SELECT, UPDATE ON runtime.position_ownership TO cripta;
GRANT USAGE ON SCHEMA analytics TO cripta;
GRANT SELECT ON analytics.position_exit_attribution_v1 TO cripta;

COMMIT;
