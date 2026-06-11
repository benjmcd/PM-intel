"""DB-gated tests for the market_aliases repo layer (cross-venue link)."""
from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)

_SRC = "TEST-ALIAS-SRC-001"
_TGT = "TEST-ALIAS-TGT-001"


async def _pool():
    import asyncpg
    return await asyncpg.create_pool(
        os.environ["PMFI_DB_URL"], min_size=1, max_size=1,
        server_settings={"search_path": "pmfi,public"},
    )


async def _seed(conn):
    from pmfi.db.repos.markets import upsert_market
    src_id = await upsert_market(conn, venue_code="polymarket", venue_market_id=_SRC, title="alias source")
    tgt_id = await upsert_market(conn, venue_code="kalshi", venue_market_id=_TGT, title="alias target")
    await conn.execute("DELETE FROM market_aliases WHERE source_market_id = $1", src_id)
    return src_id, tgt_id


async def _cleanup(conn, src_id, tgt_id):
    await conn.execute("DELETE FROM market_aliases WHERE source_market_id = $1", src_id)
    await conn.execute("DELETE FROM markets WHERE market_id IN ($1, $2)", src_id, tgt_id)


def test_link_and_list_aliases():
    from pmfi.db.repos.aliases import link_markets, list_aliases

    async def _run():
        pool = await _pool()
        try:
            async with pool.acquire() as conn:
                src_id, tgt_id = await _seed(conn)
                try:
                    alias_id = await link_markets(
                        conn,
                        source_venue="polymarket", source_market=_SRC,
                        target_venue="kalshi", target_market=_TGT,
                        confidence=0.9, rationale="same event", reviewed_by="tester",
                    )
                    assert alias_id
                    rows = await list_aliases(conn, limit=50)
                    assert any(r["alias_id"] == alias_id for r in rows)
                finally:
                    await _cleanup(conn, src_id, tgt_id)
        finally:
            await pool.close()

    asyncio.run(_run())


def test_link_rejects_bad_inputs():
    from pmfi.db.repos.aliases import link_markets

    async def _run():
        pool = await _pool()
        try:
            async with pool.acquire() as conn:
                src_id, tgt_id = await _seed(conn)
                try:
                    with pytest.raises(ValueError):
                        await link_markets(
                            conn, source_venue="polymarket", source_market=_SRC,
                            target_venue="kalshi", target_market=_TGT,
                            confidence=1.5, rationale="bad confidence",
                        )
                    with pytest.raises(ValueError):
                        await link_markets(
                            conn, source_venue="polymarket", source_market="NO-SUCH-MARKET",
                            target_venue="kalshi", target_market=_TGT,
                            confidence=0.9, rationale="missing source",
                        )
                finally:
                    await _cleanup(conn, src_id, tgt_id)
        finally:
            await pool.close()

    asyncio.run(_run())
