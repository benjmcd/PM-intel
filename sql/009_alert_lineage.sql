SET search_path TO pmfi, public;
-- Add raw/normalized lineage columns to alerts.
-- No FK: raw_events is partitioned with composite PK; these are informational reference columns.
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS raw_event_id bigint;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS trade_id uuid;
