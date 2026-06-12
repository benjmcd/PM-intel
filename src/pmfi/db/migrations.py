from __future__ import annotations
from datetime import datetime, timedelta, timezone
import asyncpg

PARTITIONED_TABLES = [
    "raw_events",
    "normalized_trades",
    "metric_windows",
    "market_snapshots",
    "orderbook_snapshots",
]


def _months_ahead(year: int, month: int, count: int) -> list[tuple[int, int]]:
    """Return list of (year, month) for the current month and the next `count` months."""
    result = []
    for i in range(count + 1):
        m = month + i
        y = year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        result.append((y, m))
    return result


async def ensure_current_partitions(pool: asyncpg.Pool, *, months_ahead: int = 3) -> None:
    """Create partitions for the current month and the next `months_ahead` months."""
    now = datetime.now(timezone.utc)
    months = _months_ahead(now.year, now.month, months_ahead)
    async with pool.acquire() as conn:
        for (year, month) in months:
            nm = month + 1 if month < 12 else 1
            ny = year if month < 12 else year + 1
            start = f"{year}-{month:02d}-01"
            end = f"{ny}-{nm:02d}-01"
            for table in PARTITIONED_TABLES:
                part = f"{table}_{year}_{month:02d}"
                await conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {part} "
                    f"PARTITION OF {table} "
                    f"FOR VALUES FROM ('{start}') TO ('{end}')"
                )


async def find_partitions_older_than(pool: asyncpg.Pool, *, before_days: int) -> list[str]:
    """Return names of monthly partitions whose period starts before *before_days* ago.

    Read-only — drops nothing. Mirrors drop_old_partitions name-parsing logic.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=before_days)
    cutoff_key = (cutoff.year, cutoff.month)
    found: list[str] = []

    async with pool.acquire() as conn:
        for table in PARTITIONED_TABLES:
            rows = await conn.fetch(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = current_schema()
                AND tablename LIKE $1
                """,
                f"{table}_%",
            )
            for row in rows:
                name: str = row["tablename"]
                parts = name.split("_")
                if len(parts) < 2:
                    continue
                try:
                    part_year = int(parts[-2])
                    part_month = int(parts[-1])
                except ValueError:
                    continue
                if (part_year, part_month) < cutoff_key:
                    found.append(name)

    return found


async def drop_old_partitions(pool: asyncpg.Pool, *, before_days: int = 90) -> list[str]:
    """Drop monthly partitions older than `before_days`. Returns names of dropped tables."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=before_days)
    cutoff_key = (cutoff.year, cutoff.month)
    dropped: list[str] = []

    async with pool.acquire() as conn:
        for table in PARTITIONED_TABLES:
            rows = await conn.fetch(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = current_schema()
                AND tablename LIKE $1
                """,
                f"{table}_%",
            )
            for row in rows:
                name: str = row["tablename"]
                parts = name.split("_")
                if len(parts) < 2:
                    continue
                try:
                    part_year = int(parts[-2])
                    part_month = int(parts[-1])
                except ValueError:
                    continue
                if (part_year, part_month) < cutoff_key:
                    await conn.execute(f"DROP TABLE IF EXISTS {name}")
                    dropped.append(name)

    return dropped


async def apply_schema_migrations(pool: asyncpg.Pool) -> None:
    """Apply incremental schema changes that may be missing on existing DBs."""
    async with pool.acquire() as conn:
        # Migration 005: watched flag on markets
        await conn.execute(
            "ALTER TABLE markets ADD COLUMN IF NOT EXISTS watched boolean NOT NULL DEFAULT false"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_markets_watched ON markets (watched) WHERE watched = true"
        )
        # Migration 007: index for venue_trade_id dedup lookups on normalized_trades.
        # A unique constraint is not feasible on a partitioned table without the partition key.
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_normalized_trades_venue_trade_id "
            "ON normalized_trades (venue_code, venue_trade_id) "
            "WHERE venue_trade_id IS NOT NULL"
        )
        # Migration 006: unique constraint on metric_windows for proper upsert accumulation.
        # Deduplicates first, then adds constraint idempotently.
        await conn.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'metric_windows_window_unique'
                  AND conrelid = 'metric_windows'::regclass
              ) THEN
                DELETE FROM metric_windows
                WHERE metric_window_id IN (
                  SELECT metric_window_id FROM (
                    SELECT metric_window_id, window_start,
                      ROW_NUMBER() OVER (
                        PARTITION BY market_id, COALESCE(outcome_key, ''), window_start, window_seconds
                        ORDER BY metric_window_id
                      ) AS rn
                    FROM metric_windows
                  ) sub WHERE rn > 1
                );
                ALTER TABLE metric_windows
                  ADD CONSTRAINT metric_windows_window_unique
                  UNIQUE (market_id, outcome_key, window_start, window_seconds);
              END IF;
            END;
            $$
            """
        )
        # Migration 011: index for metric_windows range scans by market_id + window_start.
        # metric_windows is partitioned; Postgres propagates a parent index to all partitions.
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_metric_windows_market_window "
            "ON metric_windows (market_id, window_start DESC)"
        )
        # Migration 008: is_binary column on market_outcomes (binary vs multi-outcome markets).
        await conn.execute(
            "ALTER TABLE market_outcomes ADD COLUMN IF NOT EXISTS is_binary boolean NOT NULL DEFAULT true"
        )
        # Migration 009: raw/normalized lineage columns on alerts (informational, no FK).
        await conn.execute(
            "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS raw_event_id bigint"
        )
        await conn.execute(
            "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS trade_id uuid"
        )
        # Migration 010: unique constraint on market_baselines to prevent duplicate rows.
        # Keeps the most recent row per (market_id, venue_code, scope), then adds constraint.
        # Scope note: covers the only scope written today ('market', non-null keys). Non-market
        # scopes carry NULL keys (distinct under UNIQUE) — revisit with a COALESCE index if added.
        await conn.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'market_baselines_scope_unique'
                  AND conrelid = 'market_baselines'::regclass
              ) THEN
                DELETE FROM market_baselines
                WHERE baseline_id IN (
                  SELECT baseline_id FROM (
                    SELECT baseline_id,
                      ROW_NUMBER() OVER (
                        PARTITION BY market_id, venue_code, scope
                        ORDER BY computed_at DESC, baseline_id DESC
                      ) AS rn
                    FROM market_baselines
                  ) sub WHERE rn > 1
                );
                ALTER TABLE market_baselines
                  ADD CONSTRAINT market_baselines_scope_unique
                  UNIQUE (market_id, venue_code, scope);
              END IF;
            END;
            $$
            """
        )


async def startup_maintenance(pool: asyncpg.Pool) -> bool:
    """Ensure partitions exist for the current month and next 3. Non-fatal on failure."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        await apply_schema_migrations(pool)
        await ensure_current_partitions(pool, months_ahead=3)
        logger.debug("Partition maintenance complete (current + 3 months ahead)")
        return True
    except Exception as exc:
        logger.warning("Partition maintenance failed (non-fatal): %s", exc)
        return False


async def verify_connection(pool: asyncpg.Pool) -> bool:
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT count(*) AS n FROM venues")
            return row is not None
    except Exception:
        return False
