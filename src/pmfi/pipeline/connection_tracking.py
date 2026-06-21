from __future__ import annotations

from typing import Any, Callable

from pmfi.db.repos.ingestion_connections import (
    finish_ingestion_connection,
    mark_ingestion_connection_message,
    start_ingestion_connection,
)


class PooledIngestionConnectionRecorder:
    def __init__(self, pool_getter: Callable[[], Any]) -> None:
        self._pool_getter = pool_getter

    async def connected(
        self,
        *,
        venue_code: str,
        source_channel: str,
        reconnect_count: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        async with self._pool_getter().acquire() as conn:
            return await start_ingestion_connection(
                conn,
                venue_code=venue_code,
                source_channel=source_channel,
                reconnect_count=reconnect_count,
                metadata=metadata,
            )

    async def message(self, connection_id: object) -> None:
        async with self._pool_getter().acquire() as conn:
            await mark_ingestion_connection_message(conn, connection_id)

    async def disconnected(
        self,
        connection_id: object,
        *,
        reason: str,
        classification: str,
    ) -> None:
        async with self._pool_getter().acquire() as conn:
            await finish_ingestion_connection(
                conn,
                connection_id,
                reason=reason,
                classification=classification,
            )
