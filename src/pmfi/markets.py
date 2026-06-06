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
    from pmfi.db.repos.markets import upsert_market_full, upsert_market_outcome

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
                market_id = await upsert_market_full(
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
                continue

            # Populate market_outcomes with token IDs (asset_id → outcome mapping)
            tokens = m.get("tokens") or []
            for token in tokens:
                token_id = token.get("token_id") or token.get("asset_id")
                outcome_label = str(token.get("outcome") or "Unknown")
                outcome_key = outcome_label.lower()
                if outcome_key not in ("yes", "no"):
                    outcome_key = "yes" if "yes" in outcome_label.lower() else "no"
                if token_id:
                    try:
                        await upsert_market_outcome(
                            conn,
                            market_id=market_id,
                            venue_code="polymarket",
                            venue_outcome_id=str(token_id),
                            outcome_key=outcome_key,
                            outcome_label=outcome_label,
                            raw_metadata=token,
                        )
                    except Exception as exc:
                        logger.warning("Failed to upsert market_outcome %s: %s", token_id, exc)

    logger.info("synced %d/%d Polymarket markets to DB", synced, len(raw_markets))
    return synced


KALSHI_REST_BASE = "https://api.elections.kalshi.com/trade-api/v2"


async def fetch_kalshi_markets(
    *,
    limit: int = 100,
    status: str = "open",
    min_volume: float | None = None,
) -> list[dict[str, Any]]:
    """Fetch markets from Kalshi REST API (no auth required for public data)."""
    params: dict[str, Any] = {"limit": min(limit, 200), "status": status}
    markets: list[dict] = []
    cursor: str | None = None

    async with aiohttp.ClientSession() as session:
        while len(markets) < limit:
            if cursor:
                params["cursor"] = cursor
            async with session.get(
                f"{KALSHI_REST_BASE}/markets",
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

            page_markets: list[dict] = data.get("markets", [])
            for m in page_markets:
                if min_volume is not None:
                    vol = float(m.get("volume", 0) or 0)
                    if vol < min_volume:
                        continue
                markets.append(m)

            cursor = data.get("cursor")
            if not cursor or not page_markets:
                break

    return markets[:limit]


async def sync_kalshi_markets(pool: Any, *, limit: int = 100, min_volume: float | None = None) -> int:
    """Fetch active Kalshi markets and upsert into markets table. Returns count synced."""
    from pmfi.db.repos.markets import upsert_market_full, upsert_market_outcome
    from datetime import datetime as _dt

    raw_markets = await fetch_kalshi_markets(limit=limit, min_volume=min_volume)
    synced = 0
    async with pool.acquire() as conn:
        for m in raw_markets:
            venue_market_id = m.get("ticker", "")
            if not venue_market_id:
                continue
            title = m.get("title", venue_market_id)
            category = str(m.get("event_ticker") or m.get("category") or "")

            close_ts = None
            raw_ts = m.get("close_time") or m.get("end_date_iso") or m.get("end_date")
            if raw_ts:
                try:
                    close_ts = _dt.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
                except Exception:
                    pass

            try:
                market_id = await upsert_market_full(
                    conn,
                    venue_code="kalshi",
                    venue_market_id=venue_market_id,
                    title=title,
                    category=category,
                    close_ts=close_ts,
                    raw_metadata=m,
                )
                synced += 1
            except Exception as exc:
                logger.warning("Failed to upsert Kalshi market %s: %s", venue_market_id, exc)
                continue

            for outcome_key, outcome_label in [("yes", "Yes"), ("no", "No")]:
                try:
                    await upsert_market_outcome(
                        conn,
                        market_id=market_id,
                        venue_code="kalshi",
                        venue_outcome_id=f"{venue_market_id}_{outcome_key}",
                        outcome_key=outcome_key,
                        outcome_label=outcome_label,
                        raw_metadata={"ticker": venue_market_id},
                    )
                except Exception as exc:
                    logger.warning("Failed to upsert Kalshi outcome %s/%s: %s", venue_market_id, outcome_key, exc)

    logger.info("synced %d/%d Kalshi markets to DB", synced, len(raw_markets))
    return synced


async def fetch_kalshi_trades(
    ticker: str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch recent trades for a Kalshi market from REST API (no auth required).

    Returns raw trade dicts as returned by the Kalshi API.
    Endpoint: GET /markets/{ticker}/trades
    """
    params: dict[str, Any] = {"limit": min(limit, 200)}
    trades: list[dict] = []
    cursor: str | None = None

    async with aiohttp.ClientSession() as session:
        while len(trades) < limit:
            if cursor:
                params["cursor"] = cursor
            async with session.get(
                f"{KALSHI_REST_BASE}/markets/{ticker}/trades",
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

            page_trades: list[dict] = data.get("trades", [])
            trades.extend(page_trades)

            cursor = data.get("cursor")
            if not cursor or not page_trades:
                break

    return trades[:limit]


def kalshi_trade_to_raw_event(trade: dict[str, Any], ticker: str) -> "RawEvent":
    """Convert a Kalshi REST trade dict to a RawEvent for replay/normalization.

    The payload is stored verbatim; ticker is added if not already present.
    exchange_ts is extracted from 'created_time' field.
    """
    from pmfi.domain import RawEvent
    from pmfi.normalization import parse_ts

    payload = dict(trade)
    if "ticker" not in payload and "market_ticker" not in payload:
        payload["ticker"] = ticker

    exchange_ts = parse_ts(trade.get("created_time") or trade.get("ts"))
    trade_id = str(trade.get("trade_id")) if trade.get("trade_id") else None

    return RawEvent(
        venue_code="kalshi",
        source_channel="rest_trades",
        source_event_type="trade",
        source_event_id=trade_id,
        venue_market_id=ticker,
        exchange_ts=exchange_ts,
        payload=payload,
    )


async def load_asset_id_mapping(pool) -> dict[str, dict]:
    """Load asset_id (token_id) → {market_id, venue_market_id, outcome_key, outcome_label} mapping.

    Returns dict keyed by token_id/asset_id for fast lookup during normalization.
    Used to resolve Polymarket last_trade_price events that include asset_id.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT mo.venue_outcome_id, mo.outcome_key, mo.outcome_label,
                      m.market_id::text, m.venue_market_id, m.venue_code
               FROM market_outcomes mo
               JOIN markets m ON m.market_id = mo.market_id
               WHERE mo.venue_code = 'polymarket' AND mo.venue_outcome_id IS NOT NULL"""
        )
        return {
            row["venue_outcome_id"]: {
                "market_id": row["market_id"],
                "venue_market_id": row["venue_market_id"],
                "venue_code": row["venue_code"],
                "outcome_key": row["outcome_key"],
                "outcome_label": row["outcome_label"],
            }
            for row in rows
        }
