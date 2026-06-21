from __future__ import annotations
from datetime import datetime
from typing import Any
import asyncpg


async def upsert_market(
    conn: asyncpg.Connection,
    *,
    venue_code: str,
    venue_market_id: str,
    title: str | None = None,
    status: str = "unknown",
) -> str:
    """Minimal upsert used by the live pipeline runner.

    title=None (the pipeline default) means: keep whatever title discovery already
    stored.  A non-null title is only used for the INSERT fallback so rows always
    have a non-empty value; it is never allowed to overwrite an existing non-null
    title (COALESCE preserves the stored value).
    """
    effective_title = title if title is not None else venue_market_id
    row = await conn.fetchrow(
        """INSERT INTO markets (venue_code, venue_market_id, title, status)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (venue_code, venue_market_id) DO UPDATE
             SET last_seen_at=now(),
                 status=EXCLUDED.status,
                 title=COALESCE(NULLIF(markets.title, ''), EXCLUDED.title)
           RETURNING market_id::text""",
        venue_code, venue_market_id, effective_title, status,
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
    volume: float | None = None,
) -> str:
    """Full upsert used by market discovery, preserving watched flag.

    volume is a venue-relative denormalized cache (Polymarket=USD notional,
    Kalshi=contract count). COALESCE means it only overwrites when non-None.
    raw_metadata distinguishes not supplied (`None`, keep existing) from
    supplied empty (`{}`, intentionally overwrite with empty metadata).
    """
    import json as _json

    raw_metadata_supplied = raw_metadata is not None
    meta_json = _json.dumps(raw_metadata if raw_metadata_supplied else {})
    row = await conn.fetchrow(
        """INSERT INTO markets (venue_code, venue_market_id, title, status, category, close_ts, raw_metadata, volume)
           VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
           ON CONFLICT (venue_code, venue_market_id) DO UPDATE
             SET title=CASE
                   WHEN EXCLUDED.title IS NULL
                     OR EXCLUDED.title = ''
                     OR EXCLUDED.title = 'unknown'
                     OR EXCLUDED.title = markets.venue_market_id
                   THEN COALESCE(NULLIF(markets.title, ''), EXCLUDED.title, markets.venue_market_id)
                   ELSE EXCLUDED.title
                 END,
                 status=EXCLUDED.status,
                 category=COALESCE(EXCLUDED.category, markets.category),
                 close_ts=COALESCE(EXCLUDED.close_ts, markets.close_ts),
                 raw_metadata=CASE
                   WHEN $9 THEN EXCLUDED.raw_metadata
                   ELSE markets.raw_metadata
                 END,
                 volume=COALESCE(EXCLUDED.volume, markets.volume),
                 last_seen_at=now()
           RETURNING market_id::text""",
        venue_code, venue_market_id, title, status, category, close_ts, meta_json,
        volume, raw_metadata_supplied,
    )
    return str(row["market_id"])


_SORT_CLAUSES: dict[str, str] = {
    "volume": "volume DESC NULLS LAST",
    "trades": "trade_count DESC",
    "last-trade": "last_trade_at DESC NULLS LAST",
}


async def fetch_markets_ranked(
    conn: asyncpg.Connection,
    *,
    venue_code: str | None = None,
    watched: bool | None = None,
    search: str | None = None,
    min_volume: float | None = None,
    sort: str = "volume",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return markets ranked by the given sort key with optional filters.

    Returns rows with: venue_code, venue_market_id, title, status, watched,
    volume, trade_count, last_trade_at. sort is whitelisted (injection guard).
    """
    order_clause = _SORT_CLAUSES.get(sort, _SORT_CLAUSES["volume"])

    conditions: list[str] = []
    params: list[Any] = []
    idx = 1

    if venue_code is not None:
        conditions.append(f"m.venue_code=${idx}")
        params.append(venue_code)
        idx += 1
    if watched is not None:
        conditions.append(f"m.watched=${idx}")
        params.append(watched)
        idx += 1
    if search is not None:
        conditions.append(f"(m.title ILIKE ${idx} OR m.venue_market_id ILIKE ${idx})")
        params.append(f"%{search}%")
        idx += 1
    if min_volume is not None:
        # Bind as Decimal so the comparison against numeric(20,2) is exact
        # (avoids any float8->numeric rounding artifact at the boundary).
        from decimal import Decimal as _Decimal
        conditions.append(f"m.volume>=${idx}")
        params.append(_Decimal(str(min_volume)))
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)

    rows = await conn.fetch(
        f"""
        SELECT m.venue_code, m.venue_market_id, m.title, m.status, m.watched,
               m.volume,
               COUNT(t.trade_id) AS trade_count,
               MAX(t.received_at) AS last_trade_at
        FROM markets m
        LEFT JOIN normalized_trades t ON t.market_id = m.market_id
        {where}
        GROUP BY m.market_id, m.venue_code, m.venue_market_id, m.title, m.status, m.watched, m.volume
        ORDER BY {order_clause}
        LIMIT ${idx}
        """,
        *params,
    )
    return [dict(r) for r in rows]


async def set_markets_watched_bulk(
    conn: asyncpg.Connection,
    *,
    venue_code: str,
    venue_market_ids: list[str],
    watched: bool,
) -> int:
    """Set watched flag on multiple markets. Returns count of affected rows."""
    if not venue_market_ids:
        return 0  # nothing to update; skip the round-trip
    result = await conn.execute(
        "UPDATE markets SET watched=$1 WHERE venue_code=$2 AND venue_market_id = ANY($3::text[])",
        watched, venue_code, venue_market_ids,
    )
    # result is like 'UPDATE 3'
    try:
        return int(result.split()[-1])
    except (IndexError, ValueError):
        return 0


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


async def upsert_market_outcome(
    conn: asyncpg.Connection,
    *,
    market_id: str,
    venue_code: str,
    venue_outcome_id: str,
    outcome_key: str,
    outcome_label: str,
    is_binary: bool = True,
    raw_metadata: dict | None = None,
) -> str:
    """Upsert a market outcome (token → outcome mapping).

    venue_outcome_id is the Polymarket token_id / asset_id.
    is_binary is True only for genuine yes/no binary markets; False for multi-outcome.
    Conflict target is (market_id, outcome_key) — venue_outcome_id for Polymarket tokens
    is always distinct per token, so binary yes/no markets are never merged.
    """
    import json as _json
    meta_json = _json.dumps(raw_metadata) if raw_metadata else None
    row = await conn.fetchrow(
        """INSERT INTO market_outcomes
               (market_id, venue_code, venue_outcome_id, outcome_key, outcome_label,
                is_active, is_binary, raw_metadata)
           VALUES ($1::uuid, $2, $3, $4, $5, true, $6, COALESCE($7::jsonb, '{}'))
           ON CONFLICT (market_id, outcome_key) DO UPDATE
               SET venue_outcome_id = EXCLUDED.venue_outcome_id,
                   venue_code       = EXCLUDED.venue_code,
                   outcome_label    = EXCLUDED.outcome_label,
                   is_active        = true,
                   is_binary        = EXCLUDED.is_binary,
                   updated_at       = now()
           RETURNING outcome_id::text""",
        market_id, venue_code, venue_outcome_id,
        outcome_key.lower(), outcome_label, is_binary, meta_json,
    )
    return str(row["outcome_id"])
