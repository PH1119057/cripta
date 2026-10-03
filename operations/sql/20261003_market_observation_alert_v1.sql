BEGIN;

CREATE TABLE IF NOT EXISTS mayak_v2.market_observation_alerts (
    alert_id TEXT PRIMARY KEY,
    alert_class TEXT NOT NULL CHECK (alert_class IN (
        'REGIME_CHANGE',
        'MARKET_WIDE_STRESS',
        'SYNCHRONIZATION_SPIKE',
        'LIQUIDATION_CASCADE',
        'DATA_QUALITY_DEGRADATION',
        'SOURCE_OUTAGE'
    )),
    observed_at TIMESTAMPTZ NOT NULL,
    source_component TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    owner_notifiable BOOLEAN NOT NULL DEFAULT FALSE,
    payload JSONB NOT NULL,
    provenance JSONB NOT NULL,
    content_hash TEXT UNIQUE NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK (jsonb_typeof(payload) = 'object'),
    CHECK (jsonb_typeof(provenance) = 'object'),
    CHECK ((provenance->>'trading_command') = 'false')
);

CREATE INDEX IF NOT EXISTS mayak_v2_market_observation_alert_class_at
    ON mayak_v2.market_observation_alerts(alert_class, observed_at DESC);
CREATE INDEX IF NOT EXISTS mayak_v2_market_observation_alert_scope_at
    ON mayak_v2.market_observation_alerts(scope_key, observed_at DESC);

DROP TRIGGER IF EXISTS market_observation_alerts_immutable
ON mayak_v2.market_observation_alerts;
CREATE TRIGGER market_observation_alerts_immutable
BEFORE UPDATE OR DELETE ON mayak_v2.market_observation_alerts
FOR EACH ROW EXECUTE FUNCTION runtime.reject_immutable_change();

CREATE TABLE IF NOT EXISTS mayak_v2.market_observation_alert_deliveries (
    delivery_id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL UNIQUE
        REFERENCES mayak_v2.market_observation_alerts(alert_id),
    channel TEXT NOT NULL CHECK (channel IN ('OWNER_WEBHOOK')),
    state TEXT NOT NULL CHECK (
        state IN ('PENDING','DELIVERED','ACKNOWLEDGED','ESCALATION_REQUIRED')
    ),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at TIMESTAMPTZ NOT NULL,
    last_attempt_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ,
    acknowledged_at TIMESTAMPTZ,
    escalation_at TIMESTAMPTZ,
    last_error TEXT,
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

REVOKE ALL ON mayak_v2.market_observation_alerts FROM PUBLIC;
REVOKE ALL ON mayak_v2.market_observation_alert_deliveries FROM PUBLIC;
GRANT SELECT, INSERT ON mayak_v2.market_observation_alerts TO cripta;
GRANT SELECT, INSERT, UPDATE ON mayak_v2.market_observation_alert_deliveries TO cripta;
REVOKE DELETE ON mayak_v2.market_observation_alert_deliveries FROM cripta;

COMMIT;
