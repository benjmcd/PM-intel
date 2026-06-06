-- Add watched flag to markets table for watch-list management.
-- Safe to run multiple times (IF NOT EXISTS / DO NOTHING patterns).

ALTER TABLE markets ADD COLUMN IF NOT EXISTS watched boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_markets_watched ON markets (watched) WHERE watched = true;
