-- Issue #243: NEW REAL v3 market/Entry epoch, never UPDATE PAPER history.
-- Actor: postgres via backed-up verified release; additive only.
-- MarketSource origin: ONE canonical analytics.closed_candles owner.
-- This schema alone does not bootstrap a WS epoch or grant any trading arm.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

CREATE TABLE IF NOT EXISTS strategy_entry.p90l90_real_source_epochs (
  epoch_id text PRIMARY KEY
    CHECK (epoch_id ~ '^p90-real-epoch-[0-9a-f]{40}$'),
  source_commit text NOT NULL
    CHECK (source_commit ~ '^[0-9a-f]{40}$'),
  market_source_id text NOT NULL
    CHECK (market_source_id='ms-bybit-mainnet-public-v1'),
  opened_at timestamptz NOT NULL,
  source_ws_ready_at timestamptz NOT NULL,
  native_mirror_ready_at timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'ACTIVE'
    CHECK (status IN ('ACTIVE','RETIRED')),
  retired_at timestamptz,
  retire_reason text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (
    (status='ACTIVE' AND retired_at IS NULL AND retire_reason IS NULL)
    OR (status='RETIRED' AND retired_at IS NOT NULL
        AND nullif(btrim(retire_reason),'') IS NOT NULL)
  ),
  CHECK (source_ws_ready_at >= opened_at
         AND native_mirror_ready_at >= opened_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS p90_real_at_most_one_active_epoch
  ON strategy_entry.p90l90_real_source_epochs(status)
  WHERE status='ACTIVE';

CREATE TABLE IF NOT EXISTS strategy_entry.p90l90_real_causal_checkpoints (
  epoch_id text NOT NULL REFERENCES strategy_entry.p90l90_real_source_epochs(epoch_id),
  symbol text NOT NULL
    CHECK (symbol IN ('LTCUSDT','INJUSDT','DOTUSDT','ARBUSDT','APTUSDT')),
  checkpoint_json jsonb NOT NULL
    CHECK (jsonb_typeof(checkpoint_json)='object'
       AND checkpoint_json->>'schema'='P90L90_CAUSAL_TRANSPORT_JOIN_V1'
       AND checkpoint_json->>'symbol'=symbol),
  entry_checkpoint_json jsonb NOT NULL
    CHECK (jsonb_typeof(entry_checkpoint_json)='object'
       AND entry_checkpoint_json->>'schema'='P90L90_CAUSAL_ENTRY_RESTART_V1'),
  source_commit text NOT NULL CHECK (source_commit ~ '^[0-9a-f]{40}$'),
  writer_release_commit text
    CHECK (writer_release_commit ~ '^[0-9a-f]{40}$'),
  generation bigint NOT NULL DEFAULT 0 CHECK (generation >= 0),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (epoch_id,symbol)
);
CREATE INDEX IF NOT EXISTS p90_real_epoch_checkpoints_lookup
  ON strategy_entry.p90l90_real_causal_checkpoints(symbol,epoch_id);

-- Append-only evidence that an existing open position survived an Observer
-- process restart without replacing its original source epoch. The bridge is
-- written only after exact bounded publicTrade replay, causal candle replay,
-- MFE reconstruction and fresh signed MAIN/SUB protection readback all pass.
CREATE TABLE IF NOT EXISTS strategy_entry.p90l90_real_restart_bridges (
  restart_id text PRIMARY KEY
    CHECK (restart_id ~ '^p90-real-restart-[0-9a-f]{40}$'),
  epoch_id text NOT NULL
    REFERENCES strategy_entry.p90l90_real_source_epochs(epoch_id),
  source_commit text NOT NULL
    CHECK (source_commit ~ '^[0-9a-f]{40}$'),
  mirror_serial bigint NOT NULL CHECK (mirror_serial > 0),
  mirror_started_at timestamptz NOT NULL,
  source_ws_ready_at timestamptz NOT NULL,
  completed_at timestamptz NOT NULL,
  symbol_cursors jsonb NOT NULL CHECK (jsonb_typeof(symbol_cursors)='object'),
  position_cursors jsonb NOT NULL CHECK (jsonb_typeof(position_cursors)='object'),
  account_generations jsonb NOT NULL
    CHECK (jsonb_typeof(account_generations)='object'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (source_ws_ready_at >= mirror_started_at),
  CHECK (completed_at >= source_ws_ready_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS p90_real_restart_bridge_source_session
  ON strategy_entry.p90l90_real_restart_bridges(
    epoch_id,source_commit,mirror_started_at
  );

REVOKE ALL ON strategy_entry.p90l90_real_source_epochs FROM PUBLIC;
REVOKE ALL ON strategy_entry.p90l90_real_causal_checkpoints FROM PUBLIC;
REVOKE ALL ON strategy_entry.p90l90_real_restart_bridges FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON
  strategy_entry.p90l90_real_source_epochs,
  strategy_entry.p90l90_real_causal_checkpoints TO cripta;
GRANT SELECT,INSERT ON
  strategy_entry.p90l90_real_restart_bridges TO cripta;
REVOKE UPDATE,DELETE,TRUNCATE ON
  strategy_entry.p90l90_real_restart_bridges FROM cripta;
REVOKE DELETE,TRUNCATE ON
  strategy_entry.p90l90_real_source_epochs,
  strategy_entry.p90l90_real_causal_checkpoints FROM cripta;
DO $verify$
BEGIN
  IF has_table_privilege('cripta','strategy_entry.p90l90_real_source_epochs','DELETE')
    OR has_table_privilege('cripta','strategy_entry.p90l90_real_causal_checkpoints','DELETE')
    OR NOT has_table_privilege('cripta','strategy_entry.p90l90_real_causal_checkpoints','SELECT,INSERT,UPDATE')
    OR NOT has_table_privilege('cripta','strategy_entry.p90l90_real_restart_bridges','SELECT,INSERT')
    OR has_table_privilege('cripta','strategy_entry.p90l90_real_restart_bridges','UPDATE')
    OR has_table_privilege('cripta','strategy_entry.p90l90_real_restart_bridges','DELETE')
  THEN
    RAISE EXCEPTION 'P90_REAL_EPOCH_LEAST_PRIVILEGE_INVARIANT';
  END IF;
END
$verify$;
COMMIT;
