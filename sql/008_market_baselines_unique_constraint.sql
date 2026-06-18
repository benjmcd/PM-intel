-- Migration 008: make market baseline upserts idempotent.
-- Keeps the newest baseline per market/scope before adding the constraint.

SET search_path TO pmfi, public;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'market_baselines_market_scope_unique'
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
      ADD CONSTRAINT market_baselines_market_scope_unique
      UNIQUE (market_id, venue_code, scope);
  END IF;
END;
$$;
