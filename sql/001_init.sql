-- Prediction Market Flow Intelligence — Initial Postgres Schema
-- Target: PostgreSQL 16+
-- This schema is intentionally Postgres-first and local-friendly.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

CREATE SCHEMA IF NOT EXISTS pmfi;
SET search_path TO pmfi, public;

-- ---------------------------------------------------------------------------
-- Reference tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS venues (
    venue_code text PRIMARY KEY,
    display_name text NOT NULL,
    base_url text,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO venues (venue_code, display_name, base_url)
VALUES
    ('polymarket', 'Polymarket', 'https://polymarket.com'),
    ('kalshi', 'Kalshi', 'https://kalshi.com')
ON CONFLICT (venue_code) DO NOTHING;

CREATE TABLE IF NOT EXISTS markets (
    market_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_code text NOT NULL REFERENCES venues(venue_code),
    venue_market_id text NOT NULL,
    venue_event_id text,
    slug text,
    title text NOT NULL,
    description text,
    category text,
    tags text[] NOT NULL DEFAULT '{}',
    currency text NOT NULL DEFAULT 'USD',
    status text NOT NULL DEFAULT 'unknown',
    is_active boolean NOT NULL DEFAULT true,
    start_ts timestamptz,
    close_ts timestamptz,
    resolution_ts timestamptz,
    source_url text,
    raw_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata_version text NOT NULL DEFAULT 'metadata.v1',
    watched boolean NOT NULL DEFAULT false,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venue_code, venue_market_id)
);

CREATE TABLE IF NOT EXISTS market_outcomes (
    outcome_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    market_id uuid NOT NULL REFERENCES markets(market_id) ON DELETE CASCADE,
    venue_code text NOT NULL REFERENCES venues(venue_code),
    venue_outcome_id text,
    outcome_key text NOT NULL,
    outcome_label text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    raw_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (market_id, outcome_key),
    UNIQUE (venue_code, venue_outcome_id)
);

CREATE TABLE IF NOT EXISTS market_aliases (
    alias_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_market_id uuid NOT NULL REFERENCES markets(market_id) ON DELETE CASCADE,
    target_market_id uuid NOT NULL REFERENCES markets(market_id) ON DELETE CASCADE,
    alias_type text NOT NULL DEFAULT 'manual_cross_venue',
    confidence numeric(5,4) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    rationale text NOT NULL,
    reviewed_by text,
    reviewed_at timestamptz,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (source_market_id <> target_market_id)
);

-- ---------------------------------------------------------------------------
-- Ingestion tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_events (
    raw_event_id bigserial,
    venue_code text NOT NULL REFERENCES venues(venue_code),
    source_channel text NOT NULL,
    source_event_type text NOT NULL,
    source_event_id text,
    market_id uuid REFERENCES markets(market_id),
    venue_market_id text,
    exchange_ts timestamptz,
    received_at timestamptz NOT NULL DEFAULT now(),
    parser_version text NOT NULL DEFAULT 'raw.v1',
    payload jsonb NOT NULL,
    payload_hash text,
    ingest_node text,
    PRIMARY KEY (raw_event_id, received_at)
) PARTITION BY RANGE (received_at);

