from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)


def _dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def test_dead_letters_insert_is_idempotent_for_same_raw_stage_class() -> None:
    from pmfi.db import create_pool
    from pmfi.db.repos.dead_letters import insert_dead_letter

    raw_event_id = 700000000000000101
    source_channel = "dead-letter-dedupe-guard-test"

    async def _run() -> None:
        pool = await create_pool(_dsn())
        try:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM dead_letters WHERE raw_event_id = $1", raw_event_id)

            async def write(message: str) -> None:
                async with pool.acquire() as conn:
                    await insert_dead_letter(
                        conn,
                        venue_code=None,
                        raw_event_id=raw_event_id,
                        source_channel=source_channel,
                        failure_stage="pipeline_write",
                        error_class="pipeline_write_failed",
                        error_message=message,
                        payload={"source": source_channel},
                    )

            await asyncio.gather(write("first"), write("second"))

            async with pool.acquire() as conn:
                count = int(
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM dead_letters WHERE raw_event_id = $1",
                        raw_event_id,
                    )
                    or 0
                )
            assert count == 1
        finally:
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM dead_letters WHERE raw_event_id = $1", raw_event_id)
            await pool.close()

    asyncio.run(_run())
