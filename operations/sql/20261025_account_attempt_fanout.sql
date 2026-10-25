-- Account-scoped admission cardinality: one StrategySignal/attempt may
-- produce independent reservations and slot claims per proven execution binding.
-- Preserve historical LEGACY_UNKNOWN (NULL binding) uniqueness and all row IDs.
-- Source-only until an explicitly owner-approved release/migration.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';

-- Postgres 15+ NULLS NOT DISTINCT preserves the old one-row-per-attempt
-- invariant for LEGACY_UNKNOWN, while allowing distinct proven bindings.
-- Create replacement indexes BEFORE removing the existing unique constraints.
CREATE UNIQUE INDEX IF NOT EXISTS ux_capital_attempt_binding_v1
    ON runtime.capital_reservations (
        strategy_attempt_id, strategy_execution_binding_id
    ) NULLS NOT DISTINCT;
CREATE UNIQUE INDEX IF NOT EXISTS ux_slot_attempt_binding_v1
    ON runtime.exchange_position_slot_claims (
        strategy_attempt_id, strategy_execution_binding_id
    ) NULLS NOT DISTINCT;

DO $$
DECLARE
    item record;
    definition text;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('runtime.capital_reservations'::regclass,
         'ux_capital_attempt_binding_v1'::text,
         'capital_reservations_strategy_attempt_id_key'::text),
        ('runtime.exchange_position_slot_claims'::regclass,
         'ux_slot_attempt_binding_v1'::text,
         'exchange_position_slot_claims_strategy_attempt_id_key'::text)
    ) AS v(relid,index_name,old_constraint)
    LOOP
        -- The new index must be exact before the one-attempt constraint goes.
        SELECT pg_get_indexdef(i.indexrelid) INTO definition
          FROM pg_index i
          JOIN pg_class cls ON cls.oid=i.indexrelid
         WHERE i.indrelid=item.relid
           AND cls.relname=item.index_name
           AND i.indisunique AND i.indnullsnotdistinct AND i.indnkeyatts=2;
        IF definition IS NULL
           OR position(
               '(strategy_attempt_id, strategy_execution_binding_id) NULLS NOT DISTINCT'
               IN definition
           )=0 THEN
            RAISE EXCEPTION 'account-scoped attempt index mismatch: %',item.index_name;
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_constraint c
             WHERE c.conrelid=item.relid AND c.conname=item.old_constraint
               AND pg_get_constraintdef(c.oid)<>'UNIQUE (strategy_attempt_id)'
        ) THEN
            RAISE EXCEPTION 'unexpected legacy attempt constraint: %',item.old_constraint;
        END IF;
    END LOOP;
END $$;

ALTER TABLE runtime.capital_reservations
    DROP CONSTRAINT IF EXISTS capital_reservations_strategy_attempt_id_key;
ALTER TABLE runtime.exchange_position_slot_claims
    DROP CONSTRAINT IF EXISTS exchange_position_slot_claims_strategy_attempt_id_key;

COMMIT;
