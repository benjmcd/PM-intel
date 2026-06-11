"""DB-gated tests for the cross-venue divergence monitor.

Seeds two markets, an active alias, and two latest snapshots; asserts an alert
fires when the price spread exceeds the threshold and not when it is small.
Cleans up every row it creates (FK-safe order). Pool max_size=2 so the test's
own connection and the monitor's internal pool.acquire() do not deadlock.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)

_SRC = "TEST-CV-SRC-001"
_TGT = "TEST-CV-TGT-001"


async def _pool():
    import asyncpg
    return await asyncpg.create_pool(
        os.environ["PMFI_DB_URL"], min_size=1, max_size=2,
        server_settings={"search_path": "pmfi,public"},
    )


async def _seed(conn, src_price: str, tgt_price: str):
    from pmfi.db.repos.markets import upsert_market
    src_id = await upsert_market(conn, venue_code="polymarket", venue_market_id=_SRC, title="CV source")
    tgt_id = await upsert_market(conn, venue_code="kalshi", venue_market_id=_TGT, title="CV target")
    await conn.execute("DELETE FROM alerts WHERE market_id IN ($1, $2)", src_id, tgt_id)
    await conn.execute("DELETE FROM market_aliases WHERE source_market_id = $1", src_id)
    await conn.execute("DELETE FROM market_snapshots WHERE market_id IN ($1, $2)", src_id, tgt_id)
    await conn.execute(
        "INSERT INTO market_aliases (source_market_id, target_market_id, alias_type, confidence, rationale) "
        "VALUES ($1::uuid, $2::uuid, 'manual_cross_venue', $3, $4)",
        src_id, tgt_id, Decimal("0.95"), "test match",
    )
    await conn.execute(
        "INSERT INTO market_snapshots (venue_code, market_id, source, last_price) "
        "VALUES ('polymarket', $1::uuid, 'test', $2)",
        src_id, Decimal(src_price),
    )
    await conn.execute(
        "INSERT INTO market_snapshots (venue_code, market_id, source, last_price) "
        "VALUES ('kalshi', $1::uuid, 'test', $2)",
        tgt_id, Decimal(tgt_price),
    )
    return src_id, tgt_id


async def _cleanup(conn, src_id, tgt_id):
    await conn.execute("DELETE FROM alerts WHERE market_id IN ($1, $2)", src_id, tgt_id)
    await conn.execute("DELETE FROM market_snapshots WHERE market_id IN ($1, $2)", src_id, tgt_id)
    await conn.execute("DELETE FROM market_aliases WHERE source_market_id = $1", src_id)
    await conn.execute("DELETE FROM markets WHERE market_id IN ($1, $2)", src_id, tgt_id)


def test_divergence_emits_alert():
    from pmfi.monitoring.cross_venue import check_cross_venue_divergence

    async def _run():
        pool = await _pool()
        try:
            async with pool.acquire() as conn:
                src_id, tgt_id = await _seed(conn, "0.60", "0.72")  # 12c spread >= 3c
                try:
                    await check_cross_venue_divergence(pool, now=datetime.now(timezone.utc))
                    count = await conn.fetchval(
                        "SELECT count(*) FROM alerts WHERE market_id = $1 "
                        "AND rule_key = 'cross_venue_divergence_v1'",
                        src_id,
                    )
                    assert count >= 1
                finally:
                    await _cleanup(conn, src_id, tgt_id)
        finally:
            await pool.close()

    asyncio.run(_run())


def test_no_divergence_no_alert():
    from pmfi.monitoring.cross_venue import check_cross_venue_divergence

    async def _run():
        pool = await _pool()
        try:
            async with pool.acquire() as conn:
                src_id, tgt_id = await _seed(conn, "0.60", "0.61")  # 1c spread < 3c
                try:
                    await check_cross_venue_divergence(pool, now=datetime.now(timezone.utc))
                    count = await conn.fetchval(
                        "SELECT count(*) FROM alerts WHERE market_id = $1 "
                        "AND rule_key = 'cross_venue_divergence_v1'",
                        src_id,
                    )
                    assert count == 0
                finally:
                    await _cleanup(conn, src_id, tgt_id)
        finally:
            await pool.close()

    asyncio.run(_run())
