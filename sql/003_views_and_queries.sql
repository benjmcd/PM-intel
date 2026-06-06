-- Prediction Market Flow Intelligence — Views and Diagnostics

SET search_path TO pmfi, public;

CREATE OR REPLACE VIEW v_recent_raw_event_counts AS
SELECT
    venue_code,
    source_channel,
    source_event_type,
    date_trunc('hour', received_at) AS hour,
    count(*) AS event_count
FROM raw_events
GROUP BY venue_code, source_channel, source_event_type, date_trunc('hour', received_at);

CREATE OR REPLACE VIEW v_recent_large_trades AS
SELECT
    venue_code,
    market_id,
    outcome_key,
    directional_side,
    price,
    contracts,
    capital_at_risk_usd,
    payout_notional_usd,
    exchange_ts,
    received_at
FROM normalized_trades
WHERE received_at >= now() - interval '7 days'
ORDER BY capital_at_risk_usd DESC NULLS LAST;

CREATE OR REPLACE VIEW v_open_data_quality_incidents AS
SELECT *
FROM data_quality_incidents
WHERE status = 'open'
ORDER BY severity DESC, started_at DESC;

CREATE OR REPLACE VIEW v_alert_summary_24h AS
SELECT
    venue_code,
    severity,
    confidence,
    data_quality,
    count(*) AS alert_count,
    max(fired_at) AS last_fired_at
FROM alerts
WHERE fired_at >= now() - interval '24 hours'
GROUP BY venue_code, severity, confidence, data_quality;
