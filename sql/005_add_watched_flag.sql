-- Add watched flag to markets table for watch-list management.
-- Safe to run multiple times (IF NOT EXISTS / DO NOTHING patterns).
-- Self-contained: does not depend on a search_path inherited from a prior
-- migration session (db_local.py applies each file in a separate psql session).
SET search_path TO pmfi, public;

ALTER TABLE markets ADD COLUMN IF NOT EXISTS watched boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_markets_watched ON markets (watched) WHERE watched = true;
