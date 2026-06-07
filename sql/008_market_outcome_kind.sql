SET search_path TO pmfi, public;
ALTER TABLE pmfi.market_outcomes ADD COLUMN IF NOT EXISTS is_binary boolean NOT NULL DEFAULT true;
-- backfill: existing rows are binary iff their key is yes/no
UPDATE pmfi.market_outcomes SET is_binary = (outcome_key IN ('yes','no')) WHERE true;
