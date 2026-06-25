"""DB-gated baseline idempotency test (Fix F1).

Skips when PMFI_DB_URL is unset — the default offline verify.py run stays green.
Calls upsert_baseline twice with the same (market_id, venue_code, scope) but different
p99 values, then asserts exactly one row exists and the second call's value won.
"""
from __future__ import annotations

import asyncio
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
        raise RuntimeError("baseline idempotency scratch DB was not initialized")
    return _SCRATCH_DB.dsn


@pytest.fixture(scope="module", autouse=True)
def _baseline_idempotency_scratch_database():
    global _SCRATCH_DB  # noqa: PLW0603
    _SCRATCH_DB = create_test_scratch_database("baseline_idempotency")
    try:
        yield
    finally:
        if _SCRATCH_DB is not None:
            drop_test_scratch_database(_SCRATCH_DB)
            _SCRATCH_DB = None


def test_baseline_idempotency_uses_scratch_db_not_configured_primary() -> None:
    assert _SCRATCH_DB is not None
    assert _get_dsn() != os.environ["PMFI_DB_URL"]
    assert _SCRATCH_DB.name.startswith(
        f"{TESTISO_DB_PREFIX}baseline_idempotency_"
    )
    assert _SCRATCH_DB.name in _get_dsn()


def test_upsert_baseline_idempotent():
    """upsert_baseline called twice with same scope produces exactly one row with updated p99."""
    import asyncpg
    from pmfi.db.repos.baselines import upsert_baseline

    synthetic_venue_market_id = "test-baseline-idem-" + uuid.uuid4().hex[:12]

    async def _run():
        conn = await asyncpg.connect(_get_dsn())
        market_id = None
        try:
            # Insert a synthetic market to satisfy the FK constraint
            row = await conn.fetchrow(
                """
                INSERT INTO pmfi.markets (venue_code, venue_market_id, title, status)
                VALUES ('kalshi', $1, 'Idempotency test market', 'active')
                RETURNING market_id::text
                """,
                synthetic_venue_market_id,
            )
            market_id = row["market_id"]

            # First upsert — p99 = 100.00
            id1 = await upsert_baseline(
                conn,
                market_id=market_id,
                venue_code="kalshi",
                scope="market",
                lookback_seconds=3600,
                sample_size=50,
                p50_trade_usd=10.0,
                p95_trade_usd=80.0,
                p99_trade_usd=100.0,
                p995_trade_usd=120.0,
                median_5m_flow_usd=500.0,
                p99_5m_flow_usd=2000.0,
                baseline_payload={"call": 1},
            )
            assert id1, "first upsert_baseline returned empty string"

            # Second upsert — same (market_id, venue_code, scope), different p99 = 999.00
            id2 = await upsert_baseline(
                conn,
                market_id=market_id,
                venue_code="kalshi",
                scope="market",
                lookback_seconds=3600,
                sample_size=75,
                p50_trade_usd=15.0,
                p95_trade_usd=90.0,
                p99_trade_usd=999.0,
                p995_trade_usd=1100.0,
                median_5m_flow_usd=600.0,
                p99_5m_flow_usd=2500.0,
                baseline_payload={"call": 2},
            )
            assert id2, "second upsert_baseline returned empty string"

            # (a) Exactly one row for this (market_id, venue_code, scope)
            count = await conn.fetchval(
                """
                SELECT count(*) FROM pmfi.market_baselines
                WHERE market_id = $1 AND venue_code = 'kalshi' AND scope = 'market'
                """,
                market_id,
            )
            assert count == 1, (
                f"Expected exactly 1 baseline row, got {count} — "
                "ON CONFLICT upsert did not replace duplicate"
            )

            # (b) The stored p99 is the second call's value (update-in-place worked)
            p99 = await conn.fetchval(
                """
                SELECT p99_trade_usd FROM pmfi.market_baselines
                WHERE market_id = $1 AND venue_code = 'kalshi' AND scope = 'market'
                """,
                market_id,
            )
            assert float(p99) == 999.0, (
                f"Expected p99_trade_usd=999.0 (second call), got {p99!r}"
            )

        finally:
            if market_id:
                await conn.execute(
                    "DELETE FROM pmfi.market_baselines WHERE market_id = $1", market_id
                )
                await conn.execute(
                    "DELETE FROM pmfi.markets WHERE market_id = $1::uuid", market_id
                )
            await conn.close()

    asyncio.run(_run())
