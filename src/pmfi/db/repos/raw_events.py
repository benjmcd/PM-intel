from __future__ import annotations
import json
from datetime import timezone
from typing import Any
import asyncpg
from pmfi.domain import RawEvent

async def insert_raw_event(conn: asyncpg.Connection, event: RawEvent) -> int:
    received_at = event.received_at
    if received_at.tzinfo is None:
        from datetime import timezone as _tz
        received_at = received_at.replace(tzinfo=_tz.utc)
    row = await conn.fetchrow(
        """INSERT INTO raw_events
           (venue_code, source_channel, source_event_type, source_event_id,
            venue_market_id, exchange_ts, received_at, payload, parser_version)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9)
           RETURNING raw_event_id""",
        event.venue_code, event.source_channel, event.source_event_type,
        event.source_event_id, event.venue_market_id, event.exchange_ts,
        received_at, json.dumps(event.payload), "raw.v1",
    )
    return int(row["raw_event_id"])

async def fetch_recent(conn: asyncpg.Connection, venue_code: str | None = None, limit: int = 100) -> list[asyncpg.Record]:
    if venue_code:
        return await conn.fetch(
            "SELECT * FROM raw_events WHERE venue_code=$1 ORDER BY received_at DESC LIMIT $2",
            venue_code, limit,
        )
    return await conn.fetch("SELECT * FROM raw_events ORDER BY received_at DESC LIMIT $1", limit)
