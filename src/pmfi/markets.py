"""Market discovery: fetch active markets from venue REST APIs and sync to DB."""
from __future__ import annotations
import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

POLYMARKET_REST_BASE = "https://clob.polymarket.com"


async def fetch_polymarket_markets(
    *,
    limit: int = 100,
    active_only: bool = True,
    min_volume: float | None = None,
) -> list[dict[str, Any]]:
    """Fetch active markets from Polymarket CLOB REST API (no auth required)."""
    params: dict[str, Any] = {"limit": min(limit, 100)}
    if active_only:
        params["active"] = "true"

    markets: list[dict[str, Any]] = []
    next_cursor: str | None = None

    async with aiohttp.ClientSession() as session:
        while len(markets) < limit:
            if next_cursor:
                params["next_cursor"] = next_cursor
            async with session.get(
                f"{POLYMARKET_REST_BASE}/markets",
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

            page_markets: list[dict] = data.get("data", [])
            for m in page_markets:
                if min_volume is not None:
                    vol = float(m.get("volume", 0) or 0)
                    if vol < min_volume:
                        continue
                markets.append(m)

            next_cursor = data.get("next_cursor")
            if not next_cursor or not page_markets:
                break

    return markets[:limit]


async def sync_polymarket_markets(pool: Any, *, limit: int = 100, min_volume: float | None = None) -> int:
    """Fetch active Polymarket markets and upsert into markets table. Returns count synced."""
    from pmfi.db.repos.markets import upsert_market_full

    raw_markets = await fetch_polymarket_markets(limit=limit, min_volume=min_volume)
    synced = 0
    async with pool.acquire() as conn:
        for m in raw_markets:
            venue_market_id = m.get("condition_id") or m.get("id", "")
            if not venue_market_id:
                continue
            title = m.get("question", venue_market_id)
            category = str(m.get("category") or m.get("market_slug") or "")

            close_ts = None
            raw_ts = m.get("end_date_iso") or m.get("end_date")
            if raw_ts:
                from datetime import datetime
                try:
                    close_ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                except Exception:
                    pass

            try:
                await upsert_market_full(
                    conn,
                    venue_code="polymarket",
                    venue_market_id=venue_market_id,
                    title=title,
                    category=category,
                    close_ts=close_ts,
                    raw_metadata=m,
                )
                synced += 1
            except Exception as exc:
                logger.warning("Failed to upsert market %s: %s", venue_market_id, exc)

    logger.info("synced %d/%d Polymarket markets to DB", synced, len(raw_markets))
    return synced
