-- volume is a venue-relative denormalized cache of the fetched volume:
--   Polymarket = USD notional (from volumeNum), Kalshi = contract count (from volume field).
-- raw_metadata remains source of truth; populated on next 'pmfi markets discover', no backfill.

SET search_path TO pmfi, public;

ALTER TABLE markets ADD COLUMN IF NOT EXISTS volume numeric(20,2);

CREATE INDEX IF NOT EXISTS idx_markets_volume
    ON markets (volume DESC NULLS LAST) WHERE volume IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_markets_venue_volume
    ON markets (venue_code, volume DESC NULLS LAST) WHERE volume IS NOT NULL;
