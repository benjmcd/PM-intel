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

    # Atomic dedup: race-free INSERT-first approach.
    #
    # We attempt to INSERT the dedupe_key row first (ON CONFLICT DO NOTHING).
    # The RETURNING clause tells us whether we won the race:
    #   - Row returned  → this is the FIRST sighting; proceed to insert raw_events.
    #   - No row returned → another caller already holds the key; skip and return
    #     the existing raw_event_id.
    #
    # A transaction wraps both statements so that a crash between the two writes
    # cannot leave a dedupe_key entry pointing at a missing raw_events row.
    # first_raw_event_id is initially NULL; we back-fill it after inserting the
    # raw_events row within the same transaction.
    async with conn.transaction():
        claimed = await conn.fetchrow(
            """INSERT INTO event_dedupe_keys
                   (dedupe_key, venue_code, source_channel, first_raw_event_id,
                    first_seen_at, last_seen_at, duplicate_count)
               VALUES ($1, $2, $3, NULL, now(), now(), 0)
               ON CONFLICT (dedupe_key) DO NOTHING
               RETURNING dedupe_key""",
            dedupe_key, event.venue_code, event.source_channel,
        )

        if claimed is None:
            # Duplicate: another caller already inserted this dedupe_key.
            existing = await conn.fetchrow(
                """UPDATE event_dedupe_keys
                   SET last_seen_at = now(), duplicate_count = duplicate_count + 1
                   WHERE dedupe_key = $1
                   RETURNING first_raw_event_id""",
                dedupe_key,
            )
            existing_id = existing["first_raw_event_id"] if existing else None
            return (int(existing_id) if existing_id is not None else 0), True

        # First sighting: insert the raw_events row.
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

        # Back-fill the raw_event_id reference now that we have it.
        await conn.execute(
            "UPDATE event_dedupe_keys SET first_raw_event_id = $1 WHERE dedupe_key = $2",
            raw_event_id, dedupe_key,
        )

    return raw_event_id, False


async def fetch_recent(conn: asyncpg.Connection, venue_code: str | None = None, limit: int = 100) -> list[asyncpg.Record]:
    if venue_code:
        return await conn.fetch(
            "SELECT * FROM raw_events WHERE venue_code=$1 ORDER BY received_at DESC LIMIT $2",
            venue_code, limit,
        )
    return await conn.fetch("SELECT * FROM raw_events ORDER BY received_at DESC LIMIT $1", limit)
