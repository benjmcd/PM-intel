SET search_path TO pmfi, public;
-- Migration 010: add unique constraint to market_baselines and deduplicate existing rows.
-- Idempotent: safe to run multiple times.
-- SCOPE NOTE: this enforces uniqueness for the only scope currently written, 'market'
-- (market_id/venue_code are non-null there). The other scopes in the CHECK
-- (category/venue/global) carry NULL market_id/venue_code, and NULLs are distinct in a
-- UNIQUE index — so if/when those baselines are computed, replace this with a
-- COALESCE-based expression index (and matching ON CONFLICT target) so they dedupe too.

DO $$
BEGIN
  -- Deduplicate any existing rows before adding the constraint.
  -- Keeps the most recent row per (market_id, venue_code, scope).
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'market_baselines_scope_unique'
      AND conrelid = 'pmfi.market_baselines'::regclass
  ) THEN
    DELETE FROM pmfi.market_baselines
    WHERE baseline_id IN (
      SELECT baseline_id FROM (
        SELECT baseline_id,
          ROW_NUMBER() OVER (
            PARTITION BY market_id, venue_code, scope
            ORDER BY computed_at DESC, baseline_id DESC
          ) AS rn
        FROM pmfi.market_baselines
      ) sub WHERE rn > 1
    );

    ALTER TABLE pmfi.market_baselines
      ADD CONSTRAINT market_baselines_scope_unique
      UNIQUE (market_id, venue_code, scope);
  END IF;
END;
$$;
