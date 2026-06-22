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


async def ensure_current_partitions(
    pool: asyncpg.Pool,
    *,
    months_ahead: int = 3,
    now: datetime | None = None,
) -> None:
    """Create partitions for the current month and the next `months_ahead` months."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
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
        # Migration 012: venue-relative volume cache column + indexes.
        # volume = USD notional for Polymarket, contract count for Kalshi.
        # raw_metadata remains source of truth; populated on next 'pmfi markets discover'.
        await conn.execute(
            "ALTER TABLE markets ADD COLUMN IF NOT EXISTS volume numeric(20,2)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_markets_volume "
            "ON markets (volume DESC NULLS LAST) WHERE volume IS NOT NULL"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_markets_venue_volume "
            "ON markets (venue_code, volume DESC NULLS LAST) WHERE volume IS NOT NULL"
        )
        # Migration 013: partition-safe guard for normalized trade dedupe.
        # normalized_trades cannot enforce this identity directly without
        # including the partition key, so claims live in a small unpartitioned table.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS normalized_trade_dedupe_keys (
                dedupe_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                venue_code text NOT NULL REFERENCES venues(venue_code),
                venue_trade_id text,
                market_id uuid NOT NULL REFERENCES markets(market_id) ON DELETE CASCADE,
                exchange_ts timestamptz,
                exchange_ts_key timestamptz NOT NULL,
                price numeric(12,8) NOT NULL,
                contracts numeric(28,8) NOT NULL,
                outcome_key text NOT NULL,
                trade_id uuid,
                first_seen_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_normalized_trade_dedupe_venue_id
                ON normalized_trade_dedupe_keys (venue_code, venue_trade_id)
                WHERE venue_trade_id IS NOT NULL
            """
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_normalized_trade_dedupe_fingerprint
                ON normalized_trade_dedupe_keys
                    (venue_code, market_id, exchange_ts_key, price, contracts, outcome_key)
                WHERE venue_trade_id IS NULL
            """
        )
        await conn.execute(
            """
            INSERT INTO normalized_trade_dedupe_keys
                (venue_code, venue_trade_id, market_id, exchange_ts, exchange_ts_key,
                 price, contracts, outcome_key, trade_id, first_seen_at)
            SELECT DISTINCT ON (venue_code, venue_trade_id)
                venue_code,
                venue_trade_id,
                market_id,
                exchange_ts,
                COALESCE(exchange_ts, '-infinity'::timestamptz),
                price,
                contracts,
                outcome_key,
                trade_id,
                COALESCE(processed_at, received_at, now())
            FROM normalized_trades
            WHERE venue_trade_id IS NOT NULL
            ORDER BY venue_code, venue_trade_id, received_at, trade_id
            ON CONFLICT DO NOTHING
            """
        )
        await conn.execute(
            """
            INSERT INTO normalized_trade_dedupe_keys
                (venue_code, venue_trade_id, market_id, exchange_ts, exchange_ts_key,
                 price, contracts, outcome_key, trade_id, first_seen_at)
            SELECT DISTINCT ON (
                venue_code,
                market_id,
                COALESCE(exchange_ts, '-infinity'::timestamptz),
                price,
                contracts,
                outcome_key
            )
                venue_code,
                NULL,
                market_id,
                exchange_ts,
                COALESCE(exchange_ts, '-infinity'::timestamptz),
                price,
                contracts,
                outcome_key,
                trade_id,
                COALESCE(processed_at, received_at, now())
            FROM normalized_trades
            WHERE venue_trade_id IS NULL
            ORDER BY
                venue_code,
                market_id,
                COALESCE(exchange_ts, '-infinity'::timestamptz),
                price,
                contracts,
                outcome_key,
                received_at,
                trade_id
            ON CONFLICT DO NOTHING
            """
        )
        # Migration 014: linked dead-letter duplicate guard. The runner also
        # serializes duplicate recovery, but this keeps the write path idempotent.
        await conn.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'pmfi'
                  AND c.relname = 'idx_dead_letters_raw_stage_class_dedupe'
              ) THEN
                WITH duplicate_rows AS (
                  SELECT
                    dead_letter_id,
                    ROW_NUMBER() OVER (
                      PARTITION BY raw_event_id, failure_stage, error_class
                      ORDER BY created_at, dead_letter_id
                    ) AS rn
                  FROM dead_letters
                  WHERE raw_event_id IS NOT NULL
                )
                UPDATE dead_letters dl
                SET
                  error_class = CONCAT(
                    COALESCE(dl.error_class, 'unknown_error'),
                    ':dedupe_preserved:',
                    LEFT(dl.dead_letter_id::text, 8)
                  ),
                  error_message = CONCAT(
                    dl.error_message,
                    ' [duplicate row preserved before idx_dead_letters_raw_stage_class_dedupe]'
                  )
                FROM duplicate_rows
                WHERE dl.dead_letter_id = duplicate_rows.dead_letter_id
                  AND duplicate_rows.rn > 1;
              END IF;
            END;
            $$
            """
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_dead_letters_raw_stage_class_dedupe
                ON dead_letters (raw_event_id, failure_stage, error_class)
                WHERE raw_event_id IS NOT NULL
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
