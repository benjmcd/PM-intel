from __future__ import annotations
import hashlib
import logging
from datetime import datetime, timedelta, timezone
import asyncpg

_log = logging.getLogger(__name__)

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


async def _record_migration(conn: asyncpg.Connection, name: str, body: str) -> None:
    """Record a migration in schema_migrations (idempotent; never blocks re-run of DDL)."""
    checksum = hashlib.sha256(body.encode()).hexdigest()
    await conn.execute(
        """
        INSERT INTO schema_migrations (migration_name, checksum)
        VALUES ($1, $2)
        ON CONFLICT DO NOTHING
        """,
        name,
        checksum,
    )


async def apply_schema_migrations(pool: asyncpg.Pool) -> None:
    """Apply incremental schema changes that may be missing on existing DBs."""
    async with pool.acquire() as conn:
        # Ensure the migration ledger exists before recording anything.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_name text PRIMARY KEY,
                checksum       text NOT NULL,
                applied_at     timestamptz NOT NULL DEFAULT now()
            )
            """
        )

        # Migration 005: watched flag on markets.
        _m005 = (
            "ALTER TABLE markets ADD COLUMN IF NOT EXISTS watched boolean NOT NULL DEFAULT false"
        )
        await conn.execute(_m005)
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_markets_watched ON markets (watched) WHERE watched = true"
        )
        await _record_migration(conn, "005_add_watched_flag.sql", _m005)

        # Migration 007: index for venue_trade_id dedup lookups on normalized_trades.
        # A unique constraint is not feasible on a partitioned table without the partition key.
        _m007 = (
            "CREATE INDEX IF NOT EXISTS idx_normalized_trades_venue_trade_id "
            "ON normalized_trades (venue_code, venue_trade_id) "
            "WHERE venue_trade_id IS NOT NULL"
        )
        await conn.execute(_m007)
        await _record_migration(conn, "007_venue_trade_id_index.sql", _m007)

        # Migration 006: unique constraint on metric_windows for proper upsert accumulation.
        # Deduplicates first, then adds constraint idempotently.
        _m006 = """
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
        await conn.execute(_m006)
        await _record_migration(conn, "006_metric_windows_unique_constraint.sql", _m006)

        # Migration 011: index for metric_windows range scans by market_id + window_start.
        # metric_windows is partitioned; Postgres propagates a parent index to all partitions.
        _m011 = (
            "CREATE INDEX IF NOT EXISTS idx_metric_windows_market_window "
            "ON metric_windows (market_id, window_start DESC)"
        )
        await conn.execute(_m011)
        await _record_migration(conn, "011_metric_windows_index.sql", _m011)

        # Migration 010: unique constraint on market_baselines to prevent duplicate rows.
        # Keeps the most recent row per (market_id, venue_code, scope), then adds constraint.
        # Scope note: covers the only scope written today ('market', non-null keys). Non-market
        # scopes carry NULL keys (distinct under UNIQUE) — revisit with a COALESCE index if added.
        _m010 = """
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
        await conn.execute(_m010)
        await _record_migration(conn, "010_market_baselines_unique.sql", _m010)

        # Migration 012: schema_migrations ledger table (self-referential record).
        _m012 = (
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "migration_name text PRIMARY KEY, "
            "checksum text NOT NULL, "
            "applied_at timestamptz NOT NULL DEFAULT now()"
            ")"
        )
        await _record_migration(conn, "012_schema_migrations.sql", _m012)

        # Migration 013: persist outcome identity on orderbook snapshot summaries.
        # Existing rows cannot be safely backfilled, so they remain explicit unknowns.
        _m013 = (
            "ALTER TABLE orderbook_snapshots "
            "ADD COLUMN IF NOT EXISTS outcome_key text NOT NULL DEFAULT 'unknown'"
        )
        await conn.execute(_m013)
        await _record_migration(conn, "013_orderbook_snapshot_outcome_key.sql", _m013)


async def _check_current_partition_exists(pool: asyncpg.Pool) -> bool:
    """Return True when the current-month partition exists for every partitioned table.

    Logs ERROR for each missing partition so operators can act before ingest begins.
    Returning False signals that the caller should block/abort ingest rather than
    silently writing to the DEFAULT partition (or raising an unrouted-row error).
    """
    now = datetime.now(timezone.utc)
    part_suffix = f"{now.year}_{now.month:02d}"
    all_present = True
    async with pool.acquire() as conn:
        for table in PARTITIONED_TABLES:
            part_name = f"{table}_{part_suffix}"
            exists = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM pg_tables
                    WHERE schemaname = current_schema()
                      AND tablename = $1
                )
                """,
                part_name,
            )
            if not exists:
                _log.error(
                    "Current-month partition %r is missing for table %r. "
                    "Ingest must not proceed until partitions are created via "
                    "ensure_current_partitions(). Run 'python scripts/db_local.py init' "
                    "or call startup_maintenance() before starting ingest.",
                    part_name,
                    table,
                )
                all_present = False
    return all_present


async def startup_maintenance(pool: asyncpg.Pool) -> bool:
    """Ensure partitions exist for the current month and next 3. Non-fatal on failure."""
    try:
        await apply_schema_migrations(pool)
        await ensure_current_partitions(pool, months_ahead=3)
        # After creating partitions, verify current month is present.
        if not await _check_current_partition_exists(pool):
            _log.error(
                "One or more current-month partitions are still missing after "
                "ensure_current_partitions(). DB may be in a degraded state."
            )
        else:
            _log.debug("Partition maintenance complete (current + 3 months ahead)")
        return True
    except Exception as exc:
        _log.warning("Partition maintenance failed (non-fatal): %s", exc)
        return False


async def verify_connection(pool: asyncpg.Pool) -> bool:
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT count(*) AS n FROM venues")
            return row is not None
    except Exception:
        return False
