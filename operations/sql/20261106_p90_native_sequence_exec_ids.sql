-- Issue #469: exact same-sequence publicTrade dedup sidecar for the
-- existing atomic P90 REAL/PAPER native checkpoint owner only.
-- Migration actor: postgres under verified release; runtime DB role: cripta.
-- No Strategy/Entry/Exit policy, Exchange mutation, gate or OWNER ARM effect.
-- This is rolling native cursor state, NOT a market history/fact ledger.
-- The writer may delete only the previous sequence *in the same transaction*
-- as committing the new sequence's checkpoint and downstream effects.
BEGIN;
SET LOCAL lock_timeout='3s';
SET LOCAL statement_timeout='20s';

CREATE TABLE IF NOT EXISTS strategy_entry.p90l90_native_sequence_exec_ids (
  mode text NOT NULL CHECK(mode IN ('REAL','PAPER')),
  source_epoch_id text NOT NULL CHECK (
    (mode='PAPER' AND source_epoch_id='PAPER')
    OR (mode='REAL' AND source_epoch_id ~ '^p90-real-epoch-[0-9a-f]{40}$')
  ),
  source_commit text NOT NULL CHECK(source_commit ~ '^[0-9a-f]{40}$'),
  symbol text NOT NULL CHECK(
    symbol IN ('LTCUSDT','INJUSDT','DOTUSDT','ARBUSDT','APTUSDT')
  ),
  exchange_sequence bigint NOT NULL CHECK(exchange_sequence >= 0),
  exec_id text NOT NULL CHECK(length(exec_id) > 0),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(mode,source_epoch_id,source_commit,symbol,exchange_sequence,exec_id)
);

REVOKE ALL ON strategy_entry.p90l90_native_sequence_exec_ids FROM PUBLIC;
-- DELETE is scoped solely to this bounded rolling transport-state relation;
-- current/immutable source epochs, checkpoints, original facts and audit retain
-- their existing no-DELETE grants.
GRANT SELECT,INSERT,DELETE ON
  strategy_entry.p90l90_native_sequence_exec_ids TO cripta;
REVOKE UPDATE,TRUNCATE ON
  strategy_entry.p90l90_native_sequence_exec_ids FROM cripta;

DO $verify$
BEGIN
  IF NOT has_table_privilege(
        'cripta','strategy_entry.p90l90_native_sequence_exec_ids',
        'SELECT,INSERT,DELETE')
     OR has_table_privilege(
        'cripta','strategy_entry.p90l90_native_sequence_exec_ids',
        'UPDATE')
     OR has_table_privilege(
        'cripta','strategy_entry.p90l90_native_sequence_exec_ids',
        'TRUNCATE')
     OR has_table_privilege(
        'cripta','strategy_entry.p90l90_real_causal_checkpoints',
        'DELETE')
  THEN
    RAISE EXCEPTION 'P90_NATIVE_SEQ_ID_ROLLING_LEAST_PRIVILEGE_INVARIANT';
  END IF;
END
$verify$;
COMMIT;
