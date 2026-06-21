from __future__ import annotations

import json
from typing import Any

import asyncpg


async def start_ingestion_connection(
    conn: asyncpg.Connection,
    *,
    venue_code: str,
    source_channel: str,
    reconnect_count: int = 0,
    metadata: dict[str, Any] | None = None,
) -> str:
    row = await conn.fetchrow(
        """INSERT INTO ingestion_connections
             (venue_code, source_channel, status, connected_at, reconnect_count, metadata)
           VALUES ($1, $2, 'connected', now(), $3, $4::jsonb)
           RETURNING connection_id::text""",
        venue_code,
        source_channel,
        reconnect_count,
        json.dumps(metadata or {}),
    )
    return str(row["connection_id"])


async def mark_ingestion_connection_message(
    conn: asyncpg.Connection,
    connection_id: object,
) -> None:
    await conn.execute(
        """UPDATE ingestion_connections
           SET last_message_at = now(), updated_at = now()
           WHERE connection_id = $1::uuid""",
        connection_id,
    )


async def finish_ingestion_connection(
    conn: asyncpg.Connection,
    connection_id: object,
    *,
    reason: str | None = None,
    classification: str,
    status: str = "disconnected",
) -> None:
    await conn.execute(
        """UPDATE ingestion_connections
           SET status = $2,
               disconnected_at = now(),
               last_error = $3,
               metadata = metadata || $4::jsonb,
               updated_at = now()
           WHERE connection_id = $1::uuid""",
        connection_id,
        status,
        reason,
        json.dumps({"classification": classification}),
    )
