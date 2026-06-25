from __future__ import annotations

import asyncio
import json
import os
import uuid

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


def _get_dsn() -> str:
    if _SCRATCH_DB is None:
        raise RuntimeError("market title backfill scratch DB was not initialized")
    return _SCRATCH_DB.dsn


@pytest.fixture(scope="module", autouse=True)
def _market_title_backfill_scratch_database():
    global _SCRATCH_DB  # noqa: PLW0603
    _SCRATCH_DB = create_test_scratch_database("market_title_backfill")
    try:
        yield
    finally:
        if _SCRATCH_DB is not None:
            drop_test_scratch_database(_SCRATCH_DB)
            _SCRATCH_DB = None


def test_market_title_backfill_uses_scratch_db_not_configured_primary() -> None:
    assert _SCRATCH_DB is not None
    assert _get_dsn() != os.environ["PMFI_DB_URL"]
    assert _SCRATCH_DB.name.startswith(
        f"{TESTISO_DB_PREFIX}market_title_backfill_"
    )
    assert _SCRATCH_DB.name in _get_dsn()


def _as_json_dict(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def test_full_market_upsert_does_not_clobber_real_title_with_placeholder():
    import asyncpg
    from pmfi.db.repos.markets import upsert_market, upsert_market_full

    venue_code = "polymarket"
    venue_market_id = "TEST-TITLE-BACKFILL-" + uuid.uuid4().hex[:12]
    real_title = "Will the title survive placeholder backfill?"

    async def _run():
        conn = await asyncpg.connect(
            _get_dsn(),
            server_settings={"search_path": "pmfi,public"},
        )
        market_id = None
        try:
            market_id = await upsert_market(
                conn,
                venue_code=venue_code,
                venue_market_id=venue_market_id,
                title=venue_market_id,
            )

            await upsert_market_full(
                conn,
                venue_code=venue_code,
                venue_market_id=venue_market_id,
                title=real_title,
                raw_metadata={"source": "discovery", "tag": venue_market_id},
            )
            assert await conn.fetchval("SELECT title FROM markets WHERE market_id=$1::uuid", market_id) == real_title
            assert _as_json_dict(await conn.fetchval("SELECT raw_metadata FROM markets WHERE market_id=$1::uuid", market_id)) == {
                "source": "discovery",
                "tag": venue_market_id,
            }

            await upsert_market_full(
                conn,
                venue_code=venue_code,
                venue_market_id=venue_market_id,
                title=venue_market_id,
            )
            assert await conn.fetchval("SELECT title FROM markets WHERE market_id=$1::uuid", market_id) == real_title
            assert _as_json_dict(await conn.fetchval("SELECT raw_metadata FROM markets WHERE market_id=$1::uuid", market_id)) == {
                "source": "discovery",
                "tag": venue_market_id,
            }

            await upsert_market_full(
                conn,
                venue_code=venue_code,
                venue_market_id=venue_market_id,
                title="unknown",
            )
            assert await conn.fetchval("SELECT title FROM markets WHERE market_id=$1::uuid", market_id) == real_title
            assert _as_json_dict(await conn.fetchval("SELECT raw_metadata FROM markets WHERE market_id=$1::uuid", market_id)) == {
                "source": "discovery",
                "tag": venue_market_id,
            }
        finally:
            if market_id is not None:
                await conn.execute("DELETE FROM market_outcomes WHERE market_id=$1::uuid", market_id)
                await conn.execute("DELETE FROM markets WHERE market_id=$1::uuid", market_id)
            await conn.close()

    asyncio.run(_run())
