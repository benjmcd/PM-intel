from __future__ import annotations

import asyncio
import os

import pytest

from db_scratch import (
    TESTISO_DB_PREFIX,
    ScratchDatabase,
    create_test_scratch_database,
    drop_test_scratch_database,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)

_SCRATCH_DB: ScratchDatabase | None = None


def _dsn() -> str:
    if _SCRATCH_DB is None:
        raise RuntimeError("dead-letters dedupe scratch DB was not initialized")
    return _SCRATCH_DB.dsn


@pytest.fixture(scope="module", autouse=True)
def _dead_letters_scratch_database():
    global _SCRATCH_DB  # noqa: PLW0603
    _SCRATCH_DB = create_test_scratch_database("dead_letters")
    try:
        yield
    finally:
        if _SCRATCH_DB is not None:
            drop_test_scratch_database(_SCRATCH_DB)
            _SCRATCH_DB = None


def test_dead_letters_dedupe_uses_scratch_db_not_configured_primary() -> None:
    assert _SCRATCH_DB is not None
    assert _dsn() != os.environ["PMFI_DB_URL"]
    assert _SCRATCH_DB.name.startswith(f"{TESTISO_DB_PREFIX}dead_letters_")
    assert _SCRATCH_DB.name in _dsn()


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
