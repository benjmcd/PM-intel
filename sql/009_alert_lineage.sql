SET search_path TO pmfi, public;
-- Add raw/normalized lineage columns to alerts.
-- No FK: raw_events and normalized_trades are partitioned with composite PKs.
-- Retention ordering: alerts may outlive dropped raw/trade partitions after
-- explicit operator opt-in; dangling references must be reported by the
-- lineage integrity check, not auto-deleted.
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS raw_event_id bigint;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS trade_id uuid;
COMMENT ON COLUMN alerts.raw_event_id IS
    'Informational raw_events pointer; retention may remove the referenced partition. Check with pmfi alerts lineage-check.';
COMMENT ON COLUMN alerts.trade_id IS
    'Informational normalized_trades pointer; retention may remove the referenced partition. Check with pmfi alerts lineage-check.';
