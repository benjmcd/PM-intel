"""Repo layer for market_aliases (operator-curated cross-venue matches).

Cross-venue matching is manual/reviewed only — see docs/MANUAL_CROSS_VENUE_MATCHING.md.
The cross_venue_divergence_v1 monitor reads active aliases from this table.
"""
from __future__ import annotations

from decimal import Decimal

import asyncpg


async def _resolve_market_id(conn, venue_code: str, venue_market_id: str):
    return await conn.fetchval(
        "SELECT market_id::text FROM markets WHERE venue_code = $1 AND venue_market_id = $2",
        venue_code, venue_market_id,
    )


async def link_markets(
    conn: asyncpg.Connection,
    *,
    source_venue: str,
    source_market: str,
    target_venue: str,
    target_market: str,
    confidence,
    rationale: str,
    reviewed_by: str | None = None,
) -> str:
    """Create an active manual cross-venue alias. Returns alias_id; raises ValueError on bad input."""
    src_id = await _resolve_market_id(conn, source_venue, source_market)
    if src_id is None:
        raise ValueError(
            f"source market not found: {source_venue}:{source_market} (run 'pmfi markets discover' first)"
        )
    tgt_id = await _resolve_market_id(conn, target_venue, target_market)
    if tgt_id is None:
        raise ValueError(
            f"target market not found: {target_venue}:{target_market} (run 'pmfi markets discover' first)"
        )
    if src_id == tgt_id:
        raise ValueError("source and target markets must differ")
    conf = Decimal(str(confidence))
    if conf < 0 or conf > 1:
        raise ValueError(f"confidence must be in [0, 1], got {confidence!r}")
    row = await conn.fetchrow(
        """INSERT INTO market_aliases
               (source_market_id, target_market_id, alias_type, confidence, rationale, reviewed_by, reviewed_at)
           VALUES ($1::uuid, $2::uuid, 'manual_cross_venue', $3, $4, $5, now())
           RETURNING alias_id::text""",
        src_id, tgt_id, conf, rationale, reviewed_by,
    )
    return row["alias_id"]


async def list_aliases(conn: asyncpg.Connection, *, limit: int = 50) -> list[dict]:
    rows = await conn.fetch(
        """SELECT a.alias_id::text AS alias_id, a.confidence, a.is_active, a.rationale,
                  a.reviewed_by, a.created_at,
                  sm.venue_code AS source_venue, COALESCE(sm.title, sm.venue_market_id) AS source_title,
                  tm.venue_code AS target_venue, COALESCE(tm.title, tm.venue_market_id) AS target_title
             FROM market_aliases a
             JOIN markets sm ON sm.market_id = a.source_market_id
             JOIN markets tm ON tm.market_id = a.target_market_id
            ORDER BY a.created_at DESC
            LIMIT $1""",
        limit,
    )
    return [dict(r) for r in rows]
