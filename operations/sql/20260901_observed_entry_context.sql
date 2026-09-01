BEGIN;

ALTER TABLE runtime.m3_consumed_context
    DROP CONSTRAINT IF EXISTS m3_consumed_context_context_type_check;

ALTER TABLE runtime.m3_consumed_context
    ADD CONSTRAINT m3_consumed_context_context_type_check
    CHECK (context_type IN ('CONSUMED_CONTEXT', 'OBSERVED_CONTEXT'));

COMMENT ON TABLE runtime.m3_consumed_context IS
    'Legacy physical name. Historical CONSUMED_CONTEXT rows are preserved; new advisory Entry links use OBSERVED_CONTEXT with trading_effect=NONE.';

CREATE OR REPLACE VIEW analytics.entry_advisory_contexts AS
SELECT signal_id, symbol, direction, signal_at, assessment_id, mayak_snapshot_id,
       market_context_id, assessment_observed_at, strategy_decision_at,
       profile_id, profile_version, dispatcher_status, decision, reason_ru,
       context_type, trading_effect, payload
FROM runtime.m3_consumed_context;

GRANT SELECT, INSERT ON runtime.m3_consumed_context TO cripta;
GRANT USAGE ON SCHEMA analytics TO cripta;
GRANT SELECT ON analytics.entry_advisory_contexts TO cripta;

COMMIT;
