from __future__ import annotations
from datetime import datetime
from typing import Any
import asyncpg


async def upsert_market(
    conn: asyncpg.Connection,
    *,
    venue_code: str,
    venue_market_id: str,
    title: str = "unknown",
    status: str = "unknown",
) -> str:
    """Minimal upsert used by the live pipeline runner."""
    row = await conn.fetchrow(
        """INSERT INTO markets (venue_code, venue_market_id, title, status)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (venue_code, venue_market_id) DO UPDATE
             SET last_seen_at=now(), status=EXCLUDED.status
           RETURNING market_id::text""",
        venue_code, venue_market_id, title, status,
    )
    return str(row["market_id"])


async def upsert_market_full(
    conn: asyncpg.Connection,
    *,
    venue_code: str,
    venue_market_id: str,
    title: str = "unknown",
    status: str = "active",
    category: str | None = None,
    close_ts: datetime | None = None,
    raw_metadata: dict[str, Any] | None = None,
) -> str:
    """Full upsert used by market discovery, preserving watched flag."""
    import json as _json

    meta_json = _json.dumps(raw_metadata) if raw_metadata else None
    row = await conn.fetchrow(
        """INSERT INTO markets (venue_code, venue_market_id, title, status, category, close_ts, raw_metadata)
           VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
           ON CONFLICT (venue_code, venue_market_id) DO UPDATE
             SET title=EXCLUDED.title,
                 status=EXCLUDED.status,
                 category=COALESCE(EXCLUDED.category, markets.category),
                 close_ts=COALESCE(EXCLUDED.close_ts, markets.close_ts),
                 raw_metadata=COALESCE(EXCLUDED.raw_metadata, markets.raw_metadata),
                 last_seen_at=now()
           RETURNING market_id::text""",
        venue_code, venue_market_id, title, status, category, close_ts, meta_json,
    )
    return str(row["market_id"])


async def set_market_watched(
    conn: asyncpg.Connection,
    *,
    venue_code: str,
    venue_market_id: str,
    watched: bool,
) -> bool:
    """Set the watched flag on a market. Returns True if the market was found."""
    result = await conn.execute(
        """UPDATE markets SET watched=$1 WHERE venue_code=$2 AND venue_market_id=$3""",
        watched, venue_code, venue_market_id,
    )
    return result.endswith("1")


async def fetch_watched_markets(
    conn: asyncpg.Connection,
    *,
    venue_code: str | None = None,
) -> list[dict[str, Any]]:
    """Return all markets with watched=true, optionally filtered by venue."""
    if venue_code:
        rows = await conn.fetch(
            "SELECT market_id::text, venue_code, venue_market_id, title, category, status "
            "FROM markets WHERE watched=true AND venue_code=$1 ORDER BY venue_market_id",
            venue_code,
        )
    else:
        rows = await conn.fetch(
            "SELECT market_id::text, venue_code, venue_market_id, title, category, status "
            "FROM markets WHERE watched=true ORDER BY venue_code, venue_market_id",
        )
    return [dict(r) for r in rows]


async def fetch_all_markets(
    conn: asyncpg.Connection,
    *,
    venue_code: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return markets from DB (for listing, not just watched)."""
    if venue_code:
        rows = await conn.fetch(
            "SELECT market_id::text, venue_code, venue_market_id, title, category, status, watched, last_seen_at "
            "FROM markets WHERE venue_code=$1 ORDER BY last_seen_at DESC NULLS LAST LIMIT $2",
            venue_code, limit,
        )
    else:
        rows = await conn.fetch(
            "SELECT market_id::text, venue_code, venue_market_id, title, category, status, watched, last_seen_at "
            "FROM markets ORDER BY last_seen_at DESC NULLS LAST LIMIT $1",
            limit,
        )
    return [dict(r) for r in rows]


async def get_market_id(conn: asyncpg.Connection, venue_code: str, venue_market_id: str) -> str | None:
    row = await conn.fetchrow(
        "SELECT market_id::text FROM markets WHERE venue_code=$1 AND venue_market_id=$2",
        venue_code, venue_market_id,
    )
    return str(row["market_id"]) if row else None
