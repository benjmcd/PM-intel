from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest


pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)


def _dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def test_single_active_ingest_lock_conflicts_and_releases_on_connection_close():
    from pmfi.db.advisory_lock import (
        SINGLE_ACTIVE_INGEST_LOCK_KEY,
        SingleActiveIngestLock,
    )

    async def _run():
        holder = await asyncpg.connect(_dsn(), server_settings={"search_path": "pmfi,public"})
        try:
            assert await holder.fetchval(
                "SELECT pg_try_advisory_lock($1::bigint)",
                SINGLE_ACTIVE_INGEST_LOCK_KEY,
            )

            contender = SingleActiveIngestLock(_dsn())
            try:
                assert await contender.acquire() is False
            finally:
                await contender.close()
        finally:
            await holder.close()

        fresh = SingleActiveIngestLock(_dsn())
        try:
            assert await fresh.acquire() is True
        finally:
            await fresh.close()

    asyncio.run(_run())


def test_single_active_ingest_lock_graceful_unlock_allows_fresh_acquire():
    from pmfi.db.advisory_lock import SingleActiveIngestLock

    async def _run():
        lock = SingleActiveIngestLock(_dsn())
        assert await lock.acquire() is True
        await lock.close()

        fresh = SingleActiveIngestLock(_dsn())
        try:
            assert await fresh.acquire() is True
        finally:
            await fresh.close()

    asyncio.run(_run())
