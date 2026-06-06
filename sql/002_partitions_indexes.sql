-- Prediction Market Flow Intelligence — Partitions and Indexes
-- Target: PostgreSQL 16+

SET search_path TO pmfi, public;

-- Default partitions keep local setup simple. Codex can replace/add monthly partitions later.
CREATE TABLE IF NOT EXISTS raw_events_default PARTITION OF raw_events DEFAULT;
CREATE TABLE IF NOT EXISTS normalized_trades_default PARTITION OF normalized_trades DEFAULT;
CREATE TABLE IF NOT EXISTS market_snapshots_default PARTITION OF market_snapshots DEFAULT;
CREATE TABLE IF NOT EXISTS orderbook_snapshots_default PARTITION OF orderbook_snapshots DEFAULT;
CREATE TABLE IF NOT EXISTS metric_windows_default PARTITION OF metric_windows DEFAULT;

CREATE INDEX IF NOT EXISTS idx_raw_events_venue_received ON raw_events (venue_code, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_events_market_received ON raw_events (market_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_events_payload_gin ON raw_events USING gin (payload);

CREATE INDEX IF NOT EXISTS idx_normalized_trades_market_received ON normalized_trades (market_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_normalized_trades_venue_received ON normalized_trades (venue_code, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_normalized_trades_size ON normalized_trades (capital_at_risk_usd DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_alerts_fired ON alerts (fired_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_market ON alerts (market_id, fired_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts (severity, fired_at DESC);

CREATE INDEX IF NOT EXISTS idx_job_queue_ready ON job_queue (status, run_after, priority DESC);
CREATE INDEX IF NOT EXISTS idx_dead_letters_unresolved ON dead_letters (resolved, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_data_quality_open ON data_quality_incidents (status, severity, started_at DESC);
