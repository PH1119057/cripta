-- Issue #243 phase 2: one-row atomic P90 native transport + PAPER Entry state.
-- Apply only by approved postgres migration actor during a reviewed release.
-- This creates NO StrategyActivation, position, Bybit order or runtime switch.
-- Existing legacy P90 transport-only checkpoints remain unready (NULL).
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

ALTER TABLE strategy_entry.p90l90_causal_transport_checkpoints
    ADD COLUMN IF NOT EXISTS entry_checkpoint_json jsonb;

DO $constraint$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conrelid =
            'strategy_entry.p90l90_causal_transport_checkpoints'::regclass
          AND conname='p90l90_causal_entry_checkpoint_schema'
    ) THEN
        ALTER TABLE strategy_entry.p90l90_causal_transport_checkpoints
            ADD CONSTRAINT p90l90_causal_entry_checkpoint_schema
            CHECK (
                entry_checkpoint_json IS NULL OR (
                    jsonb_typeof(entry_checkpoint_json)='object'
                    AND entry_checkpoint_json->>'schema'='P90L90_CAUSAL_ENTRY_RESTART_V1'
                )
            );
    END IF;
END
$constraint$;

-- Immutable P90 5m/15m normalized source-fact ledger. Unlike per-trade Entry
-- state this bar history is appended ONCE and never overwritten by runtime.
-- Exact native source vendor JSON is still an independent ingress proof.
CREATE TABLE IF NOT EXISTS strategy_entry.p90l90_causal_closed_bars (
    symbol text NOT NULL
        CHECK (symbol IN ('LTCUSDT','INJUSDT','DOTUSDT','ARBUSDT','APTUSDT')),
    timeframe text NOT NULL CHECK (timeframe IN ('5','15')),
    closed_at timestamptz NOT NULL,
    event_json jsonb NOT NULL
        CHECK (jsonb_typeof(event_json)='object'
               AND event_json->>'kind'='CANDLE_CLOSED'),
    source_commit text NOT NULL CHECK (source_commit ~ '^[0-9a-f]{40}$'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (symbol,timeframe,closed_at),
    CONSTRAINT p90l90_causal_bar_symbol_matches
        CHECK (event_json->>'symbol'=symbol
               AND event_json->>'timeframe'=timeframe)
);
REVOKE ALL ON strategy_entry.p90l90_causal_closed_bars FROM PUBLIC;
GRANT SELECT, INSERT ON strategy_entry.p90l90_causal_closed_bars TO cripta;
REVOKE UPDATE, DELETE, TRUNCATE
    ON strategy_entry.p90l90_causal_closed_bars FROM cripta;
DO $bar_verify$
BEGIN
    IF NOT has_table_privilege(
        'cripta','strategy_entry.p90l90_causal_closed_bars',
        'SELECT,INSERT'
    ) OR has_table_privilege(
        'cripta','strategy_entry.p90l90_causal_closed_bars','UPDATE'
    ) OR has_table_privilege(
        'cripta','strategy_entry.p90l90_causal_closed_bars','DELETE'
    ) THEN
        RAISE EXCEPTION 'P90L90_IMMUTABLE_BAR_ARCHIVE_ACTOR_GRANT_INVALID';
    END IF;
END
$bar_verify$;

-- cripta can update only the already-scoped five-symbol table. It must
-- never delete, truncate, own the table or bypass a missing checkpoint.
REVOKE ALL ON strategy_entry.p90l90_causal_transport_checkpoints FROM PUBLIC;
REVOKE DELETE, TRUNCATE
    ON strategy_entry.p90l90_causal_transport_checkpoints FROM cripta;
DO $verify$
BEGIN
    IF NOT has_table_privilege(
        'cripta','strategy_entry.p90l90_causal_transport_checkpoints',
        'SELECT,INSERT,UPDATE'
    ) OR has_table_privilege(
        'cripta','strategy_entry.p90l90_causal_transport_checkpoints','DELETE'
    ) THEN
        RAISE EXCEPTION 'P90L90_ATOMIC_ENTRY_CHECKPOINT_ACTOR_GRANT_INVALID';
    END IF;
END
$verify$;
COMMIT;
