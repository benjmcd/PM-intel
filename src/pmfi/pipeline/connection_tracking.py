from __future__ import annotations

import logging
from typing import Any, Callable

from pmfi.db.repos.ingestion_connections import (
    finish_ingestion_connection,
    mark_ingestion_connection_message,
    start_ingestion_connection,
)

logger = logging.getLogger(__name__)


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
    ) -> str | None:
        pool = self._pool_getter()
        if pool is None:
            logger.debug("Skipping ingestion connection start because the DB pool is unavailable")
            return None
        async with pool.acquire() as conn:
            return await start_ingestion_connection(
                conn,
                venue_code=venue_code,
                source_channel=source_channel,
                reconnect_count=reconnect_count,
                metadata=metadata,
            )

    async def message(self, connection_id: object) -> None:
        pool = self._pool_getter()
        if pool is None:
            logger.debug("Skipping ingestion connection message checkpoint because the DB pool is unavailable")
            return
        async with pool.acquire() as conn:
            await mark_ingestion_connection_message(conn, connection_id)

    async def disconnected(
        self,
        connection_id: object,
        *,
        reason: str,
        classification: str,
    ) -> None:
        pool = self._pool_getter()
        if pool is None:
            logger.debug("Skipping ingestion connection finish because the DB pool is unavailable")
            return
        async with pool.acquire() as conn:
            await finish_ingestion_connection(
                conn,
                connection_id,
                reason=reason,
                classification=classification,
            )
