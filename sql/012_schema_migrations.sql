-- Migration ledger: records each applied migration name and checksum.
-- Idempotent — safe to re-run on any database state.

SET search_path TO pmfi, public;

CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_name text PRIMARY KEY,
    checksum       text NOT NULL,
    applied_at     timestamptz NOT NULL DEFAULT now()
);
