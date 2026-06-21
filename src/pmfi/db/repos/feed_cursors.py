from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import asyncpg

from pmfi.db.repos.markets import upsert_market
from pmfi.domain import RawEvent

KALSHI_REST_TRADE_FEED = "rest_trades"


def _canonical_cursor_value(cursor_value: str) -> str:
    value = str(cursor_value).strip()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


async def load_kalshi_rest_trade_cursors(
    conn: asyncpg.Connection,
    tickers: list[str],
) -> dict[str, str]:
    if not tickers:
        return {}
    rows = await conn.fetch(
        """SELECT m.venue_market_id, fc.cursor_value
           FROM feed_cursors fc
           JOIN markets m ON m.market_id = fc.market_id
           WHERE fc.venue_code = 'kalshi'
             AND fc.feed_name = $1
             AND m.venue_market_id = ANY($2::text[])
             AND fc.cursor_value IS NOT NULL""",
        KALSHI_REST_TRADE_FEED,
        tickers,
    )
    return {
        str(row["venue_market_id"]): str(row["cursor_value"])
        for row in rows
        if row["cursor_value"]
    }


async def upsert_kalshi_rest_trade_cursor(
    conn: asyncpg.Connection,
    *,
    ticker: str,
    cursor_value: str,
    source_event_id: str | None = None,
) -> None:
    cursor_value = _canonical_cursor_value(cursor_value)
    market_id = await upsert_market(
        conn,
        venue_code="kalshi",
        venue_market_id=ticker,
        title=None,
    )
    payload = json.dumps({"source_event_id": source_event_id, "ticker": ticker})
    await conn.execute(
        """INSERT INTO feed_cursors
             (venue_code, feed_name, market_id, cursor_value, cursor_payload, last_success_at)
           VALUES ('kalshi', $1, $2::uuid, $3, $4::jsonb, now())
           ON CONFLICT (venue_code, feed_name, market_id)
           DO UPDATE SET
             cursor_value = CASE
               WHEN feed_cursors.cursor_value IS NULL
                 OR EXCLUDED.cursor_value::timestamptz >= feed_cursors.cursor_value::timestamptz
               THEN EXCLUDED.cursor_value
               ELSE feed_cursors.cursor_value
             END,
             cursor_payload = CASE
               WHEN feed_cursors.cursor_value IS NULL
                 OR EXCLUDED.cursor_value::timestamptz >= feed_cursors.cursor_value::timestamptz
               THEN EXCLUDED.cursor_payload
               ELSE feed_cursors.cursor_payload
             END,
             last_success_at = CASE
               WHEN feed_cursors.cursor_value IS NULL
                 OR EXCLUDED.cursor_value::timestamptz >= feed_cursors.cursor_value::timestamptz
               THEN now()
               ELSE feed_cursors.last_success_at
             END,
             updated_at = now()""",
        KALSHI_REST_TRADE_FEED,
        market_id,
        cursor_value,
        payload,
    )


def _cursor_from_raw(raw: RawEvent) -> tuple[str, str] | None:
    if raw.venue_code != "kalshi" or raw.source_channel != KALSHI_REST_TRADE_FEED:
        return None
    if raw.source_event_type != "trade":
        return None
    ticker = str(
        raw.payload.get("ticker")
        or raw.payload.get("market_ticker")
        or raw.venue_market_id
        or ""
    )
    if not ticker:
        return None
    cursor_value = raw.payload.get("created_time")
    if cursor_value is None and raw.exchange_ts is not None:
        cursor_value = raw.exchange_ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if cursor_value is None or cursor_value == "":
        return None
    return ticker, _canonical_cursor_value(str(cursor_value))


async def record_kalshi_rest_trade_cursor(pool: Any, raw: RawEvent) -> None:
    cursor = _cursor_from_raw(raw)
    if cursor is None:
        return
    ticker, cursor_value = cursor
    async with pool.acquire() as conn:
        await upsert_kalshi_rest_trade_cursor(
            conn,
            ticker=ticker,
            cursor_value=cursor_value,
            source_event_id=raw.source_event_id,
        )
