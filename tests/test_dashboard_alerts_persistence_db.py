from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)


def _dsn() -> str:
    return os.environ["PMFI_DB_URL"]


def test_persistence_health_reports_recent_normalized_trade():
    import asyncpg
    from pmfi.dashboard.queries import persistence_health

    tag = uuid4().hex[:10]
    venue_market_id = f"DASH-PERSIST-TEST-{tag}"
    trade_id = str(uuid4())

    async def _run():
        conn = await asyncpg.connect(_dsn())
        market_id = None
        try:
            market_id = await conn.fetchval(
                """INSERT INTO markets (venue_code, venue_market_id, title, status)
                   VALUES ('polymarket', $1, $2, 'active')
                   ON CONFLICT (venue_code, venue_market_id) DO UPDATE SET last_seen_at=now()
                   RETURNING market_id::text""",
                venue_market_id,
                f"Dash persist test market {tag}",
            )
            await conn.execute(
                """INSERT INTO normalized_trades
                   (trade_id, venue_code, market_id, outcome_key, price, contracts,
                    capital_at_risk_usd, received_at, processed_at, normalization_version,
                    source_payload)
                   VALUES ($1::uuid, 'polymarket', $2::uuid, 'yes', 0.6, 50.0,
                           123.45, now(), now(), 'trade.v1', '{}'::jsonb)""",
                trade_id,
                market_id,
            )

            result = await persistence_health(conn)
            assert "venues" in result
            assert "unresolved_dead_letters_1h" in result
            polymarket = next(
                (row for row in result["venues"] if row["venue_code"] == "polymarket"),
                None,
            )
            assert polymarket is not None
            assert polymarket["last_persisted_age_s"] is not None
            assert polymarket["last_persisted_age_s"] < 120
            assert polymarket["trades_5m"] >= 1
            assert polymarket["trades_1h"] >= 1
            assert isinstance(polymarket["last_persisted_at"], str)
        finally:
            await conn.execute("DELETE FROM normalized_trades WHERE trade_id=$1::uuid", trade_id)
            if market_id:
                await conn.execute("DELETE FROM markets WHERE market_id=$1::uuid", market_id)
            await conn.close()

    asyncio.run(_run())


def test_persistence_health_reports_stale_venue_with_age():
    import asyncpg
    from pmfi.dashboard.queries import persistence_health

    tag = uuid4().hex[:10]
    venue_code = f"persist-test-{tag}"
    venue_market_id = f"DASH-PERSIST-STALE-{tag}"
    trade_id = str(uuid4())

    async def _run():
        conn = await asyncpg.connect(_dsn())
        market_id = None
        try:
            await conn.execute(
                """INSERT INTO venues (venue_code, display_name, base_url)
                   VALUES ($1, $2, 'http://localhost')
                   ON CONFLICT (venue_code) DO NOTHING""",
                venue_code,
                f"Persist test {tag}",
            )
            market_id = await conn.fetchval(
                """INSERT INTO markets (venue_code, venue_market_id, title, status)
                   VALUES ($1, $2, $3, 'active')
                   RETURNING market_id::text""",
                venue_code,
                venue_market_id,
                f"Dash stale persist test market {tag}",
            )
            await conn.execute(
                """INSERT INTO normalized_trades
                   (trade_id, venue_code, market_id, outcome_key, price, contracts,
                    capital_at_risk_usd, received_at, processed_at, normalization_version,
                    source_payload)
                   VALUES ($1::uuid, $2, $3::uuid, 'yes', 0.6, 50.0,
                           123.45, now() - interval '3 hours', now(), 'trade.v1',
                           '{}'::jsonb)""",
                trade_id,
                venue_code,
                market_id,
            )

            result = await persistence_health(conn)
            stale = next((row for row in result["venues"] if row["venue_code"] == venue_code), None)
            assert stale is not None
            assert stale["last_persisted_age_s"] >= 3 * 60 * 60
            assert stale["trades_5m"] == 0
            assert stale["trades_1h"] == 0
        finally:
            await conn.execute("DELETE FROM normalized_trades WHERE trade_id=$1::uuid", trade_id)
            if market_id:
                await conn.execute("DELETE FROM markets WHERE market_id=$1::uuid", market_id)
            await conn.execute("DELETE FROM venues WHERE venue_code=$1", venue_code)
            await conn.close()

    asyncio.run(_run())


def test_persistence_health_reports_venue_without_trades():
    import asyncpg
    from pmfi.dashboard.queries import persistence_health

    tag = uuid4().hex[:10]
    venue_code = f"persist-empty-{tag}"

    async def _run():
        conn = await asyncpg.connect(_dsn())
        try:
            await conn.execute(
                """INSERT INTO venues (venue_code, display_name, base_url)
                   VALUES ($1, $2, 'http://localhost')
                   ON CONFLICT (venue_code) DO NOTHING""",
                venue_code,
                f"Persist empty test {tag}",
            )

            result = await persistence_health(conn)
            empty = next((row for row in result["venues"] if row["venue_code"] == venue_code), None)
            assert empty is not None
            assert empty["last_persisted_at"] is None
            assert empty["last_persisted_age_s"] is None
            assert empty["trades_5m"] == 0
            assert empty["trades_1h"] == 0
        finally:
            await conn.execute("DELETE FROM venues WHERE venue_code=$1", venue_code)
            await conn.close()

    asyncio.run(_run())
