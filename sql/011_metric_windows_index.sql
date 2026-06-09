-- Index for metric_windows range scans by market_id + window_start.
-- metric_windows is partitioned by RANGE(window_start); an index on the
-- parent table is propagated to all existing and future partitions by Postgres.

SET search_path TO pmfi, public;

CREATE INDEX IF NOT EXISTS idx_metric_windows_market_window
    ON metric_windows (market_id, window_start DESC);
