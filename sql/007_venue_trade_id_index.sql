-- Index for venue_trade_id dedup lookups on normalized_trades.
-- A unique constraint is not feasible on a partitioned table without including
-- the partition key (received_at). This index enables application-level dedup
-- in process_event when venue_trade_id is present.

SET search_path TO pmfi, public;

CREATE INDEX IF NOT EXISTS idx_normalized_trades_venue_trade_id
    ON normalized_trades (venue_code, venue_trade_id)
    WHERE venue_trade_id IS NOT NULL;