CREATE TABLE IF NOT EXISTS event_dedupe_keys (
    dedupe_key text PRIMARY KEY,
    venue_code text NOT NULL REFERENCES venues(venue_code),
    source_channel text NOT NULL,
    first_raw_event_id bigint,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    duplicate_count bigint NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ingestion_connections (
    connection_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_code text NOT NULL REFERENCES venues(venue_code),
    source_channel text NOT NULL,
    status text NOT NULL DEFAULT 'starting',
    connected_at timestamptz,
    disconnected_at timestamptz,
    last_message_at timestamptz,
    reconnect_count integer NOT NULL DEFAULT 0,
    last_error text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS feed_cursors (
    cursor_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_code text NOT NULL REFERENCES venues(venue_code),
    feed_name text NOT NULL,
    market_id uuid REFERENCES markets(market_id),
    cursor_value text,
    cursor_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    last_success_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (venue_code, feed_name, market_id)
);

-- ---------------------------------------------------------------------------
-- Normalized data
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS normalized_trades (
    trade_id uuid DEFAULT gen_random_uuid(),
    raw_event_id bigint,
    raw_event_received_at timestamptz,
    venue_code text NOT NULL REFERENCES venues(venue_code),
    venue_trade_id text,
    market_id uuid NOT NULL REFERENCES markets(market_id),
    outcome_id uuid REFERENCES market_outcomes(outcome_id),
    outcome_key text NOT NULL,
    aggressor_side text CHECK (aggressor_side IN ('buy', 'sell', 'unknown')) DEFAULT 'unknown',
    directional_side text CHECK (directional_side IN ('yes', 'no', 'unknown')) DEFAULT 'unknown',
    side_confidence text CHECK (side_confidence IN ('high', 'medium', 'low', 'unknown')) DEFAULT 'unknown',
    price numeric(12,8) NOT NULL CHECK (price >= 0 AND price <= 1),
    contracts numeric(28,8) NOT NULL CHECK (contracts >= 0),
    capital_at_risk_usd numeric(28,8),
    payout_notional_usd numeric(28,8),
    fee_usd numeric(28,8),
    exchange_ts timestamptz,
    received_at timestamptz NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now(),
    normalization_version text NOT NULL DEFAULT 'trade.v1',
    warnings text[] NOT NULL DEFAULT '{}',
    source_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (trade_id, received_at)
) PARTITION BY RANGE (received_at);

CREATE TABLE IF NOT EXISTS market_snapshots (
    snapshot_id uuid DEFAULT gen_random_uuid(),
    venue_code text NOT NULL REFERENCES venues(venue_code),
    market_id uuid NOT NULL REFERENCES markets(market_id),
    captured_at timestamptz NOT NULL DEFAULT now(),
    source text NOT NULL,
    last_price numeric(12,8),
    best_bid numeric(12,8),
    best_ask numeric(12,8),
    spread numeric(12,8),
    volume_24h_usd numeric(28,8),
    open_interest_contracts numeric(28,8),
    open_interest_usd numeric(28,8),
    status text,
    raw_event_id bigint,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (snapshot_id, captured_at)
) PARTITION BY RANGE (captured_at);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    orderbook_snapshot_id uuid DEFAULT gen_random_uuid(),
    venue_code text NOT NULL REFERENCES venues(venue_code),
    market_id uuid NOT NULL REFERENCES markets(market_id),
    captured_at timestamptz NOT NULL DEFAULT now(),
    source text NOT NULL,
    sequence_no text,
    is_reconstructed boolean NOT NULL DEFAULT false,
    is_valid boolean NOT NULL DEFAULT true,
    invalidation_reason text,
    best_bid numeric(12,8),
    best_ask numeric(12,8),
    spread numeric(12,8),
    top_depth_usd numeric(28,8),
    raw_event_id bigint,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (orderbook_snapshot_id, captured_at)
) PARTITION BY RANGE (captured_at);

CREATE TABLE IF NOT EXISTS orderbook_levels (
    orderbook_snapshot_id uuid NOT NULL,
    captured_at timestamptz NOT NULL,
    market_id uuid NOT NULL REFERENCES markets(market_id),
    outcome_key text NOT NULL,
    side text NOT NULL CHECK (side IN ('bid', 'ask')),
    price numeric(12,8) NOT NULL CHECK (price >= 0 AND price <= 1),
    contracts numeric(28,8) NOT NULL CHECK (contracts >= 0),
    level_index integer NOT NULL,
    PRIMARY KEY (orderbook_snapshot_id, captured_at, outcome_key, side, level_index)
);

-- ---------------------------------------------------------------------------
-- Metrics and baselines
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS metric_windows (
    metric_window_id uuid DEFAULT gen_random_uuid(),
    market_id uuid NOT NULL REFERENCES markets(market_id),
    venue_code text NOT NULL REFERENCES venues(venue_code),
    outcome_key text,
    window_start timestamptz NOT NULL,
    window_seconds integer NOT NULL,
    trade_count integer NOT NULL DEFAULT 0,
    gross_capital_at_risk_usd numeric(28,8) NOT NULL DEFAULT 0,
    payout_notional_usd numeric(28,8) NOT NULL DEFAULT 0,
    net_yes_flow_usd numeric(28,8),
    net_no_flow_usd numeric(28,8),
    price_open numeric(12,8),
    price_close numeric(12,8),
    price_change numeric(12,8),
    max_trade_capital_at_risk_usd numeric(28,8),
    sample_size integer NOT NULL DEFAULT 0,
    data_quality text NOT NULL DEFAULT 'unknown',
    metric_version text NOT NULL DEFAULT 'metrics.v1',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (metric_window_id, window_start)
) PARTITION BY RANGE (window_start);

CREATE TABLE IF NOT EXISTS market_baselines (
    baseline_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    market_id uuid REFERENCES markets(market_id),
    venue_code text REFERENCES venues(venue_code),
    category text,
    scope text NOT NULL CHECK (scope IN ('market', 'category', 'venue', 'global')),
    lookback_seconds integer NOT NULL,
    computed_at timestamptz NOT NULL DEFAULT now(),
    sample_size integer NOT NULL DEFAULT 0,
    p50_trade_usd numeric(28,8),
    p95_trade_usd numeric(28,8),
    p99_trade_usd numeric(28,8),
    p995_trade_usd numeric(28,8),
    median_5m_flow_usd numeric(28,8),
    p99_5m_flow_usd numeric(28,8),
    baseline_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    baseline_version text NOT NULL DEFAULT 'baseline.v1'
);

-- ---------------------------------------------------------------------------
-- Alerts
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS alert_rules (
    rule_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_key text NOT NULL,
    rule_version text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    severity_default text NOT NULL DEFAULT 'medium',
    config jsonb NOT NULL,
    description text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (rule_key, rule_version)
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dedupe_key text NOT NULL UNIQUE,
    rule_key text NOT NULL,
    rule_version text NOT NULL,
    venue_code text NOT NULL REFERENCES venues(venue_code),
    market_id uuid REFERENCES markets(market_id),
    outcome_key text,
    severity text NOT NULL CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical')),
    confidence text NOT NULL CHECK (confidence IN ('low', 'medium', 'high', 'unknown')) DEFAULT 'unknown',
    score numeric(8,6),
    title text NOT NULL,
    summary text NOT NULL,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    data_quality text NOT NULL DEFAULT 'unknown',
    -- Informational lineage pointers only. No FK is declared because raw_events
    -- and normalized_trades are range-partitioned by timestamp; retention can
    -- intentionally remove old partitions after operator opt-in.
    raw_event_id bigint,
    trade_id uuid,
    status text NOT NULL DEFAULT 'new',
    fired_at timestamptz NOT NULL DEFAULT now(),
    acknowledged_at timestamptz,
    resolved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alert_deliveries (
    delivery_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id uuid NOT NULL REFERENCES alerts(alert_id) ON DELETE CASCADE,
    channel text NOT NULL,
    destination text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    attempt_count integer NOT NULL DEFAULT 0,
    last_attempt_at timestamptz,
    delivered_at timestamptz,
    last_error text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alert_reviews (
    review_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    alert_id uuid NOT NULL REFERENCES alerts(alert_id) ON DELETE CASCADE,
    label text NOT NULL,
    false_positive_category text,
    notes text,
    reviewed_by text,
    reviewed_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Ops tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS job_queue (
    job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    priority integer NOT NULL DEFAULT 0,
    status text NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 5,
    run_after timestamptz NOT NULL DEFAULT now(),
    locked_by text,
    locked_at timestamptz,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dead_letters (
    dead_letter_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_code text REFERENCES venues(venue_code),
    raw_event_id bigint,
    source_channel text,
    failure_stage text NOT NULL,
    error_class text,
    error_message text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    resolved boolean NOT NULL DEFAULT false,
    resolved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS data_quality_incidents (
    incident_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    venue_code text REFERENCES venues(venue_code),
    market_id uuid REFERENCES markets(market_id),
    incident_type text NOT NULL,
    severity text NOT NULL CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical')) DEFAULT 'medium',
    status text NOT NULL DEFAULT 'open',
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    summary text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS system_heartbeats (
    heartbeat_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    worker_name text NOT NULL,
    worker_type text NOT NULL,
    status text NOT NULL DEFAULT 'healthy',
    last_heartbeat_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (worker_name)
);
