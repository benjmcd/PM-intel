from __future__ import annotations
import hashlib
import json
from datetime import timezone
from typing import Any
import asyncpg
from pmfi.domain import RawEvent


def _compute_payload_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _compute_dedupe_key(venue_code: str, source_channel: str, source_event_id: str | None, payload_hash: str) -> str:
    if source_event_id:
        raw = f"{venue_code}:{source_channel}:{source_event_id}"
    else:
        raw = f"{venue_code}:{source_channel}:{payload_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def insert_raw_event(conn: asyncpg.Connection, event: RawEvent) -> tuple[int, bool]:
    """Insert a raw event, deduplicating via event_dedupe_keys.

    Returns (raw_event_id, is_duplicate).
    is_duplicate=True means the payload was already ingested; the returned
    raw_event_id is the original one. Callers should skip downstream processing.
    """
    received_at = event.received_at
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)

    payload_hash = _compute_payload_hash(event.payload)
    dedupe_key = _compute_dedupe_key(
        event.venue_code, event.source_channel, event.source_event_id, payload_hash
    )

    # Check dedup BEFORE inserting raw_events to avoid accumulating duplicate rows.
    existing = await conn.fetchrow(
        "SELECT first_raw_event_id FROM event_dedupe_keys WHERE dedupe_key = $1",
        dedupe_key,
    )
    if existing is not None:
        await conn.execute(
            """UPDATE event_dedupe_keys
               SET last_seen_at = now(), duplicate_count = duplicate_count + 1
               WHERE dedupe_key = $1""",
            dedupe_key,
        )
        return int(existing["first_raw_event_id"]), True

    row = await conn.fetchrow(
        """INSERT INTO raw_events
           (venue_code, source_channel, source_event_type, source_event_id,
            venue_market_id, exchange_ts, received_at, payload, payload_hash, parser_version)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10)
           RETURNING raw_event_id""",
        event.venue_code, event.source_channel, event.source_event_type,
        event.source_event_id, event.venue_market_id, event.exchange_ts,
        received_at, json.dumps(event.payload), payload_hash, "raw.v1",
    )
    raw_event_id = int(row["raw_event_id"])

    await conn.execute(
        """INSERT INTO event_dedupe_keys
               (dedupe_key, venue_code, source_channel, first_raw_event_id,
                first_seen_at, last_seen_at, duplicate_count)
           VALUES ($1, $2, $3, $4, now(), now(), 0)
           ON CONFLICT (dedupe_key) DO NOTHING""",
        dedupe_key, event.venue_code, event.source_channel, raw_event_id,
    )

    return raw_event_id, False


async def fetch_recent(conn: asyncpg.Connection, venue_code: str | None = None, limit: int = 100) -> list[asyncpg.Record]:
    if venue_code:
        return await conn.fetch(
            "SELECT * FROM raw_events WHERE venue_code=$1 ORDER BY received_at DESC LIMIT $2",
            venue_code, limit,
        )
    return await conn.fetch("SELECT * FROM raw_events ORDER BY received_at DESC LIMIT $1", limit)
