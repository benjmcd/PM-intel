from __future__ import annotations

import asyncio
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)


def _get_dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def test_full_market_upsert_does_not_clobber_real_title_with_placeholder():
    import asyncpg
    from pmfi.db.repos.markets import upsert_market, upsert_market_full

    venue_code = "polymarket"
    venue_market_id = "TEST-TITLE-BACKFILL-" + uuid.uuid4().hex[:12]
    real_title = "Will the title survive placeholder backfill?"

    async def _run():
        conn = await asyncpg.connect(_get_dsn())
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
            )
            assert await conn.fetchval("SELECT title FROM markets WHERE market_id=$1::uuid", market_id) == real_title

            await upsert_market_full(
                conn,
                venue_code=venue_code,
                venue_market_id=venue_market_id,
                title=venue_market_id,
            )
            assert await conn.fetchval("SELECT title FROM markets WHERE market_id=$1::uuid", market_id) == real_title

            await upsert_market_full(
                conn,
                venue_code=venue_code,
                venue_market_id=venue_market_id,
                title="unknown",
            )
            assert await conn.fetchval("SELECT title FROM markets WHERE market_id=$1::uuid", market_id) == real_title
        finally:
            if market_id is not None:
                await conn.execute("DELETE FROM market_outcomes WHERE market_id=$1::uuid", market_id)
                await conn.execute("DELETE FROM markets WHERE market_id=$1::uuid", market_id)
            await conn.close()

    asyncio.run(_run())
