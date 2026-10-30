-- Scoped, PAPER-only P90L90 native/kline causal buffer for Issue #243.
-- This schema is inert without the reviewed Observer hook; no REAL actors.
-- Applied only by the standard full release migration actor postgres.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

CREATE TABLE IF NOT EXISTS strategy_entry.p90l90_causal_transport_checkpoints (
    symbol text PRIMARY KEY
        CHECK (symbol IN ('LTCUSDT','INJUSDT','DOTUSDT','ARBUSDT','APTUSDT')),
    checkpoint_json jsonb NOT NULL
        CHECK (jsonb_typeof(checkpoint_json)='object'
               AND checkpoint_json->>'schema'='P90L90_CAUSAL_TRANSPORT_JOIN_V1'),
    source_commit text NOT NULL
        CHECK (source_commit ~ '^[0-9a-f]{40}$'),
    generation bigint NOT NULL DEFAULT 0 CHECK (generation>=0),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT p90l90_checkpoint_symbol_same_as_json
        CHECK (checkpoint_json->>'symbol'=symbol)
);

REVOKE ALL ON strategy_entry.p90l90_causal_transport_checkpoints FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE
  ON strategy_entry.p90l90_causal_transport_checkpoints TO cripta;
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
        RAISE EXCEPTION 'P90L90_CAUSAL_TRANSPORT_ACTOR_PRIVILEGE_INVARIANT';
    END IF;
END
$verify$;
COMMIT;
