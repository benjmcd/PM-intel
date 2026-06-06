from __future__ import annotations
import asyncpg

async def upsert_market(
    conn: asyncpg.Connection,
    *,
    venue_code: str,
    venue_market_id: str,
    title: str = "unknown",
    status: str = "unknown",
) -> str:
    row = await conn.fetchrow(
        """INSERT INTO markets (venue_code, venue_market_id, title, status)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (venue_code, venue_market_id) DO UPDATE
             SET last_seen_at=now(), status=EXCLUDED.status
           RETURNING market_id::text""",
        venue_code, venue_market_id, title, status,
    )
    return str(row["market_id"])

async def get_market_id(conn: asyncpg.Connection, venue_code: str, venue_market_id: str) -> str | None:
    row = await conn.fetchrow(
        "SELECT market_id::text FROM markets WHERE venue_code=$1 AND venue_market_id=$2",
        venue_code, venue_market_id,
    )
    return str(row["market_id"]) if row else None
