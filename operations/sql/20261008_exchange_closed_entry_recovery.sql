-- Durable exact exchange evidence for an orphan Entry closed during restart.
-- No Exchange mutations and no guessed StrategyPosition are created here.
CREATE TABLE IF NOT EXISTS runtime.recovered_closed_entry_cycles (
    reservation_id text PRIMARY KEY REFERENCES runtime.capital_reservations(reservation_id),
    account_ref text NOT NULL,
    strategy_attempt_id text NOT NULL,
    symbol text NOT NULL,
    side text NOT NULL CHECK (side IN ('Buy','Sell')),
    entry_order_id text NOT NULL,
    entry_execution_ids jsonb NOT NULL,
    exit_order_ids jsonb NOT NULL,
    exit_execution_ids jsonb NOT NULL,
    actual_qty numeric NOT NULL CHECK (actual_qty > 0),
    entry_vwap numeric NOT NULL CHECK (entry_vwap > 0),
    exit_vwap numeric NOT NULL CHECK (exit_vwap > 0),
    entry_fee_actual numeric NOT NULL,
    exit_fee_actual numeric NOT NULL,
    gross_pnl numeric NOT NULL,
    net_without_funding numeric NOT NULL,
    economics_completeness text NOT NULL DEFAULT 'PARTIAL_NO_FUNDING',
    evidence jsonb NOT NULL,
    recovered_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_recovered_closed_entry_order
  ON runtime.recovered_closed_entry_cycles(account_ref,entry_order_id);
CREATE INDEX IF NOT EXISTS ix_recovered_closed_entry_at
  ON runtime.recovered_closed_entry_cycles(recovered_at DESC);
GRANT SELECT, INSERT ON runtime.recovered_closed_entry_cycles TO cripta;
