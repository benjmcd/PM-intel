"""DB-gated storage hardening tests (migration ledger + orderbook idempotency).

Skips when PMFI_DB_URL is unset — the default offline verify.py run stays green.

Tests:
  1. schema_migrations table exists and has rows after init/apply_schema_migrations.
  2. orderbook_levels insert is idempotent under the named ON CONFLICT target.

All seeded rows are self-cleaned in FK-safe order.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMFI_DB_URL"),
    reason="Requires PMFI_DB_URL env var pointing to a local Postgres instance",
)

_VENUE = "polymarket"
_SYNTH_PREFIX = "DB-HARDENING-SYNTH"


def _get_dsn() -> str:
    return os.environ["PMFI_DB_URL"]


# ---------------------------------------------------------------------------
# Test 1: migrations are recorded in schema_migrations after apply_schema_migrations
# ---------------------------------------------------------------------------

def test_schema_migrations_recorded():
    """apply_schema_migrations must insert rows into schema_migrations for known migrations."""
    import asyncpg
    from pmfi.db.migrations import apply_schema_migrations

    async def _run():
        pool = await asyncpg.create_pool(
            _get_dsn(),
            server_settings={"search_path": "pmfi,public"},
            min_size=1,
            max_size=2,
        )
        try:
            await apply_schema_migrations(pool)
            async with pool.acquire() as conn:
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM schema_migrations"
                )
                assert int(count) >= 1, (
                    "schema_migrations must contain at least one row after "
                    "apply_schema_migrations(); got 0."
                )
                # Confirm the ledger table itself is recorded.
                row = await conn.fetchrow(
                    "SELECT migration_name, checksum FROM schema_migrations "
                    "WHERE migration_name = '012_schema_migrations.sql'"
                )
                assert row is not None, (
                    "012_schema_migrations.sql must be recorded in schema_migrations"
                )
                assert row["checksum"], "checksum must be non-empty"
        finally:
            await pool.close()

    asyncio.run(_run())


def test_schema_migrations_idempotent():
    """Calling apply_schema_migrations twice must not raise and must not add duplicate rows."""
    import asyncpg
    from pmfi.db.migrations import apply_schema_migrations

    async def _run():
        pool = await asyncpg.create_pool(
            _get_dsn(),
            server_settings={"search_path": "pmfi,public"},
            min_size=1,
            max_size=2,
        )
        try:
            await apply_schema_migrations(pool)
            async with pool.acquire() as conn:
                count_before = await conn.fetchval(
                    "SELECT COUNT(*) FROM schema_migrations"
                )
            # Second call must be a no-op (ON CONFLICT DO NOTHING).
            await apply_schema_migrations(pool)
            async with pool.acquire() as conn:
                count_after = await conn.fetchval(
                    "SELECT COUNT(*) FROM schema_migrations"
                )
            assert int(count_after) == int(count_before), (
                f"Row count changed after second apply_schema_migrations call: "
                f"{count_before} -> {count_after}"
            )
        finally:
            await pool.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Test 2: orderbook_levels insert is idempotent under explicit ON CONFLICT target
# ---------------------------------------------------------------------------

def test_orderbook_levels_insert_idempotent():
    """Inserting the same orderbook level twice must not raise and must not duplicate rows."""
    import asyncpg
    from pmfi.db.repos.markets import upsert_market
    from pmfi.db.repos.orderbook import insert_orderbook_snapshot

    run_id = os.urandom(6).hex()
    synth_market_id_str = f"{_SYNTH_PREFIX}-OB-{run_id}"

    async def _run():
        conn = await asyncpg.connect(
            _get_dsn(),
            server_settings={"search_path": "pmfi,public"},
        )
        market_id = None
        snapshot_id = None
        try:
            market_id = await upsert_market(
                conn,
                venue_code=_VENUE,
                venue_market_id=synth_market_id_str,
                title=synth_market_id_str,
            )

            levels = [
                {"price": 0.55, "size": 100},
                {"price": 0.50, "size": 200},
            ]

            # First insert — should succeed.
            snapshot_id = await insert_orderbook_snapshot(
                conn,
                venue_code=_VENUE,
                market_id=market_id,
                best_bid=None,
                best_ask=None,
                bids=levels,
                asks=levels,
            )
            assert snapshot_id is not None, "insert_orderbook_snapshot must return a snapshot_id"

            # Count rows.
            count_first = await conn.fetchval(
                "SELECT COUNT(*) FROM orderbook_levels "
                "WHERE orderbook_snapshot_id = $1::uuid",
                snapshot_id,
            )

            # Manually attempt to re-insert the same rows — must be silently ignored.
            captured_at_row = await conn.fetchrow(
                "SELECT captured_at FROM orderbook_snapshots "
                "WHERE orderbook_snapshot_id = $1::uuid",
                snapshot_id,
            )
            captured_at = captured_at_row["captured_at"]

            for idx, level in enumerate(levels[:10]):
                await conn.execute(
                    """INSERT INTO orderbook_levels
                           (orderbook_snapshot_id, captured_at, market_id, outcome_key,
                            side, price, contracts, level_index)
                       VALUES ($1::uuid, $2, $3::uuid, $4, 'bid', $5, $6, $7)
                       ON CONFLICT (orderbook_snapshot_id, captured_at, outcome_key, side, level_index)
                       DO NOTHING""",
                    snapshot_id, captured_at, market_id, "yes",
                    level["price"], level["size"], idx,
                )

            count_second = await conn.fetchval(
                "SELECT COUNT(*) FROM orderbook_levels "
                "WHERE orderbook_snapshot_id = $1::uuid",
                snapshot_id,
            )

            assert int(count_second) == int(count_first), (
                f"Duplicate insert changed row count: {count_first} -> {count_second}. "
                "ON CONFLICT target must be explicit and correct."
            )

        finally:
            if snapshot_id is not None:
                await conn.execute(
                    "DELETE FROM orderbook_levels WHERE orderbook_snapshot_id = $1::uuid",
                    snapshot_id,
                )
                await conn.execute(
                    "DELETE FROM orderbook_snapshots WHERE orderbook_snapshot_id = $1::uuid",
                    snapshot_id,
                )
            if market_id is not None:
                await conn.execute(
                    "DELETE FROM markets WHERE market_id = $1::uuid", market_id
                )
            await conn.close()

    asyncio.run(_run())
