BEGIN;

CREATE TABLE IF NOT EXISTS strategy_entry.execution_permissions (
    execution_permission_id text PRIMARY KEY,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    enabled boolean NOT NULL,
    enabled_at timestamptz,
    disabled_at timestamptz,
    operator text NOT NULL,
    source text NOT NULL,
    change_reason text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(strategy_id,strategy_version,strategy_config_fingerprint),
    FOREIGN KEY(strategy_id,strategy_version,strategy_config_fingerprint)
      REFERENCES strategy_entry.strategy_cards(strategy_id,strategy_version,strategy_config_fingerprint),
    CHECK ((enabled AND enabled_at IS NOT NULL AND disabled_at IS NULL)
        OR ((NOT enabled) AND disabled_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS strategy_entry.execution_permission_events (
    execution_permission_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    execution_permission_id text NOT NULL
      REFERENCES strategy_entry.execution_permissions(execution_permission_id),
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    enabled boolean NOT NULL,
    enabled_at timestamptz,
    disabled_at timestamptz,
    operator text NOT NULL,
    source text NOT NULL,
    reason text NOT NULL
);

CREATE OR REPLACE FUNCTION strategy_entry.guard_execution_permission_update()
RETURNS trigger LANGUAGE plpgsql AS $$
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

CREATE OR REPLACE FUNCTION strategy_entry.log_execution_permission_event()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
SET search_path=strategy_entry,pg_catalog AS $$
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

DROP TRIGGER IF EXISTS execution_permissions_guard_update
  ON strategy_entry.execution_permissions;
CREATE TRIGGER execution_permissions_guard_update
BEFORE UPDATE ON strategy_entry.execution_permissions
FOR EACH ROW EXECUTE FUNCTION strategy_entry.guard_execution_permission_update();

DROP TRIGGER IF EXISTS execution_permissions_journal
  ON strategy_entry.execution_permissions;
CREATE TRIGGER execution_permissions_journal
AFTER INSERT OR UPDATE ON strategy_entry.execution_permissions
FOR EACH ROW EXECUTE FUNCTION strategy_entry.log_execution_permission_event();

CREATE TABLE IF NOT EXISTS strategy_entry.paper_orders (
    paper_order_id text PRIMARY KEY,
    execution_request_id text UNIQUE NOT NULL
      REFERENCES strategy_entry.execution_requests(execution_request_id),
    strategy_activation_id text NOT NULL
      REFERENCES strategy_entry.strategy_activations(activation_id),
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    exit_plan_fingerprint text NOT NULL,
    signal_id text NOT NULL,
    strategy_attempt_id text NOT NULL,
    entry_decision_id text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL CHECK(direction IN ('LONG','SHORT')),
    state text NOT NULL CHECK(state IN ('PENDING','FILLED','EXPIRED','CANCELLED')),
    order_type text NOT NULL CHECK(order_type IN ('MARKET','LIMIT_OFFSET')),
    requested_at timestamptz NOT NULL,
    reference_price numeric NOT NULL CHECK(reference_price > 0),
    limit_price numeric,
    expires_at timestamptz,
    filled_at timestamptz,
    filled_price numeric,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CHECK(jsonb_typeof(payload)='object')
);

CREATE INDEX IF NOT EXISTS ix_paper_orders_pending_symbol
ON strategy_entry.paper_orders(state,symbol,requested_at);

CREATE TABLE IF NOT EXISTS strategy_entry.paper_positions (
    paper_position_id text PRIMARY KEY,
    paper_order_id text UNIQUE,
    parent_position_id text REFERENCES strategy_entry.paper_positions(paper_position_id),
    leg_type text NOT NULL CHECK(leg_type IN ('PRIMARY','HEDGE')),
    strategy_activation_id text NOT NULL,
    strategy_id text NOT NULL,
    strategy_version text NOT NULL,
    strategy_config_fingerprint text NOT NULL,
    entry_plan_fingerprint text NOT NULL,
    exit_plan_fingerprint text NOT NULL,
    signal_id text NOT NULL,
    symbol text NOT NULL,
    direction text NOT NULL CHECK(direction IN ('LONG','SHORT')),
    state text NOT NULL CHECK(state IN ('OPEN','CLOSED')),
    opened_at timestamptz NOT NULL,
    entry_price numeric NOT NULL CHECK(entry_price > 0),
    stake_usdt numeric NOT NULL CHECK(stake_usdt > 0),
    leverage integer NOT NULL CHECK(leverage > 0),
    notional_usdt numeric NOT NULL CHECK(notional_usdt > 0),
    quantity numeric NOT NULL CHECK(quantity > 0),
    best_price numeric NOT NULL CHECK(best_price > 0),
    mfe_pct numeric NOT NULL DEFAULT 0,
    mae_pct numeric NOT NULL DEFAULT 0,
    active_stop_price numeric,
    trailing_active boolean NOT NULL DEFAULT false,
    hedge_opened boolean NOT NULL DEFAULT false,
    closed_at timestamptz,
    exit_price numeric,
    exit_reason text,
    gross_pnl_usdt numeric,
    gross_return_pct numeric,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY(paper_order_id) REFERENCES strategy_entry.paper_orders(paper_order_id),
    CHECK(jsonb_typeof(payload)='object')
);

CREATE INDEX IF NOT EXISTS ix_paper_positions_open_symbol
ON strategy_entry.paper_positions(state,symbol,opened_at);
CREATE INDEX IF NOT EXISTS ix_paper_positions_strategy
ON strategy_entry.paper_positions(strategy_id,strategy_version,state,opened_at);

CREATE TABLE IF NOT EXISTS strategy_entry.paper_position_events (
    paper_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    paper_position_id text NOT NULL REFERENCES strategy_entry.paper_positions(paper_position_id),
    occurred_at timestamptz NOT NULL,
    event_type text NOT NULL,
    price numeric,
    pnl_usdt numeric,
    return_pct numeric,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK(jsonb_typeof(payload)='object')
);

GRANT SELECT,INSERT,UPDATE ON strategy_entry.execution_permissions TO cripta;
GRANT SELECT ON strategy_entry.execution_permission_events TO cripta;
GRANT USAGE,SELECT ON SEQUENCE strategy_entry.execution_permission_events_execution_permission_event_id_seq TO cripta;
GRANT SELECT,INSERT,UPDATE ON strategy_entry.paper_orders TO cripta;
GRANT SELECT,INSERT,UPDATE ON strategy_entry.paper_positions TO cripta;
GRANT SELECT,INSERT ON strategy_entry.paper_position_events TO cripta;
GRANT USAGE,SELECT ON SEQUENCE strategy_entry.paper_position_events_paper_event_id_seq TO cripta;

COMMIT;
