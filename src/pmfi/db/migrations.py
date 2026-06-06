from __future__ import annotations
from datetime import datetime, timezone
import asyncpg

PARTITIONED_TABLES = [
    "raw_events",
    "normalized_trades",
    "metric_windows",
    "market_snapshots",
    "orderbook_snapshots",
]

async def ensure_current_partitions(pool: asyncpg.Pool) -> None:
    now = datetime.now(timezone.utc)
    year, month = now.year, now.month
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    start = f"{year}-{month:02d}-01"
    end = f"{next_year}-{next_month:02d}-01"
    async with pool.acquire() as conn:
        for table in PARTITIONED_TABLES:
            part = f"{table}_{year}_{month:02d}"
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {part}
                PARTITION OF {table}
                FOR VALUES FROM ('{start}') TO ('{end}')
            """)

async def verify_connection(pool: asyncpg.Pool) -> bool:
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT count(*) AS n FROM venues")
            return row is not None
    except Exception:
        return False
