SET search_path TO pmfi, public;

ALTER TABLE orderbook_snapshots
  ADD COLUMN IF NOT EXISTS outcome_key text NOT NULL DEFAULT 'unknown';
