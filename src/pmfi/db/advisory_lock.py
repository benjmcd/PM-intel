from __future__ import annotations

import asyncpg


# Stable 64-bit key for the PMFI single-active ingest guard.
# Hex decodes to ASCII "PMFIDPS1" and stays below PostgreSQL bigint max.
SINGLE_ACTIVE_INGEST_LOCK_KEY = 0x504D464944505331


class SingleActiveIngestLock:
    """Dedicated session-level advisory lock for one active ingest daemon per DB."""

    def __init__(self, dsn: str, *, lock_key: int = SINGLE_ACTIVE_INGEST_LOCK_KEY) -> None:
        self._dsn = dsn
        self._lock_key = lock_key
        self._conn: asyncpg.Connection | None = None
        self._acquired = False

    @property
    def acquired(self) -> bool:
        return self._acquired

    async def acquire(self) -> bool:
        if self._conn is None or self._conn.is_closed():
            self._conn = await asyncpg.connect(
                self._dsn,
                server_settings={"search_path": "pmfi,public"},
            )
        acquired = bool(
            await self._conn.fetchval(
                "SELECT pg_try_advisory_lock($1::bigint)",
                self._lock_key,
            )
        )
        self._acquired = acquired
        if not acquired:
            await self.close()
        return acquired

    async def release(self) -> None:
        if self._conn is None or self._conn.is_closed() or not self._acquired:
            self._acquired = False
            return
        released = bool(
            await self._conn.fetchval(
                "SELECT pg_advisory_unlock($1::bigint)",
                self._lock_key,
            )
        )
        self._acquired = False if released else self._acquired

    async def close(self) -> None:
        conn = self._conn
        try:
            await self.release()
        finally:
            self._conn = None
            if conn is not None and not conn.is_closed():
                await conn.close()

    async def __aenter__(self) -> SingleActiveIngestLock:
        acquired = await self.acquire()
        if not acquired:
            raise RuntimeError(
                "another PMFI ingest daemon holds the single-active lock on this database"
            )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        await self.close()
        return False
