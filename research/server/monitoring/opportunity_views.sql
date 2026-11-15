CREATE OR REPLACE VIEW monitoring.opportunity_analysis AS
SELECT
    o.*,
    c.market_source_id,
    c.source_session_id,
    c.producer_release_commit,
    c.source_quality,
    c.last_event_at AS source_last_event_at,
    c.updated_at AS source_updated_at,
    CASE WHEN o.last_price IS NULL THEN NULL
         WHEN o.direction = 'long'
           THEN (o.last_price / o.signal_price - 1) * 100
         ELSE (o.signal_price / o.last_price - 1) * 100
    END AS last_move_pct,
    (o.first_hits_json::jsonb ? '+0.1') AS hit_plus_01,
    (o.first_hits_json::jsonb ? '+0.5') AS hit_plus_05,
    (o.first_hits_json::jsonb ? '+0.7') AS hit_plus_07,
    (o.first_hits_json::jsonb ? '+1.0') AS hit_plus_10,
    (o.first_hits_json::jsonb ? '+1.1') AS hit_plus_11,
    (o.first_hits_json::jsonb ? '-0.1') AS hit_minus_01,
    (o.first_hits_json::jsonb ? '-0.2') AS hit_minus_02,
    (o.first_hits_json::jsonb ? '-1.0') AS hit_minus_10,
    (o.first_hits_json::jsonb ? '-3.0') AS hit_minus_30
FROM monitoring.opportunities o
LEFT JOIN monitoring.opportunity_source_checkpoints c
  ON c.signal_id=o.signal_id;

CREATE OR REPLACE VIEW monitoring.opportunity_quality AS
SELECT
    strategy_version,
    decision,
    decision_reason,
    traffic_light,
    symbol,
    direction,
    coalesce(source_quality,'MISSING') AS source_quality,
    count(*) AS signals,
    count(*) FILTER (WHERE state = 'completed') AS completed,
    count(*) FILTER (WHERE state = 'completed_partial') AS completed_partial,
    avg(max_favorable_pct) AS avg_mfe_pct,
    avg(max_adverse_pct) AS avg_mae_pct,
    avg(last_move_pct) FILTER (WHERE state = 'completed') AS avg_last_move_pct,
    avg(hit_plus_01::int) AS hit_plus_01_rate,
    avg(hit_plus_05::int) AS hit_plus_05_rate,
    avg(hit_plus_07::int) AS hit_plus_07_rate,
    avg(hit_plus_10::int) AS hit_plus_10_rate,
    avg(hit_plus_11::int) AS hit_plus_11_rate,
    avg(hit_minus_10::int) AS hit_minus_10_rate,
    avg(hit_minus_30::int) AS hit_minus_30_rate
FROM monitoring.opportunity_analysis
GROUP BY
    strategy_version,decision,decision_reason,traffic_light,
    symbol,direction,coalesce(source_quality,'MISSING');
