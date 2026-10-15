BEGIN;

ALTER TABLE runtime.lifecycle_fault_deliveries
    DROP CONSTRAINT lifecycle_fault_deliveries_channel_check;
ALTER TABLE runtime.lifecycle_fault_deliveries
    ADD CONSTRAINT lifecycle_fault_deliveries_channel_check
    CHECK (channel IN ('OWNER_WEBHOOK','DASHBOARD_INBOX'));

-- Existing unresolved observer faults were visible in the console but had no
-- durable console-delivery record. Backfill without claiming delivery.
INSERT INTO runtime.lifecycle_fault_deliveries(
    delivery_id,fault_id,channel,state,attempts,next_attempt_at,
    payload
)
SELECT 'observer-inbox-' || f.fault_id, f.fault_id,
       'DASHBOARD_INBOX','PENDING',0,clock_timestamp(),
       jsonb_build_object('fault_id',f.fault_id,
                          'fault_code',f.fault_code,
                          'channel','DASHBOARD_INBOX')
FROM runtime.lifecycle_faults f
WHERE f.fault_code='UNIVERSAL_ENTRY_OBSERVER_RUNTIME_ERROR'
  AND f.state='OPEN'
ON CONFLICT (fault_id) DO NOTHING;

COMMIT;
