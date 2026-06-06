-- Migration 006: add unique constraint to metric_windows and deduplicate existing rows.
-- Idempotent: safe to run multiple times.

DO $$
BEGIN
  -- Deduplicate any existing rows before adding the constraint.
  -- Keeps the earliest row per window slot and aggregates metrics into it.
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'metric_windows_window_unique'
      AND conrelid = 'pmfi.metric_windows'::regclass
  ) THEN
    DELETE FROM pmfi.metric_windows
    WHERE metric_window_id IN (
      SELECT metric_window_id FROM (
        SELECT metric_window_id, window_start,
          ROW_NUMBER() OVER (
            PARTITION BY market_id, COALESCE(outcome_key, ''), window_start, window_seconds
            ORDER BY metric_window_id
          ) AS rn
        FROM pmfi.metric_windows
      ) sub WHERE rn > 1
    );

    ALTER TABLE pmfi.metric_windows
      ADD CONSTRAINT metric_windows_window_unique
      UNIQUE (market_id, outcome_key, window_start, window_seconds);
  END IF;
END;
$$;
