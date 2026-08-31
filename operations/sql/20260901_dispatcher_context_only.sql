BEGIN;

ALTER TABLE runtime.m3_consumed_context
    DROP CONSTRAINT IF EXISTS m3_consumed_context_trading_effect_check;

ALTER TABLE runtime.m3_consumed_context
    ADD CONSTRAINT m3_consumed_context_trading_effect_check
    CHECK (trading_effect IN ('FULL_LIVE_V1', 'NONE', 'CONTEXT_ONLY'));

-- Historical FULL_LIVE_V1/BLOCK rows are evidence of the old runtime and remain
-- unchanged. New runtime rows use decision=OBSERVED and trading_effect=NONE.

GRANT SELECT, INSERT ON runtime.m3_consumed_context TO cripta;

COMMIT;
