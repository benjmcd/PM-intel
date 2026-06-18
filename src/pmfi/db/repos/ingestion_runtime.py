from __future__ import annotations

import json
from typing import Any


def _json_payload(metadata: dict[str, Any] | None) -> str:
    return json.dumps(metadata or {}, default=str)


async def record_connection_start(
    pool,
    *,
    venue_code: str,
    source_channel: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ingestion_connections (
                venue_code, source_channel, status, connected_at, metadata, updated_at
            )
            VALUES ($1, $2, 'connected', now(), $3::jsonb, now())
            RETURNING connection_id
            """,
            venue_code,
            source_channel,
            _json_payload(metadata),
        )
    return str(row["connection_id"])


async def record_connection_message(pool, connection_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE ingestion_connections
            SET status='connected',
                last_message_at=now(),
                updated_at=now()
            WHERE connection_id=$1::uuid
            """,
            connection_id,
        )


async def record_connection_stop(
    pool,
    connection_id: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE ingestion_connections
            SET status='stopped',
                disconnected_at=now(),
                last_error=NULL,
                metadata=metadata || $2::jsonb,
                updated_at=now()
            WHERE connection_id=$1::uuid
            """,
            connection_id,
            _json_payload(metadata),
        )


async def record_connection_error(
    pool,
    connection_id: str,
    error: BaseException | str,
    *,
    metadata: dict[str, Any] | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE ingestion_connections
            SET status='error',
                disconnected_at=now(),
                last_error=$2,
                metadata=metadata || $3::jsonb,
                updated_at=now()
            WHERE connection_id=$1::uuid
            """,
            connection_id,
            str(error),
            _json_payload(metadata),
        )


async def record_heartbeat(
    pool,
    *,
    worker_name: str,
    worker_type: str,
    status: str = "healthy",
    metadata: dict[str, Any] | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO system_heartbeats (
                worker_name, worker_type, status, last_heartbeat_at, metadata
            )
            VALUES ($1, $2, $3, now(), $4::jsonb)
            ON CONFLICT (worker_name)
            DO UPDATE SET
                worker_type=EXCLUDED.worker_type,
                status=EXCLUDED.status,
                last_heartbeat_at=now(),
                metadata=EXCLUDED.metadata
            """,
            worker_name,
            worker_type,
            status,
            _json_payload(metadata),
        )
